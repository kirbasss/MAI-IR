from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import mongomock

from src.corpus_repair import repair_saved_documents
from src.crawler import Crawler
from src.downloader import _response_class
from src.parsers import PARSERS
from src.url_policy import is_listing_url
from tests.python.test_crawler import FakeResponse, FakeSession, HTML_1, URL, config, responses


class ResponseClassificationTests(unittest.TestCase):
    def test_captcha_interstitial_variants(self):
        challenges = [
            "<title>Just a moment...</title><p>Wait</p>",
            "<title>CAPTCHA</title><p>Please continue</p>",
            "<title>Подтвердите, что вы не робот</title>",
            "<title>Security</title><main>Verify you are human</main>" + "<!-- padding -->" * 1500,
            "<title>Game news</title><form id='challenge-form'></form>",
            "<title>Check</title><div id='px-captcha'></div>",
            "<title>Check</title><div class='h-captcha'></div>",
            "<main>Проверка браузера. Подождите.</main>",
        ]
        for html in challenges:
            for status in (200, 403, 404, 503):
                with self.subTest(html=html[:60], status=status):
                    self.assertEqual(_response_class(status, html), "anti_bot_or_challenge")

    def test_article_mentions_and_shared_captcha_scripts_are_not_blocks(self):
        articles = [
            "<title>CAPTCHA in games explained</title><article id='material_content'>"
            "<p>Verify you are human is an example of a message.</p></article>",
            "<h1>Story</h1><div id='article-body'>A game with captcha puzzles.</div>"
            "<div class='g-recaptcha'></div>",
            "<title>News</title><script>captcha(); var message='checking your browser';</script>"
            "<h1>Actual news</h1><p>New game released.</p>",
            "<title>News</title><script>var c='/cdn-cgi/challenge-platform/';</script><p>Actual news.</p>",
            "<h1>Story</h1><article class='news-details'><div class='content-row__content'>"
            "A game with captcha puzzles.</div></article><div class='g-recaptcha'></div>",
            "<h1>Review</h1><article class='reviews-details'><div class='content-row__section'>"
            "Review text.</div></article><div class='h-captcha'></div>",
        ]
        for html in articles:
            with self.subTest(html=html):
                self.assertEqual(_response_class(200, html), "valid_content_page")

    def test_empty_success_and_real_http_errors(self):
        self.assertEqual(_response_class(200, " \n\t"), "empty_response")
        self.assertEqual(_response_class(404, ""), "not_found")
        self.assertEqual(_response_class(502, ""), "server_error")
        self.assertEqual(_response_class(429, ""), "rate_limited")


class UrlPolicyTests(unittest.TestCase):
    def test_observed_non_publications_and_paginated_categories(self):
        urls = {
            "pcgamer": ["/author/dave-james/page/6/", "/tag/epic-games/page/7/",
                        "/news/archive/2025/03/", "/archive/2018/08/", "/games/mmo/page/4/",
                        "/games/rpg/dragon-age/page/8/", "/gaming-industry/page/2/",
                        "/hardware/controllers/game-pads/page/5/", "/software/platforms/page/8/"],
            "eurogamer": ["/authors/tom-orry", "/games/neverhood"],
            "stopgame": ["/games/compilation/6102"],
            "igromania": ["/"],
        }
        for source, paths in urls.items():
            for path in paths:
                with self.subTest(source=source, path=path):
                    self.assertTrue(is_listing_url(source, "https://example.org" + path))

    def test_articles_and_absent_final_url_not_mistaken_for_listings(self):
        urls = {
            "pcgamer": ["/games/action/control-resonant-review/", "/games/rpg/dragon-age-review/",
                        "/hardware/controllers/best-controller-review/", "/games/you-can-now-leap/",
                        "/a-long-story/page/2/"],
            "stopgame": ["/show/123/game-compilation", "/blogs/topic/123/example"],
            "eurogamer": ["/a-review"],
        }
        for source, paths in urls.items():
            self.assertFalse(is_listing_url(source, ""))
            for path in paths:
                with self.subTest(source=source, path=path):
                    self.assertFalse(is_listing_url(source, "https://example.org" + path))


class ParserQualityTests(unittest.TestCase):
    URL = "https://ixbt.games/news/example.html"

    def payload(self, **overrides):
        data = {"url": "/news/example.html", "component": "Publication/Show", "props": {
            "publication": {"blocks": [{"html": "<p>First <b>article</b> paragraph.</p>"},
                                         {"html": "<p>Second paragraph.</p>"}]},
            "homePublications": [{"blocks": [{"html": "Sidebar text must not leak."}]}],
        }, **overrides}
        return ("<html><h1>Article</h1><script type='application/json'>"
                + json.dumps(data) + "</script></html>").encode()

    def test_ixbt_embedded_publication_json_is_scoped_to_current_article(self):
        parsed = PARSERS["ixbt_games"](self.payload(), self.URL)
        self.assertIsNone(parsed.parse_error)
        self.assertIn("First article paragraph.", parsed.text)
        self.assertIn("Second paragraph.", parsed.text)
        self.assertNotIn("Sidebar", parsed.text)
        self.assertEqual(parsed.metadata["body_selector"], "props.publication.blocks[].html")

    def test_ixbt_malformed_unrelated_and_other_page_payloads_ignored(self):
        for raw in (self.payload(url="/news/different.html"), self.payload(props={}),
                    b"<h1>News</h1><script type='application/json'>{bad json</script>"):
            with self.subTest(raw=raw[:100]):
                self.assertIsNotNone(PARSERS["ixbt_games"](raw, self.URL).parse_error)

    def test_ixbt_html_body_remains_preferred(self):
        html = self.payload().replace(b"</html>",
            b"<div id='publication-1' class='prose'><p>Visible article.</p></div></html>")
        parsed = PARSERS["ixbt_games"](html, self.URL)
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.text, "Visible article.")

    def test_legacy_object_video_is_title_only_not_parser_failure(self):
        parsed = PARSERS["gamemag"](
            b"<h1>Old trailer</h1><div class='content-text'><object><param name='movie' value='v.swf'></object></div>",
            "https://gamemag.ru/news/123/trailer",
        )
        self.assertIsNone(parsed.parse_error)
        self.assertEqual(parsed.text, "")
        self.assertEqual(parsed.metadata["content_kind"], "media_only")


class CrawlerQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "urls.tsv"
        self.path.write_text(f"stopgame\t{URL}\n", encoding="utf-8")
        self.db = mongomock.MongoClient()["test"]

    def run_worker(self, session, mode="normal"):
        worker = Crawler(config(self.path), self.db, session=session, now=lambda: 1000)
        with redirect_stdout(io.StringIO()):
            return worker.run(mode=mode)

    def test_captcha_is_retried_before_parser_and_pauses_source(self):
        for status in (200, 403, 404, 503):
            with self.subTest(status=status):
                self.db.frontier.delete_many({})
                self.db.crawler_meta.delete_many({})
                parser = Mock(side_effect=AssertionError("parser called"))
                with patch.dict("src.crawler.PARSERS", {"stopgame": parser}):
                    result = self.run_worker(responses(b"<title>Check</title><form id='challenge-form'></form>", status=status))
                parser.assert_not_called()
                self.assertEqual(result["retry"], 1)
                self.assertEqual(self.db.frontier.find_one({"_id": URL})["last_error"], "anti_bot_or_challenge")
                self.assertEqual(self.db.crawler_meta.find_one({"_id": "cooldown:stopgame"})["until"], 1010)
                self.assertEqual(self.db.raw_pages.count_documents({}), 0)
                self.assertEqual(self.db.documents.count_documents({}), 0)

    def test_empty_200_not_saved_and_has_specific_retry_reason(self):
        result = self.run_worker(responses(b"  \n "))
        self.assertEqual(result["retry"], 1)
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["last_error"], "empty_response")
        self.assertEqual(self.db.documents.count_documents({}), 0)

    def test_challenge_on_revisit_never_overwrites_previous_article(self):
        self.run_worker(responses())
        before_raw = self.db.raw_pages.find_one({"_id": URL})
        before_doc = self.db.documents.find_one({"_id": URL})
        self.db.frontier.update_one({"_id": URL}, {"$set": {"available_at": 0}})
        result = self.run_worker(responses(b"<title>Just a moment</title>"), mode="revisit")
        self.assertEqual(result["retry"], 1)
        self.assertEqual(before_raw, self.db.raw_pages.find_one({"_id": URL}))
        self.assertEqual(before_doc, self.db.documents.find_one({"_id": URL}))
        self.assertTrue(self.db.frontier.find_one({"_id": URL})["revisit_job"])

    def test_server_502_honors_retry_after_and_pauses_source(self):
        result = self.run_worker(responses(status=502, headers={"Retry-After": "60"}))
        self.assertEqual(result["retry"], 1)
        self.assertEqual(self.db.crawler_meta.find_one({"_id": "cooldown:stopgame"})["until"], 1060)


class RepairQualityTests(unittest.TestCase):
    def test_bad_saved_response_requeued_without_deletion_and_repair_is_idempotent(self):
        for html, reason in (("", "empty_response"), ("<title>CAPTCHA</title>", "anti_bot_or_challenge")):
            with self.subTest(reason=reason):
                db = mongomock.MongoClient()["test"]
                digest = hashlib.sha256(html.encode()).hexdigest()
                db.frontier.insert_one({"_id": URL, "url": URL, "source": "stopgame",
                                        "state": "done", "etag": "old", "revisit_job": True})
                db.documents.insert_one({"_id": URL, "url": URL, "source": "stopgame",
                                         "raw_page_id": URL, "content_sha256": digest,
                                         "indexable": False, "parsed": {"parse_error": "missing body"}})
                db.raw_pages.insert_one({"_id": URL, "html": html, "content_sha256": digest})
                self.assertEqual(repair_saved_documents(db)["documents_requeued"], 1)
                self.assertEqual(db.frontier.find_one({"_id": URL})["state"], "done")
                self.assertEqual(repair_saved_documents(db, apply=True)["documents_requeued"], 1)
                task = db.frontier.find_one({"_id": URL})
                self.assertEqual((task["state"], task["last_error"]), ("retry", reason))
                self.assertNotIn("etag", task)
                self.assertNotIn("revisit_job", task)
                self.assertEqual(db.raw_pages.find_one({"_id": URL})["html"], html)
                self.assertEqual(repair_saved_documents(db)["documents_requeued"], 0)


if __name__ == "__main__":
    unittest.main()
