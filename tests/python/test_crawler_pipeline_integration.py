"""Opt-in real MongoDB checks, exclusively in newly created disposable databases."""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from pymongo import MongoClient

from src.crawler import Crawler
from src.parser_worker import ParserWorker
from src.disk_guard import DiskSpaceLow
from src.raw_archive import choose_auto_batch, export_raw, evict_archive, read_archived_raw, sample_restore
from tests.python.test_crawler import HTML_2, URL, config, responses
from tests.python.test_parallel_crawler import ConcurrentSession, GAME_URL, PC_URL


@unittest.skipUnless(os.environ.get("MAI_IR_TEST_MONGO_URI"), "set MAI_IR_TEST_MONGO_URI for isolated MongoDB checks")
class MongoPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.client = MongoClient(os.environ["MAI_IR_TEST_MONGO_URI"], serverSelectionTimeoutMS=3000)
        self.addCleanup(self.client.close)
        self.client.admin.command("ping")
        self.name = "mai_ir_pipeline_test_" + uuid.uuid4().hex
        self.db = self.client[self.name]
        self.addCleanup(self.drop_private_database)
        self.path = Path(self.temp.name) / "urls.tsv"
        self.path.write_text(f"stopgame\t{URL}\n", encoding="utf-8")
        self.settings = replace(config(self.path), db_name=self.name, download_workers=2, parse_mode="deferred")
        self.clock = [1000]

    def drop_private_database(self):
        # Never accept a config/environment-provided target for deletion.
        if not re.fullmatch(r"mai_ir_pipeline_test_[0-9a-f]{32}", self.name):
            raise AssertionError("Unsafe test database name")
        self.client.drop_database(self.name)

    def run_crawler(self, session, **kwargs):
        with redirect_stdout(io.StringIO()):
            return Crawler(self.settings, self.db, session=session, now=lambda: self.clock[0]).run(**kwargs)

    def parse(self):
        with redirect_stdout(io.StringIO()):
            return ParserWorker(self.settings, self.db, now=lambda: self.clock[0]).run()

    def test_download_304_before_parse_and_changed_version(self):
        self.assertEqual(self.run_crawler(responses(headers={"ETag": '"v1"'}))["changed"], 1)
        generation = self.db.raw_pages.find_one({"_id": URL})["raw_generation"]
        self.assertEqual(self.db.documents.count_documents({}), 0)
        self.clock[0] = 1010
        self.assertEqual(self.run_crawler(responses(status=304), mode="revisit")["unchanged"], 1)
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["raw_generation"], generation)
        self.clock[0] = 1020
        self.assertEqual(self.run_crawler(responses(HTML_2), mode="revisit")["changed"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["raw_generation"], generation)
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["parsed"]["text"], "Changed text.")
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "done")

    def test_parallel_claims_indexes_and_limit_on_real_mongo(self):
        self.path.write_text(f"stopgame\t{URL}\ngamemag\t{GAME_URL}\npcgamer\t{PC_URL}\n", encoding="utf-8")
        self.settings = replace(self.settings, max_documents=2)
        self.assertEqual(self.run_crawler(ConcurrentSession())["changed"], 2)
        self.assertEqual(self.db.raw_pages.count_documents({"parse_state": "pending"}), 2)
        self.assertEqual(self.db.frontier.count_documents({"state": "pending"}), 1)
        self.assertEqual(self.db.frontier.count_documents({"state": "in_progress"}), 0)

    def test_host_reserve_blocks_start_before_any_database_writes(self):
        self.settings = replace(self.settings, disk_watch_paths=(Path(self.temp.name),), min_free_disk_gib=1_000_000)
        with self.assertRaises(DiskSpaceLow):
            self.run_crawler(ConcurrentSession())
        self.assertEqual(self.db.list_collection_names(), [])

    def test_real_mongo_filesystem_probe_stops_parser_without_changing_job(self):
        self.run_crawler(responses())
        before = self.db.raw_pages.find_one({"_id": URL})
        self.settings = replace(self.settings, check_mongo_disk=True, min_free_disk_gib=1_000_000)
        with self.assertRaises(DiskSpaceLow): self.parse()
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(self.db.documents.count_documents({}), 0)

    def test_source_scoped_retry_keeps_other_sources_and_frontier_intact(self):
        self.path.write_text(f"stopgame\t{URL}\ngamemag\t{GAME_URL}\n", encoding="utf-8")
        self.settings = replace(self.settings, max_documents=2)
        self.assertEqual(self.run_crawler(ConcurrentSession())["changed"], 2)
        html = '<h1>Story</h1><article class="news-details"><div class="content-row__content">Body.</div></article>'
        self.db.raw_pages.update_one({"_id": GAME_URL}, {"$set": {
            "html": html, "content_sha256": hashlib.sha256(html.encode()).hexdigest(),
        }})
        self.db.raw_pages.update_many({}, {"$set": {"parse_state": "error", "parse_error": "old selector"}})
        before = self.db.raw_pages.find_one({"_id": URL})
        frontier = list(self.db.frontier.find().sort("_id", 1))
        with redirect_stdout(io.StringIO()):
            result = ParserWorker(self.settings, self.db).run(retry_errors=True, sources=["gamemag"])
        self.assertEqual(result, {"done": 1, "error": 0, "superseded": 0})
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(list(self.db.frontier.find().sort("_id", 1)), frontier)
        self.assertIsNone(self.db.documents.find_one({"_id": URL}))
        self.assertTrue(self.db.documents.find_one({"_id": GAME_URL})["indexable"])

    def test_archive_real_bulk_write_roundtrip_reparse_and_changed_download(self):
        self.run_crawler(responses()); self.parse()
        before = self.db.raw_pages.find_one({"_id": URL})
        document = self.db.documents.find_one({"_id": URL})
        frontier = self.db.frontier.find_one({"_id": URL})
        quiet = lambda *args, **kwargs: None
        directory, existing = choose_auto_batch(self.db, Path(self.temp.name), progress=quiet)
        self.assertFalse(existing)
        self.assertEqual(export_raw(self.db, directory, minimum_gib=0, progress=quiet)["records"], 1)
        self.assertEqual(choose_auto_batch(self.db, Path(self.temp.name), progress=quiet), (directory, True))
        self.assertTrue(sample_restore(self.db, directory)["ok"])
        result = evict_archive(self.db, directory, minimum_gib=0, progress=quiet)
        self.assertEqual(result["evicted"], 1)
        self.assertEqual(choose_auto_batch(self.db, Path(self.temp.name), progress=quiet), (None, False))
        stub = self.db.raw_pages.find_one({"_id": URL})
        self.assertEqual(read_archived_raw(stub), before)
        self.assertNotIn("html", stub)
        self.assertEqual(self.db.documents.find_one({"_id": URL}), document)
        self.assertEqual(self.db.frontier.find_one({"_id": URL}), frontier)
        self.assertEqual(evict_archive(self.db, directory, minimum_gib=0, progress=quiet)["already_archived"], 1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ParserWorker(self.settings, self.db).run(reparse=True)["done"], 1)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "archived")
        directory.rename(directory.with_name("offline"))
        self.clock[0] = 1010
        self.assertEqual(self.run_crawler(responses(status=304), mode="revisit")["unchanged"], 1)
        self.clock[0] = 1020
        self.assertEqual(self.run_crawler(responses(HTML_2), mode="revisit")["changed"], 1)
        self.assertIn("html", self.db.raw_pages.find_one({"_id": URL}))
        self.assertEqual(self.parse()["done"], 1)


if __name__ == "__main__":
    unittest.main()
