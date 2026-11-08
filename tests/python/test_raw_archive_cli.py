"""Maintenance disk reserve is opt-in and must not weaken crawler settings."""
from __future__ import annotations

import io
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import archive_raw
from src.disk_guard import DiskSpaceLow
from tests.python.test_crawler import config


class RawArchiveCliTests(unittest.TestCase):
    def invoke(self, flags, *, failure=None, auto=False, selection=(Path("auto_batch"), False), guard_failure=None):
        cfg = replace(config(Path("unused.tsv")), min_free_disk_gib=5)
        client = MagicMock()
        client.__enter__.return_value = client
        destination = ["--auto"] if auto else ["--output-dir", "unused_batch"]
        with (patch("sys.argv", ["archive_raw.py", "unused.yaml", *destination, *flags]),
              patch.object(archive_raw, "load_config", return_value=cfg),
              patch.object(archive_raw, "exclusive_worker", side_effect=lambda *a: nullcontext()),
              patch.object(archive_raw, "MongoClient", return_value=client),
              patch.object(archive_raw, "DiskGuard") as guard,
              patch.object(archive_raw, "choose_auto_batch", return_value=selection),
              patch.object(archive_raw, "export_raw", return_value={"records": 1, "compressed_bytes": 100}) as export,
              patch.object(archive_raw, "verify_archive", return_value={"verified": True}) as verify,
              patch.object(archive_raw, "evict_archive", return_value={"evicted": 1}, side_effect=failure) as evict,
              redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
            guard.return_value.check.side_effect = guard_failure
            code = archive_raw.main()
        return code, cfg, guard, export, verify, evict

    def test_default_eviction_keeps_yaml_reserve(self):
        code, cfg, guard, export, verify, evict = self.invoke(["--existing", "--evict"])
        self.assertEqual(code, 0)
        self.assertIs(guard.call_args.args[0], cfg)
        self.assertEqual(cfg.min_free_disk_gib, 5)
        export.assert_not_called(); verify.assert_not_called(); evict.assert_called_once()

    def test_explicit_maintenance_reserve_uses_an_independent_config(self):
        code, cfg, guard, export, verify, evict = self.invoke([
            "--existing", "--evict", "--maintenance-reserve-gib", "4.5"])
        self.assertEqual(code, 0)
        maintenance = guard.call_args.args[0]
        self.assertIsNot(maintenance, cfg)
        self.assertEqual(maintenance.min_free_disk_gib, 4.5)
        self.assertEqual(cfg.min_free_disk_gib, 5)
        self.assertEqual(maintenance.disk_watch_paths, cfg.disk_watch_paths)
        self.assertEqual(maintenance.check_mongo_disk, cfg.check_mongo_disk)
        self.assertIs(evict.call_args.kwargs["disk_guard"], guard.return_value)
        export.assert_not_called(); verify.assert_not_called()

    def test_export_only_never_needs_source_write_reserve(self):
        code, cfg, guard, export, verify, evict = self.invoke([])
        self.assertEqual(code, 0)
        export.assert_called_once(); verify.assert_called_once()
        guard.assert_not_called(); evict.assert_not_called()

    def test_low_maintenance_reserve_error_is_not_silently_bypassed(self):
        code, cfg, guard, export, verify, evict = self.invoke([
            "--existing", "--evict", "--maintenance-reserve-gib", "4.5"],
            failure=DiskSpaceLow("low disk"))
        self.assertEqual(code, 1)
        self.assertEqual(cfg.min_free_disk_gib, 5)
        evict.assert_called_once()

    def test_invalid_reserve_is_rejected_before_any_database_connection(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with (self.subTest(value=value),
                  patch("sys.argv", ["archive_raw.py", "unused.yaml", "--output-dir", "unused_batch",
                                     "--evict", "--maintenance-reserve-gib=" + value]),
                  patch.object(archive_raw, "MongoClient") as client,
                  redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error):
                archive_raw.main()
            self.assertEqual(error.exception.code, 2)
            client.assert_not_called()

    def test_override_is_not_accepted_without_an_eviction_request(self):
        with (patch("sys.argv", ["archive_raw.py", "unused.yaml", "--output-dir", "unused_batch",
                                 "--maintenance-reserve-gib", "4.5"]),
              patch.object(archive_raw, "MongoClient") as client,
              redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error):
            archive_raw.main()
        self.assertEqual(error.exception.code, 2)
        client.assert_not_called()


    def test_auto_exports_and_evicts_with_one_explicit_reserve(self):
        code, cfg, guard, export, verify, evict = self.invoke(
            ["--maintenance-reserve-gib", "4"], auto=True)
        self.assertEqual(code, 0)
        self.assertEqual(cfg.min_free_disk_gib, 5)
        self.assertEqual(guard.call_args.args[0].min_free_disk_gib, 4)
        guard.return_value.check.assert_called_once_with(force=True)
        export.assert_called_once()
        self.assertEqual(export.call_args.args[1], Path("auto_batch"))
        evict.assert_called_once(); verify.assert_not_called()

    def test_auto_resumes_without_exporting_again(self):
        code, cfg, guard, export, verify, evict = self.invoke(
            [], auto=True, selection=(Path("saved_batch"), True))
        self.assertEqual(code, 0)
        export.assert_not_called(); evict.assert_called_once()
        self.assertEqual(evict.call_args.args[1], Path("saved_batch"))

    def test_auto_no_work_does_not_export_or_write(self):
        code, cfg, guard, export, verify, evict = self.invoke(
            [], auto=True, selection=(None, False))
        self.assertEqual(code, 0)
        guard.return_value.check.assert_not_called()
        export.assert_not_called(); verify.assert_not_called(); evict.assert_not_called()

    def test_auto_low_reserve_stops_before_export(self):
        code, cfg, guard, export, verify, evict = self.invoke(
            [], auto=True, guard_failure=DiskSpaceLow("low disk"))
        self.assertEqual(code, 1)
        export.assert_not_called(); verify.assert_not_called(); evict.assert_not_called()

    def test_auto_rejects_conflicting_manual_options_before_connecting(self):
        for flags in (["--auto", "--existing"], ["--auto", "--output-dir", "batch"],
                      ["--output-dir", "batch", "--archive-root", "root"]):
            with (self.subTest(flags=flags), patch("sys.argv", ["archive_raw.py", "unused.yaml", *flags]),
                  patch.object(archive_raw, "MongoClient") as client,
                  redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error):
                archive_raw.main()
            self.assertEqual(error.exception.code, 2)
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
