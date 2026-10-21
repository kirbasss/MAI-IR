from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import mongomock
import yaml

from src.crawler import Crawler, HttpGate, RobotsDenied, load_config
from tests.test_crawler import FakeResponse, FakeSession, HTML_1, URL, config


ROBOTS = "https://stopgame.ru/robots.txt"
ROOT = "https://stopgame.ru/sitemap.xml"
LEAF = "https://stopgame.ru/sitemap/news_1.xml"
NEW_URL = "https://stopgame.ru/newsdata/2/new"
ALLOW = b"User-agent: *\nAllow: /\n"


def sitemap(*urls, index=False):
    root, entry = ("sitemapindex", "sitemap") if index else ("urlset", "url")
    return (f"<{root}>" + "".join(f"<{entry}><loc>{url}</loc></{entry}>" for url in urls)
            + f"</{root}>").encode()


def maps(*urls, rules=ALLOW):
    return [FakeResponse(ROBOTS, 200, rules),
            FakeResponse(ROOT, 200, sitemap(LEAF, index=True)),
            FakeResponse(LEAF, 200, sitemap(*urls))]


class FakeClock:
    def __init__(self):
        self.value = 1000.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class CrawlerDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = self.root / "inventory" / "url_inventory.tsv"
        self.settings = replace(config(self.inventory), refresh_sitemaps=True,
                                sitemap_sources=("stopgame",), discovery_data_dir=self.root / "data")
        self.db = mongomock.MongoClient()["test"]
        self.clock = FakeClock()
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def worker(self, session, **overrides):
        worker = Crawler(replace(self.settings, **overrides), self.db, session=session, now=self.clock.now)
        worker.gate.clock = self.clock.now
        worker.gate.sleep = self.clock.sleep
        return worker

    def test_first_start_discovers_and_queues_before_downloading(self):
        session = FakeSession(maps(URL, NEW_URL) + [FakeResponse(URL, 200, HTML_1)])
        worker = self.worker(session)
        # Stable selection of the first task, independent of its shuffle hash.
        original_claim = worker.claim

        def claim(now, **kwargs):
            self.assertEqual(self.db.frontier.count_documents({}), 2)
            self.db.frontier.update_one({"_id": URL}, {"$set": {"queue_order": 0}})
            self.db.frontier.update_one({"_id": NEW_URL}, {"$set": {"queue_order": 1}})
            return original_claim(now, **kwargs)

        with patch.object(worker, "claim", side_effect=claim):
            self.assertEqual(worker.run()["changed"], 1)
        self.assertEqual([url for url, _ in session.calls], [ROBOTS, ROOT, LEAF, URL])
        self.assertTrue(self.inventory.is_file())
        self.assertEqual(self.db.documents.count_documents({}), 1)
        self.assertEqual(self.db.crawler_meta.find_one({"_id": "sitemap_refresh"})["queue_urls_added"], 2)
        self.assertTrue((self.root / "data/discovery/stopgame/robots.txt").is_file())

    def test_repeat_start_checks_root_but_skips_fresh_leaf(self):
        self.worker(FakeSession(maps(URL)), sitemap_cache_ttl_seconds=3600).update_frontier()
        session = FakeSession(maps(URL)[:2])
        worker = self.worker(session, sitemap_cache_ttl_seconds=3600)
        self.assertEqual(worker.update_frontier(), 0)
        self.assertEqual([url for url, _ in session.calls], [ROBOTS, ROOT])
        self.assertEqual(worker.sitemap_cache.stats["cache_hits"], 1)

    def test_cached_leaf_still_obeys_new_robots_rules(self):
        self.worker(FakeSession(maps(URL)), sitemap_cache_ttl_seconds=3600).update_frontier()
        before = self.inventory.read_bytes()
        session = FakeSession(maps(URL, rules=b"User-agent: *\nDisallow: /sitemap/\n")[:2])
        worker = self.worker(session, sitemap_cache_ttl_seconds=3600)
        self.assertEqual(worker.update_frontier(), 0)
        self.assertEqual(self.inventory.read_bytes(), before)
        self.assertEqual(worker.sitemap_cache.stats["cache_hits"], 0)
        self.assertEqual(self.db.crawler_meta.find_one({"_id": "sitemap_refresh"})["status"], "failed")

    def test_refresh_preserves_old_states_and_adds_only_new_urls(self):
        self.worker(FakeSession(maps(URL))).update_frontier()
        self.db.frontier.update_one({"_id": URL}, {"$set": {
            "state": "done", "attempts": 7, "available_at": 5000, "etag": "saved",
        }})
        # The old URL has disappeared from the website's sitemap, but must remain.
        session = FakeSession(maps(NEW_URL))
        worker = self.worker(session)
        self.assertEqual(worker.update_frontier(), 1)
        old = self.db.frontier.find_one({"_id": URL})
        self.assertEqual((old["state"], old["attempts"], old["available_at"], old["etag"]),
                         ("done", 7, 5000, "saved"))
        before = self.inventory.stat().st_mtime_ns
        worker.session = worker.gate.session = FakeSession(maps(NEW_URL))
        self.assertEqual(worker.update_frontier(), 0)
        self.assertEqual(self.inventory.stat().st_mtime_ns, before)
        self.assertEqual(worker.session.calls[0][0], ROBOTS)  # Rules refreshed each cycle.
        self.assertEqual(self.db.frontier.count_documents({}), 2)

    def test_bad_xml_keeps_old_inventory_and_allows_download(self):
        self.worker(FakeSession(maps(URL))).update_frontier()
        before = self.inventory.read_bytes()
        session = FakeSession([FakeResponse(ROBOTS, 200, ALLOW),
                               FakeResponse(ROOT, 200, b"not XML"), FakeResponse(URL, 200, HTML_1)])
        self.assertEqual(self.worker(session).run()["changed"], 1)
        self.assertEqual(self.inventory.read_bytes(), before)
        self.assertEqual(self.db.crawler_meta.find_one({"_id": "sitemap_refresh"})["status"], "failed")

    def test_sitemap_503_keeps_inventory_and_pauses_source(self):
        self.worker(FakeSession(maps(URL))).update_frontier()
        before = self.inventory.read_bytes()
        session = FakeSession([FakeResponse(ROBOTS, 200, ALLOW), FakeResponse(ROOT, 503)])
        self.assertEqual(self.worker(session).run()["changed"], 0)
        self.assertEqual(self.inventory.read_bytes(), before)
        self.assertEqual(self.db.crawler_meta.find_one({"_id": "cooldown:stopgame"})["until"], 1010)

    def test_sitemap_captcha_stops_requests_to_remaining_maps(self):
        second_leaf = "https://stopgame.ru/sitemap/news_2.xml"
        session = FakeSession([
            FakeResponse(ROBOTS, 200, ALLOW),
            FakeResponse(ROOT, 200, sitemap(LEAF, second_leaf, index=True)),
            FakeResponse(LEAF, 200, b"<title>Just a moment</title>"),
        ])
        with self.assertRaises(RuntimeError):
            self.worker(session).update_frontier()
        self.assertEqual([url for url, _ in session.calls], [ROBOTS, ROOT, LEAF])
        self.assertFalse(self.inventory.exists())

    def test_frontier_refresh_does_not_reset_an_active_task(self):
        self.worker(FakeSession(maps(URL))).update_frontier()
        self.db.frontier.update_one({"_id": URL}, {"$set": {
            "state": "in_progress", "lease_token": "running", "available_at": 5000,
        }})
        self.worker(FakeSession(maps(URL, NEW_URL))).update_frontier()
        old = self.db.frontier.find_one({"_id": URL})
        self.assertEqual((old["state"], old["lease_token"], old["available_at"]),
                         ("in_progress", "running", 5000))

    def test_failed_bootstrap_does_not_create_empty_inventory(self):
        worker = self.worker(FakeSession([FakeResponse(ROBOTS, 403)]))
        with self.assertRaises(RuntimeError):
            worker.update_frontier()
        self.assertFalse(self.inventory.exists())
        self.assertEqual(self.db.frontier.count_documents({}), 0)

    def test_denied_nested_sitemap_is_not_requested(self):
        session = FakeSession([
            FakeResponse(ROBOTS, 200, b"User-agent: *\nDisallow: /sitemap/\n"),
            FakeResponse(ROOT, 200, sitemap(LEAF, index=True)),
        ])
        with self.assertRaises(RuntimeError):
            self.worker(session).update_frontier()
        self.assertEqual([url for url, _ in session.calls], [ROBOTS, ROOT])

    def test_sitemap_redirect_cannot_leave_source(self):
        session = FakeSession([FakeResponse(ROBOTS, 200, ALLOW),
                               FakeResponse(ROOT, 302, headers={"Location": "https://example.org/map.xml"})])
        with self.assertRaises(RuntimeError):
            self.worker(session).update_frontier()
        self.assertEqual(len(session.calls), 2)

    def test_partial_success_and_root_urlset_not_fetched_twice(self):
        game_url = "https://gamemag.ru/news/123/example"
        session = FakeSession([FakeResponse(ROBOTS, 503),
                               FakeResponse("https://gamemag.ru/robots.txt", 200, ALLOW),
                               FakeResponse("https://gamemag.ru/sitemap.xml", 200, sitemap(game_url))])
        worker = self.worker(session, sitemap_sources=("stopgame", "gamemag"))
        self.assertEqual(worker.update_frontier(), 1)
        status = self.db.crawler_meta.find_one({"_id": "sitemap_refresh"})
        self.assertEqual(status["status"], "partial")
        self.assertIn("stopgame", status["refresh_errors"])
        self.assertEqual(len(session.calls), 3)

    def test_revisit_never_refreshes_or_seeds_inventory(self):
        worker = self.worker(FakeSession([]))
        with patch.object(worker, "update_frontier") as update:
            worker.run(mode="revisit")
        update.assert_not_called()
        self.assertFalse(self.inventory.exists())

    def test_continuous_mode_refreshes_and_discovers_work_while_idle(self):
        self.worker(FakeSession(maps(URL))).update_frontier()
        self.db.frontier.update_one({"_id": URL}, {"$set": {"state": "done", "available_at": 9000}})
        session = FakeSession(maps(URL) + maps(URL, NEW_URL) + [FakeResponse(NEW_URL, 200, HTML_1)])
        worker = self.worker(session, run_forever=True, sitemap_refresh_interval_seconds=5, poll_seconds=60)
        with patch("src.crawler.time.sleep", side_effect=self.clock.sleep):
            self.assertEqual(worker.run()["changed"], 1)
        self.assertEqual(self.clock.sleeps, [5])
        self.assertEqual(self.db.frontier.find_one({"_id": NEW_URL})["state"], "done")


class HttpPacingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def gate(self, responses, **overrides):
        settings = replace(config(Path("unused")), **overrides)
        return HttpGate(FakeSession(responses), settings, clock=self.clock.now, sleep=self.clock.sleep)

    def test_global_delay_applies_across_different_sources(self):
        gate = self.gate([FakeResponse(URL, 200), FakeResponse("https://gamemag.ru/news/1/test", 200)],
                         global_delay_seconds=2)
        gate._send("stopgame", URL)
        gate._send("gamemag", "https://gamemag.ru/news/1/test")
        self.assertEqual(self.clock.sleeps, [2])

    def test_delays_take_maximum_not_sum_and_include_robots_crawl_delay(self):
        gate = self.gate([FakeResponse(ROBOTS, 200, ALLOW + b"Crawl-delay: 7\n"),
                          FakeResponse(URL, 200), FakeResponse(NEW_URL, 200)],
                         global_delay_seconds=2, delay_seconds=3)
        gate.get("stopgame", URL, {})
        # Parsing/other useful work consumes part of the minimum interval.
        self.clock.value += 4
        gate.get("stopgame", NEW_URL, {})
        self.assertEqual(self.clock.sleeps, [7, 3])

    def test_robots_request_uses_the_same_local_delay_as_pages(self):
        gate = self.gate([FakeResponse(ROBOTS, 200, ALLOW), FakeResponse(URL, 200),
                          FakeResponse(NEW_URL, 200)], delay_seconds=2)
        gate.get("stopgame", URL, {})
        gate.get("stopgame", NEW_URL, {})
        self.assertEqual(self.clock.sleeps, [2, 2])

    def test_local_override_shared_between_host_aliases(self):
        alias = "https://www.stopgame.ru/newsdata/1/test"
        gate = self.gate([FakeResponse(URL, 200), FakeResponse(alias, 200)],
                         delay_seconds=1, source_delays_seconds={"stopgame": 6})
        gate._send("stopgame", URL)
        gate._send("stopgame", alias)
        self.assertEqual(self.clock.sleeps, [6])

    def test_robots_denial_does_not_download_page(self):
        gate = self.gate([FakeResponse(ROBOTS, 200, b"User-agent: *\nDisallow: /\n")])
        with self.assertRaises(RobotsDenied):
            gate.get("stopgame", URL, {})
        self.assertEqual(len(gate.session.calls), 1)


class DiscoveryConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "crawler.yaml"

    def load(self, **logic):
        data = {"db": {"uri": "mongodb://localhost", "name": "test"},
                "logic": {"inventory": "generated/url_inventory.tsv", **logic}}
        self.path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return load_config(self.path)

    def test_enabled_discovery_can_bootstrap_without_local_inventory(self):
        loaded = self.load(local_delay_seconds=3, global_delay_seconds=1,
                           source_delays_seconds={"stopgame": 5},
                           sitemap_sources=["stopgame"])
        self.assertTrue(loaded.refresh_sitemaps)
        self.assertFalse(loaded.inventory.exists())
        self.assertEqual(loaded.delay_seconds, 3)
        self.assertEqual(loaded.sitemap_sources, ("stopgame",))
        self.assertEqual(loaded.discovery_data_dir, self.path.parent / "data")

    def test_disabled_refresh_requires_existing_inventory(self):
        with self.assertRaises(ValueError):
            self.load(refresh_sitemaps=False)

    def test_legacy_delay_is_local_alias(self):
        self.assertEqual(self.load(delay_seconds=4).delay_seconds, 4)
        self.assertEqual(self.load(delay_seconds=4, local_delay_seconds=6).delay_seconds, 6)

    def test_bad_discovery_and_pacing_settings_rejected(self):
        bad_settings = [
            {"sitemap_sources": []}, {"sitemap_sources": ["unknown"]},
            {"sitemap_sources": ["stopgame", "stopgame"]}, {"refresh_sitemaps": "true"},
            {"sitemap_refresh_interval_seconds": 0}, {"sitemap_refresh_interval_seconds": 1.5},
            {"sitemap_cache_ttl_seconds": -1}, {"sitemap_recheck_seconds": 0},
            {"global_delay_seconds": -1}, {"local_delay_seconds": float("nan")},
            {"source_delays_seconds": {"unknown": 3}},
            {"source_delays_seconds": {"stopgame": True}}, {"discovery_data_dir": ""},
            {"inventory": "urls.txt"},
        ]
        for settings in bad_settings:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.load(**settings)


if __name__ == "__main__":
    unittest.main()
