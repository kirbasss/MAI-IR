"""Refresh sitemap URLs without replacing a usable inventory on failure."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.discovery import discover_sitemaps, merge_inventories
from src.downloader import SOURCE_HOSTS
from src.utils import dump_json


def refresh_inventory(
    *, data_dir: Path, inventory_path: Path, sources: list[str],
    delay_seconds: float = 1.0,
) -> dict:
    """Fetch selected sources, union them with old URLs, publish atomically."""
    if inventory_path.name != "url_inventory.tsv":
        raise ValueError("Ожидается путь к файлу url_inventory.tsv")
    if not sources or len(sources) != len(set(sources)):
        raise ValueError("Укажите непустой список источников без повторов")
    unknown = set(sources) - set(SOURCE_HOSTS)
    if unknown:
        raise ValueError(f"Источник не настроен для обкачки: {', '.join(sorted(unknown))}")
    if delay_seconds < 0:
        raise ValueError("Пауза между sitemap не может быть отрицательной")

    output_dir = inventory_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    old_count = 0
    if inventory_path.is_file():
        with inventory_path.open(encoding="utf-8", newline="") as stream:
            old_count = max(0, sum(1 for _ in stream) - 1)
    with tempfile.TemporaryDirectory(prefix=".inventory-refresh-", dir=output_dir) as temporary:
        stage = Path(temporary)
        inputs = [output_dir] if inventory_path.is_file() else []
        source_counts: dict[str, int] = {}
        refresh_errors: dict[str, list] = {}
        for source in sources:
            source_dir = stage / source
            summary = discover_sitemaps(
                data_dir=data_dir, output_dir=source_dir, sources=[source],
                delay_seconds=delay_seconds, refresh_roots=True,
            )
            count = summary["sources"][source]["urls"]
            if summary["errors"] or count == 0:
                refresh_errors[source] = summary["errors"] or [
                    {"error": "Sitemap не содержит документов"}
                ]
                continue
            source_counts[source] = count
            inputs.append(source_dir)

        if not source_counts:
            raise RuntimeError(f"Не обновлён ни один источник: {refresh_errors}")

        merged_dir = stage / "merged"
        summary = merge_inventories(inputs, merged_dir)
        if summary["errors"] or summary["total_unique_urls"] == 0:
            raise RuntimeError("Объединённый инвентарь пуст или содержит ошибки")
        # A corrected alias may collapse with an already canonical URL.
        # That is a repair, not a loss of a publication.
        added = max(0, summary["total_unique_urls"] - old_count)

        summary["input_inventories"] = (
            ([str(inventory_path)] if inventory_path.is_file() else [])
            + [f"sitemap:{source}" for source in source_counts]
        )
        summary["attempted_sources"] = sources
        summary["refreshed_sources"] = source_counts
        summary["refresh_errors"] = refresh_errors
        summary["new_inventory_urls"] = added
        dump_json(merged_dir / "summary.json", summary)

        # The TSV is the crawler's source of truth. Publishing it last keeps
        # the old queue input intact until the entire refresh succeeds.
        os.replace(merged_dir / "summary.json", output_dir / "summary.json")
        if added or summary["normalized_urls"]:
            os.replace(merged_dir / "url_inventory.tsv", inventory_path)
        return summary
