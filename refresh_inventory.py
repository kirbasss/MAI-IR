"""Update publication sitemaps and seed only new URLs into MongoDB."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import Crawler, load_config
from src.downloader import SOURCE_HOSTS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="YAML с полным inventory и MongoDB")
    parser.add_argument("--sources", nargs="+", choices=sorted(SOURCE_HOSTS),
                        help="обновить только эти сайты; по умолчанию sitemap_sources из YAML")
    parser.add_argument("--delay", type=float,
                        help="переопределить локальную паузу по умолчанию, с")
    parser.add_argument("--force", action="store_true", help="проверить все карты по сети, игнорируя TTL кеша")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.delay is not None:
            if not math.isfinite(args.delay) or args.delay < 0:
                raise ValueError("--delay должен быть конечным неотрицательным числом")
            config = replace(config, delay_seconds=args.delay)
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            crawler = Crawler(config, client[config.db_name])
            crawler.ensure_indexes()
            summary = crawler.update_inventory(sources=args.sources, strict=True, force=args.force)
            added = crawler.seed()
            crawler.meta.update_one({"_id": "sitemap_refresh"}, {"$set": {"queue_urls_added": added}})
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
