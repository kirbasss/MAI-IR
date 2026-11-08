"""Read-only progress and size summary for a crawler YAML configuration."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import due_query, load_config
from src.downloader import SOURCE_HOSTS
from src.disk_guard import DiskGuard, DiskGuardError


def collection_sizes(db):
    """Metadata only: do not scan HTML bodies to calculate their sizes."""
    result = {}
    for name in ("raw_pages", "documents", "frontier", "crawler_meta"):
        stats = db.command({"collStats": name, "scale": 1, "maxTimeMS": 5000})
        storage, indexes = stats.get("storageSize", 0), stats.get("totalIndexSize", 0)
        result[name] = {
            "count": stats.get("count", 0),
            "logical_mib": round(stats.get("size", 0) / 1048576, 2),
            "storage_mib": round(storage / 1048576, 2),
            "indexes_mib": round(indexes / 1048576, 2),
            "disk_with_indexes_mib": round((storage + indexes) / 1048576, 2),
            "reusable_mib": round(stats.get("wiredTiger", {}).get("block-manager", {}).get(
                "file bytes available for reuse", 0) / 1048576, 2),
        }
    return result


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
            sizes = collection_sizes(db)
            try:
                disk_safety = DiskGuard(config, db).report(force=True)
            except DiskGuardError as exc:
                disk_safety = {"healthy": False, "error": str(exc)}
            parse_index = next((name for name, info in db.raw_pages.index_information().items()
                                if info["key"][0][0] == "parse_state"), None)
            # Group the small index, not tens of GB of HTML. Before the parser
            # has initialized its index, do not trigger an unbounded raw scan.
            parse_states = None if parse_index is None else {
                row["_id"] or "legacy": row["count"] for row in db.raw_pages.aggregate([
                    {"$group": {"_id": "$parse_state", "count": {"$sum": 1}}}
                ], hint=parse_index, maxTimeMS=10000)
            }
            cooldowns = {}
            for source in SOURCE_HOSTS:
                pause = db.crawler_meta.find_one({"_id": f"cooldown:{source}"}, {"until": 1})
                if pause and int(pause.get("until", 0)) > now:
                    cooldowns[source] = int(pause["until"])
            print(json.dumps({
                "database": config.db_name,
                "documents": db.documents.estimated_document_count(),
                "indexable_documents": db.documents.count_documents({"indexable": {"$ne": False}}),
                "title_only_documents": db.documents.count_documents({
                    "indexable": {"$ne": False}, "parsed.text": ""
                }),
                "excluded_documents": db.documents.count_documents({"indexable": False}),
                "raw_pages": db.raw_pages.estimated_document_count(),
                "queued_urls": db.frontier.estimated_document_count(),
                "collection_sizes": sizes,
                "disk_safety": disk_safety,
                "states": states,
                "parse_states": parse_states,
                "raw_storage_note": "archived = HTML на внешнем диске; raw_pages включает записи-ссылки",
                "parse_queue_note": "Запустите parse_worker.py для создания индекса очереди" if parse_index is None else None,
                "due_now": db.frontier.count_documents({"available_at": {"$lte": now}}),
                "new_due_now": db.frontier.count_documents(due_query(now, "normal")),
                "revisit_due_now": db.frontier.count_documents(due_query(now, "revisit")),
                "source_cooldowns": cooldowns,
                "sitemap_refresh": db.crawler_meta.find_one({"_id": "sitemap_refresh"}, {"_id": 0}),
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
