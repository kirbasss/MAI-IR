"""Create a reproducible, source-balanced pilot from a sitemap inventory."""

from __future__ import annotations

import csv
import os
import random
import tempfile
from collections import Counter
from pathlib import Path

from src.downloader import SOURCE_HOSTS


FIELDS = ["source", "category", "url", "sitemap_url"]


def sample_inventory(input_path: Path, output_path: Path, *, per_source: int,
                     seed: int = 42) -> dict:
    """Reservoir-sample at most ``per_source`` URLs per source in one pass."""
    input_path, output_path = input_path.resolve(), output_path.resolve()
    if input_path == output_path:
        raise ValueError("Входной и выходной инвентарь должны быть разными файлами.")
    if per_source < 1:
        raise ValueError("--per-source должен быть положительным числом.")
    if not input_path.is_file():
        raise ValueError(f"Инвентарь не найден: {input_path}")

    rng = random.Random(seed)
    seen: Counter[str] = Counter()
    samples: dict[str, list[dict[str, str]]] = {}
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != FIELDS:
            raise ValueError(f"Ожидаются колонки TSV: {', '.join(FIELDS)}")
        for line_no, row in enumerate(reader, 2):
            source = row.get("source", "").strip()
            if (source not in SOURCE_HOSTS or not row.get("url") or None in row
                    or any(row.get(field) is None for field in FIELDS)):
                raise ValueError(f"Некорректная строка инвентаря: {input_path}:{line_no}")
            seen[source] += 1
            bucket = samples.setdefault(source, [])
            if len(bucket) < per_source:
                bucket.append(row)
            else:
                index = rng.randrange(seen[source])
                if index < per_source:
                    bucket[index] = row
    if not seen:
        raise ValueError(f"Инвентарь пуст: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", delete=False,
            dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp",
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            for source in sorted(samples):
                for row in sorted(samples[source], key=lambda item: item["url"]):
                    writer.writerow(row)
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return {
        "input": str(input_path), "output": str(output_path), "seed": seed,
        "per_source": per_source, "input_rows": sum(seen.values()),
        "selected_rows": sum(map(len, samples.values())),
        "sources": {
            source: {"available": seen[source], "selected": len(samples[source])}
            for source in sorted(samples)
        },
    }
