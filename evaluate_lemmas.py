"""Controlled retrieval evaluation; native C++ matching, Python preparation/metrics."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import math
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from src.token_corpus import MAGIC, snapshot_records

MODES = ("exact", "lemma", "hybrid")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def metrics(ids: list[int], relevant: dict[int, int], k: int) -> dict[str, float]:
    if k < 1 or len(ids) != len(set(ids)):
        raise ValueError("Invalid cutoff or duplicate result IDs")
    values = [relevant.get(i, 0) for i in ids[:k]]
    hits = sum(value > 0 for value in values)
    total = sum(value > 0 for value in relevant.values())
    def dcg(grades):
        return sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))
    ideal = dcg(sorted(relevant.values(), reverse=True)[:k])
    return {"precision": hits / k, "recall": hits / total if total else 0.0,
            "ndcg": dcg(values) / ideal if ideal else 0.0}


def validate_judgments(judgments: dict) -> None:
    ids = judgments["document_ids"]
    if (not ids or len(ids) != len(set(ids)) or any(type(i) is not int or i < 1 for i in ids)
            or type(judgments["k"]) is not int or not 1 <= judgments["k"] <= 100):
        raise ValueError("Invalid document IDs/cutoff")
    if judgments["field"] not in {"title", "both"}:
        raise ValueError("Invalid evaluation field")
    query_ids = set()
    if not judgments["queries"]:
        raise ValueError("No queries")
    for q in judgments["queries"]:
        if (q["id"] in query_ids or not q["text"].strip() or
                any(c in q["text"] for c in "\t\r\n\0")):
            raise ValueError("Duplicate or malformed query")
        query_ids.add(q["id"])
        for i, grade in q["relevant"].items():
            if int(i) not in ids or type(grade) is not int or not 0 <= grade <= 3:
                raise ValueError("Invalid relevance judgment")


def evaluate(snapshot: Path, dictionary: Path, binary: Path, judgments_path: Path, output: Path) -> dict:
    judgments = json.loads(judgments_path.read_text(encoding="utf-8"))
    validate_judgments(judgments)
    snapshot_hash = digest(snapshot / "corpus.bin")
    if snapshot_hash != judgments["snapshot_sha256"]:
        raise ValueError("Judgments belong to a DIFFERENT snapshot; do not silently reuse document IDs")
    dictionary_meta = json.loads(dictionary.with_name("manifest.json").read_text(encoding="utf-8"))
    if digest(dictionary) != dictionary_meta["sha256"]:
        raise ValueError("Dictionary checksum mismatch")
    output.mkdir(parents=True, exist_ok=False)
    metadata = {}
    for line in (snapshot / "documents.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        if row["document_id"] in judgments["document_ids"]:
            metadata[row["document_id"]] = row
    wanted = set(judgments["document_ids"])
    selected = {}
    with (output / "corpus.bin").open("xb") as stream:
        stream.write(MAGIC)
        for i, source, title, text in snapshot_records(snapshot / "corpus.bin"):
            if i not in wanted:
                continue
            selected[i] = {"id": i, "source": source, "title": title.decode(),
                           "url": metadata[i]["url"]}
            encoded = source.encode("ascii")
            stream.write(struct.pack("<QI", i, len(encoded)) + encoded)
            for value in (title, text if judgments["field"] == "both" else b""):
                stream.write(struct.pack("<Q", len(value)) + value)
    if set(selected) != wanted:
        raise ValueError("Judged documents missing from snapshot")
    queries_path = output / "queries.tsv"
    queries_path.write_text("".join(q["id"] + "\t" + q["text"] + "\n" for q in judgments["queries"]), encoding="utf-8")
    command = [str(binary.resolve()), "--corpus", str((output / "corpus.bin").resolve()),
               "--dict", str(dictionary.resolve()), "--queries", str(queries_path.resolve()),
               "--field", judgments["field"], "--top-k", str(judgments["k"])]
    run = subprocess.run(command, capture_output=True)
    (output / "search.log").write_bytes(run.stderr)
    if run.returncode:
        raise RuntimeError(run.stderr.decode("utf-8", errors="replace"))
    (output / "rankings.tsv").write_bytes(run.stdout)
    rankings = {q["id"]: {mode: [] for mode in MODES} for q in judgments["queries"]}
    for row in csv.DictReader(io.StringIO(run.stdout.decode("utf-8")), delimiter="\t"):
        result = rankings[row["query_id"]][row["mode"]]
        i = int(row["document_id"])
        if i not in wanted or int(row["rank"]) != len(result) + 1 or i in result:
            raise ValueError("Invalid native ranking")
        result.append(i)
    queries = []
    for q in judgments["queries"]:
        relevant = {int(i): grade for i, grade in q["relevant"].items()}
        scores = {mode: metrics(rankings[q["id"]][mode], relevant, judgments["k"]) for mode in MODES}
        queries.append({**q, "rankings": rankings[q["id"]], "metrics": scores})
    macro = {mode: {name: sum(q["metrics"][mode][name] for q in queries) / len(queries)
                    for name in ("precision", "recall", "ndcg")} for mode in MODES}
    result = {
        "created_at": datetime.now(UTC).isoformat(), "judgments_version": judgments["version"],
        "snapshot_sha256": snapshot_hash, "dictionary_sha256": dictionary_meta["sha256"],
        "binary_sha256": digest(binary), "judgments_sha256": digest(judgments_path),
        "field": judgments["field"], "k": judgments["k"], "pool_documents": len(selected),
        "selection": judgments["selection"], "judgment_method": judgments["judgment_method"],
        "ranking": "AND; title coverage; ties by document ID; hybrid adds original-token coverage only inside a title-coverage tie",
        "macro": macro, "queries": queries, "documents": list(selected.values()),
        "limitation": "Small deliberately diagnostic title-only pool, provisional author labels; not a representative production evaluation.",
    }
    (output / "quality.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(macro, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--dict", dest="dictionary", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=Path("build/lemma-native/lemma_search.exe"))
    parser.add_argument("--judgments", type=Path, default=Path("tests/fixtures/lab4_judgments.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        evaluate(args.snapshot, args.dictionary, args.binary, args.judgments, args.output)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[evaluate-lemmas] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
