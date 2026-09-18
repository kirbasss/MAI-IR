from __future__ import annotations

import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path


WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _mean(values: list[int]) -> float:
    return float(statistics.mean(values)) if values else 0.0


def _median(values: list[int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def summarize(docs: list[dict]) -> dict:
    raw_sizes = [int(doc.get("raw_size_bytes", 0)) for doc in docs]
    text_sizes = [int(doc.get("text_size_bytes", 0)) for doc in docs]
    words = [word_count(str(doc.get("text", ""))) for doc in docs]
    hashes = [doc["text_sha256"] for doc in docs if doc.get("text_sha256")]
    raw_total = sum(raw_sizes)
    text_total = sum(text_sizes)

    return {
        "documents": len(docs),
        "parsed_ok": sum(
            bool(doc.get("text")) and not doc.get("parse_error") for doc in docs
        ),
        "parse_errors": sum(bool(doc.get("parse_error")) for doc in docs),
        "raw_bytes_total": raw_total,
        "text_bytes_total": text_total,
        "avg_raw_bytes": _mean(raw_sizes),
        "avg_text_bytes": _mean(text_sizes),
        "median_raw_bytes": _median(raw_sizes),
        "median_text_bytes": _median(text_sizes),
        "min_text_bytes": min(text_sizes, default=0),
        "max_text_bytes": max(text_sizes, default=0),
        "words_total": sum(words),
        "avg_words": _mean(words),
        "median_words": _median(words),
        "min_words": min(words, default=0),
        "max_words": max(words, default=0),
        "extraction_ratio": text_total / raw_total if raw_total else 0.0,
        "documents_with_hash": len(hashes),
        "unique_texts": len(set(hashes)),
        "duplicate_documents": len(hashes) - len(set(hashes)),
    }


def load_documents(parsed_root: Path) -> list[dict]:
    result: list[dict] = []
    for path in sorted(parsed_root.rglob("*.json")):
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] Не удалось прочитать {path}: {exc}")
    return result


def build_statistics(
    parsed_root: Path = Path("data/parsed"),
    results_dir: Path = Path("results"),
) -> dict:
    docs = load_documents(parsed_root)
    groups: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        groups[str(doc.get("source", "unknown"))].append(doc)

    result = {
        "all": summarize(docs),
        "sources": {source: summarize(items) for source, items in sorted(groups.items())},
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "statistics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = [{"source": "ALL", **result["all"]}]
    rows.extend(
        {"source": source, **stats}
        for source, stats in result["sources"].items()
    )
    with (results_dir / "statistics.csv").open(
        "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return result
