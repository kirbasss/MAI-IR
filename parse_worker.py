"""Offline HTML parser: independent durable queue, no network downloads."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import yaml

from src.crawler import load_config
from src.disk_guard import DiskGuardError
from src.parser_worker import ParserWorker
from src.parsers import PARSERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--follow", action="store_true", help="ждать новых HTML до Ctrl+C")
    parser.add_argument("--max-documents", type=int, help="ограничить число разборов в этом запуске")
    parser.add_argument("--sources", nargs="+", choices=sorted(PARSERS), help="разбирать только выбранные сайты")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reparse", action="store_true", help="поставить сохранённые HTML на повторный разбор")
    group.add_argument("--retry-errors", action="store_true", help="повторить только ошибки разбора")
    args = parser.parse_args()
    if args.max_documents is not None and args.max_documents < 1:
        parser.error("--max-documents должен быть положительным")
    try:
        config = load_config(args.config)
        with MongoClient(config.db_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            ParserWorker(config, client[config.db_name]).run(
                follow=args.follow, max_documents=args.max_documents,
                reparse=args.reparse, retry_errors=args.retry_errors,
                sources=args.sources,
            )
    except DiskGuardError as exc:
        print(f"[parser] Защитная остановка: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("[parser] Остановлен; незавершённый разбор восстановится при следующем запуске.")
        return 130
    except (OSError, ValueError, RuntimeError, PyMongoError, yaml.YAMLError) as exc:
        print(f"[parser] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
