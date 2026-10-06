"""Read-only progress and size summary for a crawler YAML configuration."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import load_config
from src.downloader import SOURCE_HOSTS


def main() -> int:
    if len(sys.argv) != 2:
        print("Использование: python crawler_status.py <config.yaml>", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(sys.argv[1]))
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            db = client[config.db_name]
            now = int(time.time())
            states = {
                row["_id"]: row["count"] for row in db.frontier.aggregate([
                    {"$group": {"_id": "$state", "count": {"$sum": 1}}}
                ])
            }
            db_stats = db.command("dbStats")
            cooldowns = {}
            for source in SOURCE_HOSTS:
                pause = db.crawler_meta.find_one({"_id": f"cooldown:{source}"}, {"until": 1})
                if pause and int(pause.get("until", 0)) > now:
                    cooldowns[source] = int(pause["until"])
            print(json.dumps({
                "database": config.db_name,
                "documents": db.documents.estimated_document_count(),
                "queued_urls": db.frontier.estimated_document_count(),
                "states": states,
                "due_now": db.frontier.count_documents({"available_at": {"$lte": now}}),
                "source_cooldowns": cooldowns,
                "data_mib": round(db_stats["dataSize"] / 1048576, 1),
                "storage_mib": round(db_stats["storageSize"] / 1048576, 1),
                "index_mib": round(db_stats["indexSize"] / 1048576, 1),
            }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[crawler-status] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
