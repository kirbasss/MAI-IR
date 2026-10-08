"""Move legacy HTML into raw_pages; defaults to a read-only preview."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import load_config
from src.raw_migration import migrate_raw_pages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="YAML с адресом MongoDB")
    parser.add_argument("--apply", action="store_true",
                        help="выполнить перенос после создания резервной копии")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            result = migrate_raw_pages(client[config.db_name], apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[raw-migration] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
