"""Fail-closed free-disk reserve for the host and MongoDB's backing filesystem."""
from __future__ import annotations

import math
import shutil
import threading
import time

from pymongo.errors import PyMongoError


GIB = 1024 ** 3


class DiskGuardError(RuntimeError):
    """Stop producers without marking unfinished work as downloaded/parsed."""


class DiskSpaceLow(DiskGuardError):
    pass


class DiskGuard:
    def __init__(self, config, db, *, disk_usage=None, clock=None):
        self.config = config
        self.db = db
        self.disk_usage = disk_usage or shutil.disk_usage
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._mongo_sample = None
        self._mongo_checked_at = None

    def report(self, *, force=False):
        """Read-only diagnostics: low space is returned, probe errors are raised.

        Host space is measured EVERY time. Mongo dbStats is cached briefly to
        avoid turning a four-thread crawler into a disk-statistics benchmark.
        The threshold is a reserve, not an atomic disk quota or a RAM limit.
        """
        with self._lock:
            minimum = int(self.config.min_free_disk_gib * GIB)
            checks = []
            for path in self.config.disk_watch_paths:
                try:
                    usage = self.disk_usage(path)
                except (OSError, ValueError) as exc:
                    raise DiskGuardError(f"Не удалось проверить место на {path}: {exc}. Запись остановлена.") from exc
                checks.append({"kind": "host", "path": str(path), "free_bytes": usage.free,
                               "total_bytes": usage.total, "healthy": usage.free >= minimum})
            # If the Windows disk is already low, do not wait for MongoDB,
            # which may be unable to answer precisely because that disk is full.
            if self.config.check_mongo_disk and all(row["healthy"] for row in checks):
                now = self.clock()
                if (force or self._mongo_sample is None or self._mongo_checked_at is None or
                        now - self._mongo_checked_at >= self.config.disk_check_interval_seconds):
                    try:
                        stats = self.db.command({"dbStats": 1, "scale": 1, "maxTimeMS": 2000})
                    except PyMongoError as exc:
                        # Never silently reuse a previous healthy probe after
                        # the new one failed, or downgrade this to an HTTP retry.
                        raise DiskGuardError(f"Не удалось проверить диск MongoDB: {exc}. Запись остановлена.") from exc
                    total, used = stats.get("fsTotalSize"), stats.get("fsUsedSize")
                    if (isinstance(total, bool) or isinstance(used, bool) or
                            not isinstance(total, (int, float)) or not isinstance(used, (int, float)) or
                            not math.isfinite(total) or not math.isfinite(used) or not 0 <= used <= total or total <= 0):
                        raise DiskGuardError("MongoDB не вернула корректные fsTotalSize/fsUsedSize; запись остановлена.")
                    self._mongo_sample = {"kind": "mongodb", "path": "MongoDB filesystem",
                                          "free_bytes": int(total - used), "total_bytes": int(total)}
                    self._mongo_checked_at = now
                checks.append({**self._mongo_sample, "healthy": self._mongo_sample["free_bytes"] >= minimum})
            return {"enabled": bool(self.config.disk_watch_paths or self.config.check_mongo_disk),
                    "min_free_gib": self.config.min_free_disk_gib,
                    "mongo_check_interval_seconds": self.config.disk_check_interval_seconds,
                    "healthy": all(row["healthy"] for row in checks), "checks": checks}

    def check(self, *, force=False):
        report = self.report(force=force)
        low = next((row for row in report["checks"] if not row["healthy"]), None)
        if low:
            raise DiskSpaceLow(
                f"Мало места на {low['path']}: свободно {low['free_bytes'] / GIB:.2f} ГиБ, "
                f"порог {report['min_free_gib']:.2f} ГиБ. Новая работа остановлена; "
                "освободите место и повторите запуск. Ничего автоматически не удаляется."
            )
        return report
