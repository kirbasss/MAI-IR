from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import mongomock
import yaml

from src.crawler import CrawlStopped, Crawler, HttpGate, load_config
from src.worker_lock import exclusive_worker
from tests.python.test_crawler import FakeResponse, HTML_1, URL, config


GAME_URL = "https://gamemag.ru/news/1/example"
PC_URL = "https://www.pcgamer.com/a-game-review/"


class ConcurrentSession:
    """In-process HTTP substitute: no sockets, actual threads and pacing clocks."""
    def __init__(self, *, latency=.005, statuses=None, article_hook=None):
        self.headers = {}
        self.mutex = threading.Lock()
        self.latency = latency
        self.statuses = statuses or {}
        self.article_hook = article_hook
        self.events = []
        self.active = {}
        self.peak = {}
        self.total_active = self.total_peak = 0

    def get(self, url, **kwargs):
        host = urlsplit(url).hostname
        start = time.monotonic()
        with self.mutex:
            self.active[host] = self.active.get(host, 0) + 1
            self.peak[host] = max(self.peak.get(host, 0), self.active[host])
            self.total_active += 1
            self.total_peak = max(self.total_peak, self.total_active)
        try:
            if self.article_hook and not url.endswith("/robots.txt"):
                self.article_hook(url)
            time.sleep(self.latency)
            body = b"User-agent: *\nAllow: /\n" if url.endswith("/robots.txt") else HTML_1
            return FakeResponse(url, self.statuses.get(url, 200), body,
                                {"Content-Type": "text/plain" if url.endswith("/robots.txt") else "text/html"})
        finally:
            end = time.monotonic()
            with self.mutex:
                self.events.append((url, start, end))
                self.active[host] -= 1
                self.total_active -= 1


class ParallelCrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.inventory = Path(self.temp.name) / "urls.tsv"
        self.db = mongomock.MongoClient()["test"]

    def worker(self, urls, session=None, **settings):
        self.inventory.write_text("".join(f"{source}\t{url}\n" for source, url in urls), encoding="utf-8")
        cfg = replace(config(self.inventory, max_documents=10), download_workers=3,
                      parse_mode="deferred", **settings)
        return Crawler(cfg, self.db, session=session or ConcurrentSession(), now=lambda: 1000)

    def run_quiet(self, worker, **kwargs):
        with redirect_stdout(io.StringIO()):
            return worker.run(**kwargs)

    def test_different_sites_overlap_and_same_site_never_overlaps(self):
        both_started = threading.Barrier(2)
        first = {URL, GAME_URL}
        def hook(url):
            if url in first:
                both_started.wait(timeout=2)
        session = ConcurrentSession(latency=.02, article_hook=hook)
        worker = self.worker([("stopgame", URL), ("stopgame", URL + "2"), ("gamemag", GAME_URL)], session)
        self.assertEqual(self.run_quiet(worker)["changed"], 3)
        self.assertGreaterEqual(session.total_peak, 2)
        self.assertTrue(all(value == 1 for value in session.peak.values()))
        self.assertEqual(self.db.raw_pages.count_documents({"parse_state": "pending"}), 3)
        self.assertEqual(self.db.documents.count_documents({}), 0)

    def test_local_wait_does_not_hold_up_ready_site(self):
        session = ConcurrentSession()
        worker = self.worker([("stopgame", URL), ("gamemag", GAME_URL)], session)
        worker.gate.last_source_request["stopgame"] = time.monotonic()
        worker.gate.config = replace(worker.config, source_delays_seconds={"stopgame": .12})
        self.assertEqual(self.run_quiet(worker)["changed"], 2)
        pages = sorted([event for event in session.events if not event[0].endswith("robots.txt")], key=lambda e: e[1])
        self.assertEqual(pages[0][0], GAME_URL)
        self.assertLess(pages[0][2], pages[1][1])

    def test_round_robin_not_two_leases_to_busy_source_and_no_limit_overshoot(self):
        worker = self.worker([("stopgame", URL + str(i)) for i in range(5)] + [("gamemag", GAME_URL)])
        worker.config = replace(worker.config, max_documents=2)
        self.assertEqual(self.run_quiet(worker)["changed"], 2)
        self.assertEqual({r["source"] for r in self.db.raw_pages.find()}, {"stopgame", "gamemag"})
        self.assertEqual(self.db.frontier.count_documents({"state": "pending"}), 4)
        self.assertEqual(self.db.frontier.count_documents({"state": "in_progress"}), 0)

    def test_challenge_cools_only_its_site_even_with_parallel_workers(self):
        session = ConcurrentSession(statuses={URL: 429})
        worker = self.worker([("stopgame", URL), ("gamemag", GAME_URL), ("stopgame", URL + "2")], session)
        worker.prepare(); worker.seed()
        self.db.frontier.update_one({"_id": URL}, {"$set": {"queue_order": 0}})
        result = self.run_quiet(worker)
        self.assertEqual((result["retry"], result["changed"]), (1, 1))
        self.assertEqual(self.db.frontier.find_one({"_id": URL + "2"})["attempts"], 0)
        self.assertEqual(self.db.raw_pages.count_documents({}), 1)

    def test_crashed_download_lease_is_recovered_on_restart(self):
        worker = self.worker([("stopgame", URL)])
        with patch.object(worker, "process", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.run_quiet(worker)
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["state"], "in_progress")
        restarted = self.worker([("stopgame", URL)])
        self.assertEqual(self.run_quiet(restarted)["changed"], 1)
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["attempts"], 2)

    def test_download_timings_written_without_inline_parser(self):
        worker = self.worker([("stopgame", URL)])
        with patch("src.crawler.PARSERS", {"stopgame": lambda *a: self.fail("inline parser called")}):
            self.assertEqual(self.run_quiet(worker)["changed"], 1)
        timing = self.db.frontier.find_one({"_id": URL})["timings_ms"]
        for name in ("http_ms", "wait_ms", "save_ms", "total_ms"):
            self.assertGreaterEqual(timing[name], 0)
        self.assertEqual(timing["http_requests"], 2)
        self.assertNotIn("parse_ms", timing)

    def test_revisit_parallel_keeps_new_document_mode_separate(self):
        worker = self.worker([("stopgame", URL), ("gamemag", GAME_URL)])
        self.assertEqual(self.run_quiet(worker, mode="revisit")["changed"], 0)
        self.assertEqual(self.db.frontier.count_documents({}), 0)

    def test_other_coordinator_cannot_reset_running_leases(self):
        worker = self.worker([("stopgame", URL)])
        self.db.frontier.insert_one({"_id": URL, "state": "in_progress", "lease_token": "active"})
        with exclusive_worker("test", "crawler"):
            with self.assertRaisesRegex(RuntimeError, "уже запущен"):
                self.run_quiet(worker)
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["lease_token"], "active")

    def test_continuous_refresh_respects_download_barrier_and_does_not_delay_new_work(self):
        worker = self.worker([("stopgame", URL)])
        worker.config = replace(worker.config, refresh_sitemaps=True, run_forever=True, max_documents=1)
        count = [0]
        def refresh():
            count[0] += 1
            if count[0] == 1:
                worker.next_sitemap_refresh_at = 1000
                return 0
            worker.next_sitemap_refresh_at = 2000
            return worker.seed()
        with patch.object(worker, "update_frontier", side_effect=refresh), patch.object(
            worker.gate, "_pause", side_effect=AssertionError("idle sleep before newly discovered work")
        ):
            self.assertEqual(self.run_quiet(worker)["changed"], 1)
        self.assertEqual(count[0], 2)


class ConcurrentGateTests(unittest.TestCase):
    def test_global_start_slots_shared_while_network_requests_overlap(self):
        session = ConcurrentSession(latency=.09)
        gate = HttpGate(session, replace(config(Path("unused")), global_delay_seconds=.025))
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(gate._send, source, url) for source, url in (
                ("stopgame", URL), ("gamemag", GAME_URL), ("pcgamer", PC_URL),
            )]
            for job in jobs: job.result()
        starts = sorted(event[1] for event in session.events)
        self.assertTrue(all(b - a >= .023 for a, b in zip(starts, starts[1:])))
        self.assertGreaterEqual(session.total_peak, 2)

    def test_local_delay_measured_from_completion_and_aliases_share_lock(self):
        session = ConcurrentSession(latency=.02)
        gate = HttpGate(session, replace(config(Path("unused")), delay_seconds=.03))
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(gate._send, "stopgame", url) for url in (
                URL, "https://www.stopgame.ru/newsdata/2/example",
            )]
            for job in jobs: job.result()
        events = sorted(session.events, key=lambda e: e[1])
        self.assertGreaterEqual(events[1][1] - events[0][2], .028)
        self.assertEqual(session.total_peak, 1)

    def test_session_is_separate_for_each_source(self):
        sessions = []
        def factory():
            session = ConcurrentSession(); sessions.append(session); return session
        gate = HttpGate(ConcurrentSession(), config(Path("unused")), session_factory=factory)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(gate._send, source, url) for source, url in (
                ("stopgame", URL), ("gamemag", GAME_URL),
            )]
            for job in jobs: job.result()
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(len(session.events) == 1 for session in sessions))

    def test_stop_interrupts_a_long_rate_limit_wait_without_request(self):
        session = ConcurrentSession()
        gate = HttpGate(session, replace(config(Path("unused")), delay_seconds=60))
        gate.last_source_request["stopgame"] = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(gate._send, "stopgame", URL)
            gate.stop.set()
            with self.assertRaises(CrawlStopped): job.result(timeout=1)
        self.assertEqual(session.events, [])


class ParallelConfigAndLockTests(unittest.TestCase):
    def test_invalid_workers_and_parse_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            base = {"db": {"uri": "mongodb://unused", "name": "test"},
                    "logic": {"inventory": "url_inventory.tsv"}}
            for settings in ({"download_workers": 0}, {"download_workers": 9},
                             {"download_workers": 1.5}, {"download_workers": True},
                             {"parse_mode": "other"}, {"parse_mode": []}):
                base["logic"] = {"inventory": "url_inventory.tsv", **settings}
                path.write_text(yaml.safe_dump(base), encoding="utf-8")
                with self.subTest(settings=settings), self.assertRaises(ValueError): load_config(path)

    def test_lock_roles_independent_and_released_on_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "stop"):
                with exclusive_worker("test", "crawler", directory=root):
                    with exclusive_worker("test", "parser", directory=root):
                        raise ValueError("stop")
            with exclusive_worker("test", "crawler", directory=root):
                with self.assertRaises(RuntimeError):
                    with exclusive_worker("test", "crawler", directory=root): self.fail("duplicate lock")


if __name__ == "__main__":
    unittest.main()
