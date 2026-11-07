"""Archive completed raw HTML losslessly; evict only explicitly and after full verification."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import load_config
from src.disk_guard import DiskGuard
from src.raw_archive import export_raw, verify_archive, evict_archive, choose_auto_batch
from src.worker_lock import exclusive_worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir", type=Path, help="каталог конкретной пачки на диске архива")
    destination.add_argument("--auto", action="store_true", help="автоматически выбрать пачку, проверить и заменить HTML ссылками")
    parser.add_argument("--archive-root", type=Path, help="для --auto; по умолчанию E:/Kirbass/MAI-IR/raw_archives")
    parser.add_argument("--existing", action="store_true", help="проверить уже созданную пачку, не экспортировать заново")
    parser.add_argument("--evict", action="store_true", help="ПОСЛЕ проверки заменить точные версии raw ссылками на архив")
    parser.add_argument("--maintenance-reserve-gib", type=float,
                        help="отдельный резерв диска MongoDB для --evict/--auto; YAML и робот не меняются")
    args = parser.parse_args()
    if args.auto:
        if args.existing:
            parser.error("--existing не нужен с --auto: готовая незавершённая пачка выбирается автоматически")
        args.evict = True
    elif args.archive_root is not None:
        parser.error("--archive-root применяется только вместе с --auto")
    if args.maintenance_reserve_gib is not None:
        if not args.evict:
            parser.error("--maintenance-reserve-gib применяется только вместе с --evict или --auto")
        if not math.isfinite(args.maintenance_reserve_gib) or args.maintenance_reserve_gib <= 0:
            parser.error("--maintenance-reserve-gib должен быть конечным положительным числом")
    try:
        config = load_config(args.config)
        with ExitStack() as stack:
            stack.enter_context(exclusive_worker(config.db_name, "crawler"))
            stack.enter_context(exclusive_worker(config.db_name, "parser"))
            client = stack.enter_context(MongoClient(config.db_uri, serverSelectionTimeoutMS=5000))
            client.admin.command("ping"); db = client[config.db_name]
            guard_config = config
            if args.maintenance_reserve_gib is not None:
                # Explicit maintenance exception, never a crawler config
                # change or an implicit retry after a safety stop.
                guard_config = replace(config, min_free_disk_gib=args.maintenance_reserve_gib)
                print(f"[archive] Резерв обслуживания: {guard_config.min_free_disk_gib:g} ГиБ; "
                      f"резерв робота в YAML: {config.min_free_disk_gib:g} ГиБ (не изменён)", flush=True)
            guard = DiskGuard(guard_config, db) if args.evict else None
            if args.auto:
                args.output_dir, args.existing = choose_auto_batch(
                    db, args.archive_root or Path("E:/Kirbass/MAI-IR/raw_archives"))
                if args.output_dir is None:
                    print("[archive] Нет новых готовых HTML для переноса; база не изменена.", flush=True)
                    return 0
                # Fail early rather than export for hours before finding that
                # the explicitly selected maintenance reserve is unavailable.
                guard.check(force=True)
                print(f"[archive] {'Продолжение' if args.existing else 'Новая пачка'}: {args.output_dir}", flush=True)
            if not args.existing:
                result = export_raw(db, args.output_dir)
                print(f"[archive] Exported {result['records']} records; compressed={result['compressed_bytes']} bytes", flush=True)
            if args.evict:
                result = evict_archive(db, args.output_dir, disk_guard=guard)
            else:
                result = verify_archive(args.output_dir)
            print(json.dumps({"directory": str(args.output_dir.resolve()), **result}, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("[archive] Остановлено. Архив/прогресс сохранены; незавершённый экспорт не разрешает удалять raw.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error, PyMongoError, yaml.YAMLError) as exc:
        print(f"[archive] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
