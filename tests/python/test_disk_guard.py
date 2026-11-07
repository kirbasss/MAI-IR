from __future__ import annotations

import io
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import mongomock
from pymongo.errors import OperationFailure
import yaml

import crawler as crawler_cli
import parse_worker as parser_cli
import refresh_inventory as refresh_cli
from crawler_status import collection_sizes
from src.crawler import Crawler, HttpGate, load_config
from src.disk_guard import DiskGuard, DiskGuardError, DiskSpaceLow, GIB
from src.parser_worker import ParserWorker
from src.parsers import PARSERS
from tests.python.test_crawler import FakeSession, HTML_1, URL, config, responses
from tests.python.test_parallel_crawler import ConcurrentSession, GAME_URL, PC_URL


def usage(free):
    return SimpleNamespace(total=100 * GIB, free=free, used=100 * GIB - free)


class DiskGuardUnitTests(unittest.TestCase):
    def settings(self, **kwargs):
        return replace(config(Path("unused")), min_free_disk_gib=5, **kwargs)

    def test_disabled_guard_does_not_probe_host_or_mongo(self):
        db = Mock(); probe = Mock()
        report = DiskGuard(self.settings(), db, disk_usage=probe).check()
        self.assertFalse(report["enabled"])
        probe.assert_not_called(); db.command.assert_not_called()

    def test_host_boundary_and_every_call_even_inside_mongo_cache_interval(self):
        probe = Mock(side_effect=[usage(5 * GIB), usage(5 * GIB - 1)])
        guard = DiskGuard(self.settings(disk_watch_paths=(Path("host"),)), Mock(), disk_usage=probe)
        self.assertTrue(guard.check()["healthy"])
        with self.assertRaisesRegex(DiskSpaceLow, "Мало места"):
            guard.check()
        self.assertEqual(probe.call_count, 2)

    def test_checks_docker_disk_separately_from_project_disk(self):
        probe = lambda path: usage((20 if str(path) == "project" else 4) * GIB)
        guard = DiskGuard(self.settings(disk_watch_paths=(Path("project"), Path("docker"))), Mock(), disk_usage=probe)
        with self.assertRaisesRegex(DiskSpaceLow, "docker"):
            guard.check()

    def test_missing_disconnected_host_path_fails_closed(self):
        guard = DiskGuard(self.settings(disk_watch_paths=(Path("external"),)), Mock(),
                          disk_usage=Mock(side_effect=FileNotFoundError("drive disconnected")))
        with self.assertRaisesRegex(DiskGuardError, "external"):
            guard.check()

    def test_full_host_stops_even_when_mongo_virtual_disk_would_have_terabytes_free(self):
        db = Mock()
        guard = DiskGuard(self.settings(disk_watch_paths=(Path("docker"),), check_mongo_disk=True), db,
                          disk_usage=lambda p: usage(4 * GIB))
        with self.assertRaises(DiskSpaceLow): guard.check()
        db.command.assert_not_called()

    def test_mongo_filesystem_limit_stops_even_with_free_space_on_host(self):
        db = Mock(); db.command.return_value = {"fsTotalSize": 64 * GIB, "fsUsedSize": 60 * GIB}
        guard = DiskGuard(self.settings(disk_watch_paths=(Path("host"),), check_mongo_disk=True), db,
                          disk_usage=lambda p: usage(20 * GIB))
        report = guard.report()
        self.assertFalse(report["healthy"])
        with self.assertRaisesRegex(DiskSpaceLow, "MongoDB filesystem"): guard.check()

    def test_mongo_cache_expires_and_force_refreshes(self):
        clock = [0.0]; db = Mock()
        db.command.return_value = {"fsTotalSize": 100 * GIB, "fsUsedSize": 20 * GIB}
        guard = DiskGuard(self.settings(check_mongo_disk=True), db, clock=lambda: clock[0])
        guard.check(); clock[0] = 4; guard.check()
        self.assertEqual(db.command.call_count, 1)
        clock[0] = 5; guard.check()
        self.assertEqual(db.command.call_count, 2)
        guard.check(force=True)
        self.assertEqual(db.command.call_count, 3)

    def test_failed_fresh_probe_never_silently_reuses_cached_healthy_value(self):
        db = Mock(); clock = [0.0]
        db.command.side_effect = [{"fsTotalSize": 100 * GIB, "fsUsedSize": 0}, OperationFailure("stats denied"),
                                  OperationFailure("still denied")]
        guard = DiskGuard(self.settings(check_mongo_disk=True), db, clock=lambda: clock[0])
        guard.check(); clock[0] = 6
        for _ in range(2):
            with self.assertRaisesRegex(DiskGuardError, "проверить диск MongoDB"): guard.check()

    def test_malformed_filesystem_counters_rejected(self):
        cases = [{}, {"fsTotalSize": 10, "fsUsedSize": -1}, {"fsTotalSize": 10, "fsUsedSize": 11},
                 {"fsTotalSize": 0, "fsUsedSize": 0}, {"fsTotalSize": True, "fsUsedSize": 0},
                 {"fsTotalSize": float("inf"), "fsUsedSize": 0}, {"fsTotalSize": "100", "fsUsedSize": 0}]
        for value in cases:
            db = Mock(); db.command.return_value = value
            with self.subTest(value=value), self.assertRaises(DiskGuardError):
                DiskGuard(self.settings(check_mongo_disk=True), db).check()

    def test_parallel_callers_share_one_mongo_probe_per_interval(self):
        db = Mock()
        def stats(command):
            time.sleep(.01)
            return {"fsTotalSize": 100 * GIB, "fsUsedSize": 0}
        db.command.side_effect = stats
        guard = DiskGuard(self.settings(check_mongo_disk=True), db)
        with ThreadPoolExecutor(max_workers=8) as pool:
            reports = list(pool.map(lambda _: guard.check(), range(8)))
        self.assertTrue(all(r["healthy"] for r in reports))
        self.assertEqual(db.command.call_count, 1)

    def test_slow_disk_probe_does_not_collapse_global_http_start_spacing(self):
        settings = self.settings(check_mongo_disk=True, global_delay_seconds=.025)
        db = Mock()
        def stats(command):
            time.sleep(.1)
            return {"fsTotalSize": 100 * GIB, "fsUsedSize": 0}
        db.command.side_effect = stats
        guard = DiskGuard(settings, db)
        session = ConcurrentSession(latency=.005)
        gate = HttpGate(session, settings, before_request=guard.check)
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(gate._send, source, url) for source, url in (
                ("stopgame", URL), ("gamemag", GAME_URL), ("pcgamer", PC_URL),
            )]
            for job in jobs: job.result()
        starts = sorted(event[1] for event in session.events)
        self.assertTrue(all(b - a >= .023 for a, b in zip(starts, starts[1:])))


class DiskGuardPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.inventory = self.root / "urls.tsv"
        self.inventory.write_text(f"stopgame\t{URL}\n", encoding="utf-8")
        self.db = mongomock.MongoClient()["test"]
        self.free = [10 * GIB]
        self.cfg = replace(config(self.inventory), disk_watch_paths=(self.root,), parse_mode="deferred")

    def worker(self, session=None, *, workers=1):
        worker = Crawler(replace(self.cfg, download_workers=workers), self.db,
                         session=session or responses(), now=lambda: 1000)
        worker.disk_guard.disk_usage = lambda path: usage(self.free[0])
        return worker

    def parser(self):
        worker = ParserWorker(self.cfg, self.db, now=lambda: 1000)
        worker.disk_guard.disk_usage = lambda path: usage(self.free[0])
        return worker

    def quiet(self, action):
        with redirect_stdout(io.StringIO()): return action()

    def test_low_startup_does_not_reset_leases_create_indexes_or_fetch(self):
        session = FakeSession([]); worker = self.worker(session)
        self.db.frontier.insert_one({"_id": URL, "state": "in_progress", "lease_token": "old"})
        self.free[0] = 4 * GIB
        with patch.object(self.db.frontier, "create_index") as create:
            with self.assertRaises(DiskSpaceLow): self.quiet(worker.run)
        create.assert_not_called(); self.assertEqual(session.calls, [])
        self.assertEqual(self.db.frontier.find_one({"_id": URL})["lease_token"], "old")

    def test_space_drops_during_http_no_false_done_and_parallel_restart_recovers(self):
        for workers in (1, 4):
            self.db.frontier.delete_many({}); self.db.raw_pages.delete_many({}); self.db.documents.delete_many({})
            self.db.crawler_meta.delete_many({}); self.free[0] = 10 * GIB
            session = responses(); get = session.get
            def consume(url, **kwargs):
                response = get(url, **kwargs)
                if url == URL: self.free[0] = 4 * GIB
                return response
            session.get = consume
            with self.subTest(workers=workers), self.assertRaises(DiskSpaceLow):
                self.quiet(self.worker(session, workers=workers).run)
            self.assertEqual(self.db.raw_pages.count_documents({}), 0)
            self.assertEqual(self.db.frontier.find_one({"_id": URL})["state"], "in_progress")
            self.free[0] = 10 * GIB
            self.assertEqual(self.quiet(self.worker(workers=workers).run)["changed"], 1)
            self.assertEqual(self.db.frontier.find_one({"_id": URL})["attempts"], 2)

    def test_parser_low_start_does_not_claim_or_mutate_download_queue(self):
        self.quiet(self.worker().run)
        before = self.db.raw_pages.find_one({"_id": URL})
        frontier = self.db.frontier.find_one({"_id": URL})
        self.free[0] = 4 * GIB
        with self.assertRaises(DiskSpaceLow): self.quiet(self.parser().run)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(self.db.frontier.find_one({"_id": URL}), frontier)

    def test_space_drops_during_parse_not_misclassified_as_parse_error(self):
        self.quiet(self.worker().run)
        original = PARSERS["stopgame"]
        def consume(html, url):
            parsed = original(html, url); self.free[0] = 4 * GIB; return parsed
        with patch.dict(PARSERS, {"stopgame": consume}), self.assertRaises(DiskSpaceLow):
            self.quiet(self.parser().run)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["parse_state"], "in_progress")
        self.assertEqual(self.db.documents.count_documents({}), 0)
        self.free[0] = 10 * GIB
        self.assertEqual(self.quiet(self.parser().run)["done"], 1)
        self.assertEqual(self.db.documents.find_one({"_id": URL})["parsed"]["text"], "First text.")

    def test_sitemap_stop_is_not_swallowed_as_site_error_and_fallback(self):
        worker = self.worker(FakeSession([]))
        with patch("src.crawler.refresh_url_inventory", side_effect=DiskSpaceLow("low disk")):
            with self.assertRaises(DiskSpaceLow): worker.update_inventory()
        self.assertEqual(self.db.crawler_meta.count_documents({}), 0)

    def test_partial_seeding_remains_replayable_after_disk_stop(self):
        self.inventory.write_text("".join(f"stopgame\thttps://stopgame.ru/newsdata/{i}/example\n" for i in range(1, 4)), encoding="utf-8")
        worker = self.worker(FakeSession([])); insert = worker._insert_batch
        def consume(rows):
            added = insert(rows); self.free[0] = 4 * GIB; return added
        with patch.object(worker, "_insert_batch", side_effect=consume), self.assertRaises(DiskSpaceLow):
            worker.seed()
        self.assertEqual(self.db.frontier.count_documents({}), 2)
        self.assertIsNone(self.db.crawler_meta.find_one({"_id": "inventory"}))
        self.free[0] = 10 * GIB
        self.assertEqual(worker.seed(), 1)
        self.assertEqual(self.db.frontier.count_documents({}), 3)


class DiskGuardConfigCliTests(unittest.TestCase):
    def test_config_paths_thresholds_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / "crawler.yaml"
            def load(**kwargs):
                data = {"db": {"uri": "mongodb://unused", "name": "test"},
                        "logic": {"inventory": "url_inventory.tsv", **kwargs}}
                path.write_text(yaml.safe_dump(data), encoding="utf-8")
                return load_config(path)
            cfg = load(disk_watch_paths=[str(root), str(root)], min_free_disk_gib=2.5, check_mongo_disk=True)
            self.assertEqual(cfg.disk_watch_paths, (root.resolve(),))
            self.assertEqual(cfg.min_free_disk_gib, 2.5)
            for settings in ({"disk_watch_paths": "D:/"}, {"disk_watch_paths": [True]},
                             {"disk_watch_paths": [""]}, {"disk_watch_paths": ["relative/path"]},
                             {"min_free_disk_gib": 0}, {"min_free_disk_gib": -1},
                             {"min_free_disk_gib": float("nan")}, {"min_free_disk_gib": True},
                             {"check_mongo_disk": "yes"}, {"disk_check_interval_seconds": 0}):
                with self.subTest(settings=settings), self.assertRaises(ValueError): load(**settings)

    def test_entrypoints_report_protective_exit_code(self):
        settings = SimpleNamespace(db_uri="mongodb://unused", db_name="test")
        for module, script, type_name, method in (
            (crawler_cli, "crawler.py", "Crawler", "run"),
            (parser_cli, "parse_worker.py", "ParserWorker", "run"),
            (refresh_cli, "refresh_inventory.py", "Crawler", "ensure_indexes"),
        ):
            error = io.StringIO()
            with (patch("sys.argv", [script, "unused.yaml"]),
                  patch.object(module, "load_config", return_value=settings),
                  patch.object(module, "MongoClient", return_value=MagicMock()),
                  patch.object(module, type_name) as worker_type,
                  redirect_stderr(error)):
                getattr(worker_type.return_value, method).side_effect = DiskSpaceLow("test reserve")
                self.assertEqual(module.main(), 3)
            self.assertIn("Защитная остановка", error.getvalue())

    def test_collection_sizes_use_stats_and_include_indexes(self):
        db = Mock()
        db.command.return_value = {"count": 10, "size": 8 * 1048576, "storageSize": 3 * 1048576,
                                   "totalIndexSize": 1048576}
        sizes = collection_sizes(db)
        self.assertEqual(sizes["raw_pages"]["disk_with_indexes_mib"], 4)
        self.assertEqual(sizes["documents"]["logical_mib"], 8)
        self.assertEqual(db.command.call_count, 4)


if __name__ == "__main__":
    unittest.main()
