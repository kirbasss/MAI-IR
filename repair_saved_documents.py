"""Preview or apply reclassification and reparsing of already saved pages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.corpus_repair import repair_saved_documents
from src.crawler import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="YAML-конфигурация краулера")
    parser.add_argument("--apply", action="store_true", help="применить изменения в MongoDB")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            result = repair_saved_documents(client[config.db_name], apply=args.apply)
        print(json.dumps({"applied": args.apply, **result}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[repair] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
