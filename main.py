from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.downloader import collect_file, parse_fixture_manifest
from src.statistics import build_statistics


DEFAULT_DATA_DIR = Path("data")
DEFAULT_RESULTS_DIR = Path("results")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="МАИ — Информационный поиск, ЛР 1: игровой корпус"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="проверить robots.txt, скачать и распарсить URL")
    collect.add_argument("urls", type=Path, help="source<TAB>url, UTF-8")
    collect.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    collect.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, с")

    fixtures = sub.add_parser("parse-fixtures", help="распарсить уже сохранённые HTML-образцы")
    fixtures.add_argument("manifest", type=Path, help="source<TAB>url<TAB>raw_file")
    fixtures.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

    stats = sub.add_parser("stats", help="посчитать статистику игрового корпуса")
    stats.add_argument("--parsed-root", type=Path, default=DEFAULT_DATA_DIR / "parsed")
    stats.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)

    args = parser.parse_args()
    if args.command == "collect":
        collect_file(args.urls, data_dir=args.data_dir, delay_seconds=args.delay)
    elif args.command == "parse-fixtures":
        parse_fixture_manifest(args.manifest, data_dir=args.data_dir)
    elif args.command == "stats":
        print(json.dumps(
            build_statistics(args.parsed_root, args.results_dir),
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
