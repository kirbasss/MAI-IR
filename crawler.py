"""Lab 2 search robot and URL inventory importer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import Crawler, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="YAML-конфигурация краулера")
    parser.add_argument("--seed-only", action="store_true",
                        help="добавить URL в MongoDB без скачивания страниц")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            crawler = Crawler(config, client[config.db_name])
            if args.seed_only:
                crawler.ensure_indexes()
                print(f"[crawler] Очередь пополнена: {crawler.seed()} URL")
            else:
                crawler.run()
    except KeyboardInterrupt:
        print("[crawler] Остановлен; незавершённый URL вернётся в очередь при следующем запуске.")
        return 130
    except (OSError, ValueError, RuntimeError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[crawler] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
