"""Fix StopGame /news/<id> aliases in local inventories and MongoDB queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pymongo import MongoClient

from src.crawler import load_config
from src.stopgame_repair import repair_frontier, repair_inventory_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="YAML конфигурация с MongoDB и инвентарём")
    parser.add_argument("--apply", action="store_true", help="применить исправления; без флага только проверка")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        paths = sorted(config.inventory.parent.parent.glob("inventory*/url_inventory.tsv"))
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            db = client[config.db_name]
            # Validate the queue first; never rewrite inventory if a page is
            # being downloaded or has already been saved under an old ID.
            queue_count = repair_frontier(db)
            inventory_counts = {
                str(path): repair_inventory_file(path, apply=args.apply)
                for path in paths
            }
            if args.apply:
                repaired = repair_frontier(db, apply=True)
                if repaired != queue_count:
                    raise RuntimeError("Очередь изменилась во время исправления")
        print(json.dumps({
            "applied": args.apply,
            "inventory_replacements": inventory_counts,
            "frontier_replacements": queue_count,
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"[stopgame-repair] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
