"""Lab 2 search robot and URL inventory importer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import Crawler, RunMode, load_config
from src.disk_guard import DiskGuardError
from src.worker_lock import exclusive_worker


def main(*, mode: RunMode = "normal") -> int:
    parser = argparse.ArgumentParser(description=(
        "Повторная проверка ранее обработанных URL" if mode == "revisit" else __doc__
    ))
    parser.add_argument("config", type=Path, help="YAML-конфигурация краулера")
    if mode == "normal":
        parser.add_argument("--seed-only", action="store_true",
                            help="обновить sitemap и очередь без скачивания HTML-публикаций")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            crawler = Crawler(config, client[config.db_name])
            if mode == "normal" and args.seed_only:
                with exclusive_worker(config.db_name, "crawler"):
                    try:
                        crawler.ensure_indexes()
                        print(f"[crawler] Очередь пополнена: {crawler.update_frontier()} URL")
                    finally:
                        crawler.gate.close_sessions()
            else:
                crawler.run(mode=mode)
    except DiskGuardError as exc:
        print(f"[crawler] Защитная остановка: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("[crawler] Остановлен; незавершённый URL вернётся в очередь при следующем запуске.")
        return 130
    except (OSError, ValueError, RuntimeError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[crawler] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
