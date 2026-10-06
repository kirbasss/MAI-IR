from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mongomock
import requests

from src.crawler import Crawler, CrawlerConfig, inventory_rows, load_config


URL = "https://stopgame.ru/newsdata/1/example"
HTML_1 = b"<html><h1>Example</h1><article id='material_content'><p>First text.</p></article></html>"
HTML_2 = b"<html><h1>Example</h1><article id='material_content'><p>Changed text.</p></article></html>"


class FakeResponse:
    def __init__(self, url: str, status: int, body: bytes = b"", headers=None):
        self.url = url
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8")
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("headers") or {}))
        if not self.responses:
            raise AssertionError(f"Неожиданный запрос: {url}")
        response = self.responses.pop(0)
        if response.url != url:
            raise AssertionError(f"Ожидался {response.url}, получен {url}")
        return response


def config(inventory: Path, *, max_documents: int = 1) -> CrawlerConfig:
    return CrawlerConfig(
        db_uri="mongodb://localhost:27017", db_name="test", inventory=inventory,
        delay_seconds=0, revisit_seconds=10, retry_seconds=10,
        lease_seconds=300, request_timeout_seconds=30, seed_batch_size=2,
        max_documents=max_documents, run_forever=False, poll_seconds=1,
    )


def responses(body=HTML_1, *, status=200, headers=None):
    return FakeSession([
        FakeResponse("https://stopgame.ru/robots.txt", 200, b"User-agent: *\nAllow: /"),
        FakeResponse(URL, status, body, headers),
    ])


class CrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.inventory = Path(self.temp.name) / "urls.tsv"
        self.inventory.write_text(
            "# examples\nstopgame\thttps://stopgame.ru/newsdata/1/example?utm_source=x\n",
            encoding="utf-8",
        )
        self.db = mongomock.MongoClient()["test"]
        self.clock = [1000]

    def crawler(self, session, *, max_documents=1):
        return Crawler(config(self.inventory, max_documents=max_documents), self.db,
                       session=session, now=lambda: self.clock[0])

    def test_config_and_streamed_inventory(self):
        path = Path(self.temp.name) / "crawler.yaml"
        path.write_text(
            "db:\n  uri: mongodb://127.0.0.1:27017\n  name: test\n"
            "logic:\n  inventory: urls.tsv\n  delay_seconds: 0\n  max_documents: 1\n",
            encoding="utf-8",
        )
        loaded = load_config(path)
        self.assertEqual(loaded.inventory, self.inventory)
        self.assertEqual(list(inventory_rows(loaded.inventory)), [("stopgame", URL, None)])
        self.inventory.write_text(
            "source\tcategory\turl\tsitemap_url\n"
            f"stopgame\tnews\t{URL}\thttps://stopgame.ru/sitemap.xml\n",
            encoding="utf-8",
        )
        self.assertEqual(list(inventory_rows(self.inventory)), [("stopgame", URL, "news")])

    def test_partial_inventory_import_is_idempotent(self):
        crawler = self.crawler(FakeSession([]))
        crawler.prepare()
        self.assertEqual(crawler.seed(), 1)
        self.db.crawler_meta.delete_one({"_id": "inventory"})
        self.assertEqual(crawler.seed(), 0)
        self.assertEqual(self.db.frontier.count_documents({}), 1)

    def test_resume_and_change_detection(self):
        first = self.crawler(responses(headers={"ETag": '"v1"'}))
        self.assertEqual(first.run(), {"changed": 1, "unchanged": 0, "retry": 0})
        document = self.db.documents.find_one({"_id": URL})
        self.assertEqual(document["html"], HTML_1.decode("utf-8"))
        self.assertEqual(document["source"], "stopgame")
        self.assertEqual(document["fetched_at"], 1000)
        self.assertEqual(self.db.frontier.count_documents({}), 1)

        # Restarting immediately does not re-fetch completed work or re-seed it.
        idle = self.crawler(FakeSession([]))
        self.assertEqual(idle.run(), {"changed": 0, "unchanged": 0, "retry": 0})

        self.clock[0] = 1010
        unchanged_session = responses(status=304)
        self.assertEqual(self.crawler(unchanged_session).run()["unchanged"], 1)
        self.assertEqual(unchanged_session.calls[1][1]["If-None-Match"], '"v1"')
        self.assertEqual(self.db.documents.find_one({"_id": URL})["fetched_at"], 1000)

        self.clock[0] = 1020
        self.assertEqual(self.crawler(responses(HTML_1, headers={"ETag": '"v1"'})).run()["unchanged"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["fetched_at"], 1000)

        self.clock[0] = 1030
        self.assertEqual(self.crawler(responses(HTML_2, headers={"ETag": '"v2"'})).run()["changed"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["fetched_at"], 1030)
        self.assertIn("Changed text.", self.db.documents.find_one({"_id": URL})["html"])
        self.assertEqual(self.db.frontier.count_documents({}), 1)

    def test_interrupted_url_is_reclaimed_first_on_restart(self):
        self.inventory.write_text(
            f"stopgame\t{URL}\n"
            "stopgame\thttps://stopgame.ru/newsdata/2/another\n",
            encoding="utf-8",
        )
        worker = self.crawler(responses())
        worker.prepare()
        self.assertEqual(worker.seed(), 2)
        claimed = worker.claim(1000)
        self.assertEqual(claimed["state"], "in_progress")
        self.assertEqual(claimed["url"], URL)
        self.clock[0] = 1001
        restarted = self.crawler(responses())
        self.assertEqual(restarted.run()["changed"], 1)
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["attempts"], 2)
        self.assertEqual(self.db.frontier.find_one({"_id": "https://stopgame.ru/newsdata/2/another"})["attempts"], 0)

    def test_retry_after_and_retry_state(self):
        session = responses(status=429, headers={"Retry-After": "120"})
        self.assertEqual(self.crawler(session).run()["retry"], 1)
        item = self.db.frontier.find_one({"_id": URL})
        self.assertEqual(item["available_at"], 1120)
        self.assertEqual(item["state"], "retry")
        self.assertEqual(self.db.documents.count_documents({}), 0)

    def test_robots_disallow_prevents_document_request(self):
        session = FakeSession([
            FakeResponse("https://stopgame.ru/robots.txt", 200, b"User-agent: *\nDisallow: /"),
        ])
        self.assertEqual(self.crawler(session).run()["retry"], 1)
        self.assertEqual([url for url, _ in session.calls], ["https://stopgame.ru/robots.txt"])
        self.assertEqual(self.db.documents.count_documents({}), 0)

    def test_robots_clean_param_captcha_is_valid_rules(self):
        session = FakeSession([
            FakeResponse(
                "https://stopgame.ru/robots.txt", 200,
                b"User-agent: *\nDisallow: /search/\n"
                b"Clean-param: __cf_chl_captcha_tk__\n",
                {"Content-Type": "text/plain; charset=utf-8"},
            ),
            FakeResponse(URL, 200, HTML_1),
        ])
        self.assertEqual(self.crawler(session).run()["changed"], 1)
        self.assertEqual(self.db.documents.count_documents({}), 1)

    def test_robots_html_challenge_is_rejected(self):
        session = FakeSession([
            FakeResponse(
                "https://stopgame.ru/robots.txt", 200,
                b"<!doctype html><html><title>Just a moment</title></html>",
                {"Content-Type": "text/html"},
            ),
        ])
        self.assertEqual(self.crawler(session).run()["retry"], 1)
        self.assertEqual(len(session.calls), 1)
        self.assertIn("HTML вместо правил", self.db.frontier.find_one({"_id": URL})["last_error"])


if __name__ == "__main__":
    unittest.main()
