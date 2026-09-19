from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.discovery import SITEMAP_ROOTS, discover_sitemaps, merge_inventories
from src.downloader import collect_file, parse_fixture_manifest, reparse_saved_documents
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

    reparse = sub.add_parser(
        "reparse", help="повторно распарсить сохранённый raw HTML без сети"
    )
    reparse.add_argument(
        "--parsed-root", type=Path, default=DEFAULT_DATA_DIR / "parsed"
    )

    stats = sub.add_parser("stats", help="посчитать статистику игрового корпуса")
    stats.add_argument("--parsed-root", type=Path, default=DEFAULT_DATA_DIR / "parsed")
    stats.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)

    discover = sub.add_parser(
        "discover", help="собрать URL публикаций из sitemap без скачивания HTML"
    )
    discover.add_argument(
        "--sources", nargs="+", choices=sorted(SITEMAP_ROOTS),
        help="источники для обхода; по умолчанию все",
    )
    discover.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    discover.add_argument(
        "--output-dir", type=Path,
        help="каталог инвентаря; по умолчанию data/discovery/inventory",
    )
    discover.add_argument(
        "--delay", type=float, default=0.5,
        help="пауза между запросами sitemap, с",
    )
    discover.add_argument(
        "--refresh-roots", action="store_true",
        help="скачать корневые sitemap заново вместо использования сохранённых",
    )
    offline_mode = discover.add_mutually_exclusive_group()
    offline_mode.add_argument(
        "--roots-only", action="store_true",
        help="без сети: подготовить список подходящих дочерних sitemap",
    )
    offline_mode.add_argument(
        "--cache-only", action="store_true",
        help="без сети: восстановить URL-инвентарь из сохранённых sitemap",
    )

    merge = sub.add_parser(
        "merge-inventories", help="объединить инвентари URL без загрузки HTML"
    )
    merge.add_argument("input_dirs", type=Path, nargs="+", help="каталоги с url_inventory.tsv")
    merge.add_argument(
        "--output-dir", type=Path, default=DEFAULT_DATA_DIR / "discovery" / "inventory_all",
        help="каталог объединённого инвентаря",
    )

    args = parser.parse_args()
    if args.command == "collect":
        collect_file(args.urls, data_dir=args.data_dir, delay_seconds=args.delay)
    elif args.command == "parse-fixtures":
        parse_fixture_manifest(args.manifest, data_dir=args.data_dir)
    elif args.command == "reparse":
        reparse_saved_documents(args.parsed_root)
    elif args.command == "stats":
        print(json.dumps(
            build_statistics(args.parsed_root, args.results_dir),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "discover":
        print(json.dumps(
            discover_sitemaps(
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                sources=args.sources,
                delay_seconds=args.delay,
                refresh_roots=args.refresh_roots,
                roots_only=args.roots_only,
                cache_only=args.cache_only,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "merge-inventories":
        print(json.dumps(
            merge_inventories(args.input_dirs, args.output_dir),
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
