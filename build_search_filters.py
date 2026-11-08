"""Build a compact source/type/publication-year catalog for a MAIIR3 snapshot."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct

SOURCES = ("gamemag", "eurogamer", "gamingonlinux", "igromania", "ixbt_games", "pcgamer", "playground", "stopgame")
TYPES = ("news", "article", "review", "blog", "unknown")
DATE = re.compile(r"^(19\d{2}|20\d{2}|2100)(?:-|$)")


def publication_year(value) -> int:
    if not isinstance(value, str):
        return 0
    match = DATE.match(value)
    return int(match.group(1)) if match else 0


def build(snapshot: Path, output: Path, publications=()) -> dict:
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    count = manifest["documents"]
    if manifest["format"] != "MAIIR3-v1" or not isinstance(count, int) or count < 1:
        raise ValueError("Expected MAIIR3 snapshot manifest")
    if output.exists():
        raise FileExistsError(f"Choose a new filter file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    years: Counter[int] = Counter()
    types: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    matched = 0
    publication = iter(publications)
    current = next(publication, None)
    try:
        with (snapshot / "documents.jsonl").open(encoding="utf-8") as lines, temporary.open("wb") as target:
            target.write(b"MAIFLT1\0" + struct.pack("<Q", count))
            for expected, line in enumerate(lines, 1):
                row = json.loads(line)
                if row["document_id"] != expected:
                    raise ValueError("Snapshot document IDs are not contiguous")
                source, kind = row["source"], row["publication_type"]
                if source not in SOURCES or kind not in TYPES:
                    raise ValueError("Unexpected source or publication type")
                url = row["url"]
                while current is not None and current["_id"] < url:
                    current = next(publication, None)
                year = publication_year(row.get("published_at"))
                if current is not None and current["_id"] == url and current.get("content_sha256") == row.get("content_sha256"):
                    year = publication_year((current.get("parsed") or {}).get("published_at")) or year
                    matched += 1
                target.write(struct.pack("<HBB", year, TYPES.index(kind) + 1, SOURCES.index(source) + 1))
                sources[source] += 1
                types[kind] += 1
                if year:
                    years[year] += 1
            if expected != count:
                raise ValueError("Snapshot document count mismatch")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {"documents": count, "publication_years": dict(sorted(years.items())),
            "sources": dict(sorted(sources.items())), "publication_types": dict(sorted(types.items())),
            "matched_database_documents": matched, "without_publication_year": count - sum(years.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="crawler YAML to read published_at from MongoDB")
    args = parser.parse_args()
    if args.config:
        import yaml
        from pymongo import MongoClient
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))["db"]
        with MongoClient(config["uri"], serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            cursor = client[config["name"]].documents.find({},
                {"_id": 1, "content_sha256": 1, "parsed.published_at": 1}).sort("_id", 1).batch_size(1000)
            result = build(args.snapshot, args.output, cursor)
    else:
        result = build(args.snapshot, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
