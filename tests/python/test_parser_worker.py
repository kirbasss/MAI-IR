from __future__ import annotations

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import mongomock
from pymongo.errors import DocumentTooLarge

from src.crawler import Crawler
from src.parser_worker import ParserWorker
from src.parsers import PARSERS
from tests.python.test_crawler import HTML_1, HTML_2, URL, config, responses


class ParserWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "urls.tsv"
        self.path.write_text(f"stopgame\t{URL}\n", encoding="utf-8")
        self.db = mongomock.MongoClient()["test"]
        self.clock = [1000]
        self.cfg = replace(config(self.path), parse_mode="deferred")

    def download(self, session=None, *, mode="normal"):
        worker = Crawler(self.cfg, self.db, session=session or responses(), now=lambda: self.clock[0])
        with redirect_stdout(io.StringIO()): return worker.run(mode=mode)

    def parser(self):
        return ParserWorker(self.cfg, self.db, now=lambda: self.clock[0])

    def parse(self, **kwargs):
        with redirect_stdout(io.StringIO()): return self.parser().run(**kwargs)

    def test_download_only_then_parse_without_any_http(self):
        self.assertEqual(self.download()["changed"], 1)
        raw = self.db.raw_pages.find_one({"_id": URL})
        self.assertEqual(raw["parse_state"], "pending")
        self.assertIsNone(self.db.documents.find_one({"_id": URL}))
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["state"], "done")
        with patch("requests.sessions.Session.request", side_effect=AssertionError("parser used HTTP")):
            self.assertEqual(self.parse()["done"], 1)
        doc = self.db.documents.find_one({"_id": URL})
        self.assertEqual(doc["parsed"]["text"], "First text.")
        self.assertEqual(doc["raw_generation"], raw["raw_generation"])
        self.assertTrue(doc["indexable"])
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "done")
        self.assertEqual(self.parse()["done"], 0)

    def test_304_is_valid_before_parsing_and_does_not_drop_job(self):
        self.download(responses(headers={"ETag": '"v1"'}))
        before = self.db.raw_pages.find_one({"_id": URL})
        self.clock[0] = 1010
        session = responses(status=304)
        self.assertEqual(self.download(session, mode="revisit")["unchanged"], 1)
        self.assertEqual(session.calls[1][1]["If-None-Match"], '"v1"')
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(self.parse()["done"], 1)

    def test_unchanged_200_retains_generation_and_parse_state(self):
        self.download(); self.parse()
        before = self.db.raw_pages.find_one({"_id": URL})
        self.clock[0] = 1010
        self.assertEqual(self.download(mode="revisit")["unchanged"], 1)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)

    def test_crash_after_raw_write_cannot_lose_parse_job(self):
        worker = Crawler(self.cfg, self.db, session=responses(), now=lambda: self.clock[0])
        with redirect_stdout(io.StringIO()), patch.object(worker, "_finish", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"): worker.run()
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["state"], "in_progress")
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.download()["unchanged"], 1)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "done")

    def test_parser_restart_recovers_job_without_touching_frontier(self):
        self.download()
        worker = self.parser(); worker.prepare(); worker.claim()
        before = self.db.frontier.find_one({"_id": URL})
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.frontier.find_one({"_id": URL}), before)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_attempts"], 2)

    def test_failure_after_document_write_is_safe_to_repeat(self):
        self.download()
        worker = self.parser(); worker.prepare(); job = worker.claim()
        original = worker.raw.update_one
        def fail_finish(query, change, *args, **kwargs):
            if change.get("$set", {}).get("parse_state") == "done": raise RuntimeError("crash before ack")
            return original(query, change, *args, **kwargs)
        with patch.object(worker.raw, "update_one", side_effect=fail_finish):
            with self.assertRaisesRegex(RuntimeError, "crash before ack"): worker.process(job)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "publishing")
        self.assertIsNotNone(self.db.documents.find_one({"_id": URL}))
        self.assertEqual(self.parse()["done"], 1)

    def test_parser_error_does_not_refetch_or_retry_http_task(self):
        self.download(); before = self.db.frontier.find_one({"_id": URL})
        with patch.dict(PARSERS, {"stopgame": lambda *args: (_ for _ in ()).throw(ValueError("bad selector"))}):
            self.assertEqual(self.parse()["error"], 1)
        self.assertIn("bad selector", self.db.raw_pages.find_one({"_id": URL})["parse_error"])
        self.assertFalse(self.db.documents.find_one({"_id": URL})["indexable"])
        self.assertEqual(self.db.frontier.find_one({"_id": URL}), before)
        self.assertEqual(self.parse()["error"], 0)
        self.assertEqual(self.parse(retry_errors=True)["done"], 1)

    def test_unicode_html_is_not_redecoded_using_legacy_meta_charset(self):
        html = '<meta charset="windows-1251"><h1>Игра</h1><article id="material_content"><p>Привет, игроки!</p></article>'
        self.download(responses(html.encode("utf-8")))
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["parsed"]["text"], "Привет, игроки!")

    def test_new_html_during_parse_discards_old_job(self):
        self.download()
        worker = self.parser(); worker.prepare(); job = worker.claim()
        fresh = copy.deepcopy(job)
        fresh.update({"html": HTML_2.decode(), "raw_generation": "new", "parse_state": "pending", "parse_available_at": 0,
                      "content_sha256": "new-hash", "fetched_at": 1010})
        fresh.pop("parse_lease_token", None)
        original = PARSERS["stopgame"]
        def change_html(html, url):
            self.db.raw_pages.replace_one({"_id": URL}, fresh)
            return original(html, url)
        with patch.dict(PARSERS, {"stopgame": change_html}):
            self.assertEqual(worker.process(job), "superseded")
        self.assertIsNone(self.db.documents.find_one({"_id": URL}))
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["raw_generation"], "new")

    def test_new_download_during_publish_keeps_pending_new_generation(self):
        self.download()
        worker = self.parser(); worker.prepare(); job = worker.claim()
        fresh = copy.deepcopy(job)
        fresh.update({"html": HTML_2.decode(), "raw_generation": "new", "parse_state": "pending", "parse_available_at": 0,
                      "content_sha256": "new-hash", "fetched_at": 1010})
        fresh.pop("parse_lease_token", None)
        insert = worker.documents.insert_one
        def publish(document):
            self.db.raw_pages.replace_one({"_id": URL}, fresh)
            return insert(document)
        with patch.object(worker.documents, "insert_one", side_effect=publish):
            self.assertEqual(worker.process(job), "superseded")
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "pending")
        self.assertEqual(self.db.documents.find_one({"_id": URL})["raw_generation"], job["raw_generation"])
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["parsed"]["text"], "Changed text.")

    def test_other_published_document_is_not_overwritten(self):
        self.download(); self.parse()
        with redirect_stdout(io.StringIO()): self.parser().queue_saved()
        worker = self.parser(); job = worker.claim()
        original = PARSERS["stopgame"]
        def publish_other(html, url):
            self.db.documents.update_one({"_id": URL}, {"$set": {"raw_generation": "other"}})
            return original(html, url)
        with patch.dict(PARSERS, {"stopgame": publish_other}):
            self.assertEqual(worker.process(job), "superseded")
        self.assertEqual(self.db.documents.find_one({"_id": URL})["raw_generation"], "other")
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "error")

    def test_explicit_reparse_handles_legacy_raw_without_migration(self):
        self.download(); self.parse()
        self.db.raw_pages.update_one({"_id": URL}, {"$unset": {"parse_state": "", "raw_generation": ""}})
        self.assertEqual(self.parse()["done"], 0)
        self.assertEqual(self.parse(reparse=True)["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["parsed"]["text"], "First text.")

    def test_large_parse_output_is_explicit_error_without_deleting_html(self):
        self.download()
        worker = self.parser(); worker.prepare(); job = worker.claim()
        insert = worker.documents.insert_one
        calls = []
        def size_limited_insert(document):
            calls.append(document)
            if len(calls) == 1:
                raise DocumentTooLarge("large")
            return insert(document)
        with patch.object(worker.documents, "insert_one", side_effect=size_limited_insert):
            self.assertEqual(worker.process(job), "error")
        self.assertIn("предел", self.db.raw_pages.find_one({"_id": URL})["parse_error"])
        self.assertFalse(self.db.documents.find_one({"_id": URL})["indexable"])
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["html"], HTML_1.decode())

    def test_saved_challenge_is_not_indexed_on_explicit_reparse(self):
        self.download()
        self.db.raw_pages.update_one({"_id": URL}, {"$set": {"html": "<title>Just a moment</title>"}})
        self.assertEqual(self.parse()["error"], 1)
        self.assertFalse(self.db.documents.find_one({"_id": URL})["indexable"])

    def test_source_filter_queues_and_claims_only_selected_errors(self):
        self.download()
        self.db.raw_pages.update_one({"_id": URL}, {"$set": {"parse_state": "error"}})
        other_url = "https://gamemag.ru/news/1/example"
        raw = self.db.raw_pages.find_one({"_id": URL})
        raw.update({"_id": other_url, "url": other_url, "source": "gamemag",
                    "html": '<h1>Story</h1><article class="news-details"><div class="content-row__content">Body.</div></article>'})
        self.db.raw_pages.insert_one(raw)
        before = self.db.raw_pages.find_one({"_id": URL})
        self.assertEqual(self.parse(retry_errors=True, sources=["gamemag"])["done"], 1)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertIsNone(self.db.documents.find_one({"_id": URL}))
        self.assertEqual(self.db.documents.find_one({"_id": other_url})["parsed"]["text"], "Body.")

    def test_source_filter_does_not_recover_another_sources_lease(self):
        self.download()
        worker = self.parser(); worker.prepare(); worker.claim()
        before = self.db.raw_pages.find_one({"_id": URL})
        self.assertEqual(self.parse(sources=["gamemag"]), {"done": 0, "error": 0, "superseded": 0})
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)

    def test_source_filter_reparse_preserves_other_completed_documents(self):
        self.download(); self.parse()
        before = self.db.raw_pages.find_one({"_id": URL})
        document = self.db.documents.find_one({"_id": URL})
        self.assertEqual(self.parse(reparse=True, sources=["gamemag"])["done"], 0)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(self.db.documents.find_one({"_id": URL}), document)

    def test_invalid_source_filter_fails_before_any_mutations(self):
        self.download(); before = self.db.raw_pages.find_one({"_id": URL})
        for sources in ([], ["unknown"], "gamemag"):
            with self.subTest(sources=sources), self.assertRaises(ValueError): self.parse(sources=sources)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)


if __name__ == "__main__":
    unittest.main()
