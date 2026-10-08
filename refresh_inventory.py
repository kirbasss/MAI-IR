"""Update publication sitemaps and seed only new URLs into MongoDB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import Crawler, load_config
from src.downloader import SOURCE_HOSTS
from src.inventory_refresh import refresh_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="YAML с полным inventory и MongoDB")
    parser.add_argument("--sources", nargs="+", choices=sorted(SOURCE_HOSTS),
                        help="обновить только эти сайты; по умолчанию все восемь")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="пауза между запросами sitemap, с")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        data_dir = Path(__file__).resolve().parent / "data"
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            summary = refresh_inventory(
                data_dir=data_dir, inventory_path=config.inventory,
                sources=args.sources or list(SOURCE_HOSTS), delay_seconds=args.delay,
            )
            crawler = Crawler(config, client[config.db_name])
            crawler.ensure_indexes()
            added = crawler.seed()
        print(json.dumps({
            "inventory_urls": summary["total_unique_urls"],
            "refreshed_sources": summary["refreshed_sources"],
            "refresh_errors": summary["refresh_errors"],
            "new_inventory_urls": summary["new_inventory_urls"],
            "new_queue_urls": added,
        }, ensure_ascii=False, indent=2))
        if summary["refresh_errors"]:
            return 1
    except (OSError, ValueError, RuntimeError, KeyError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[inventory-refresh] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
