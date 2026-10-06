"""Lab 2 search robot: its only argument is a YAML configuration file."""

from __future__ import annotations

import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import Crawler, load_config


def main() -> int:
    if len(sys.argv) != 2:
        print("Использование: python crawler.py <config.yaml>", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(sys.argv[1]))
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            Crawler(config, client[config.db_name]).run()
    except KeyboardInterrupt:
        print("[crawler] Остановлен; незавершённый URL вернётся в очередь при следующем запуске.")
        return 130
    except (OSError, ValueError, RuntimeError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[crawler] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
