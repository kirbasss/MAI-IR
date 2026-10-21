"""Export an immutable UTF-8 title/text snapshot without modifying MongoDB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from src.token_corpus import export_snapshot, local_documents, mongo_documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--config", type=Path, help="crawler YAML; only the db section is used")
    inputs.add_argument("--parsed-dir", type=Path, help="offline alternative: parser JSON directory")
    parser.add_argument("--output", type=Path, required=True, help="new snapshot directory")
    args = parser.parse_args()
    try:
        if args.config:
            config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
            db_config = config.get("db", {}) if isinstance(config, dict) else {}
            if not isinstance(db_config, dict) or not all(isinstance(db_config.get(key), str) and db_config[key].strip()
                       for key in ("uri", "name")):
                raise ValueError("YAML must contain db.uri and db.name")
            with MongoClient(db_config["uri"], serverSelectionTimeoutMS=5000) as client:
                client.admin.command("ping")
                result = export_snapshot(mongo_documents(client[db_config["name"]]), args.output,
                                         origin=f"mongodb:{db_config['name']}.documents")
        else:
            if not args.parsed_dir.is_dir():
                raise ValueError(f"Directory not found: {args.parsed_dir}")
            result = export_snapshot(local_documents(args.parsed_dir), args.output,
                                     origin=f"local:{args.parsed_dir.as_posix()}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, UnicodeError, PyMongoError, yaml.YAMLError) as exc:
        parser.exit(1, f"[export] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
