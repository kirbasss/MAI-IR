"""Reproduce Lab 13: OR timing, AND first results, and TF-IDF ranking."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parent
QUERIES = ("doom eternal", "god of war", "cyberpunk 2077", "baldur gate", "игры", "игрой", "несуществующийтерминxyz")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(command, queries):
    start = time.perf_counter()
    result = subprocess.run(command, input=("\n".join(queries)+"\n").encode(), capture_output=True, check=True)
    rows = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    if len(rows) != len(queries) or any("error" in row for row in rows):
        raise ValueError("Invalid native response")
    return rows, (time.perf_counter()-start)*1000


def evaluate_titles(rows, judgments):
    """Precision at 5 on the explicitly judged AND/TF-IDF title pool."""
    results = []
    for topic in judgments["topics"]:
        row = next(row for row in rows if row["space"] == "surface" and row["query"] == topic["query"])
        labels = {entry["id"]: entry for entry in topic["judgments"]}
        result = {"query": topic["query"]}
        for mode in ("and", "tfidf"):
            top = row[mode+"_top"][:5]
            for doc in top:
                if labels[doc["id"]]["title"] != doc["title"]:
                    raise ValueError("Judgment title differs from result")
            result[mode+"_p5"] = sum(labels[doc["id"]]["grade"] for doc in top)/5
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packed", type=Path, default=ROOT/"data/compact/lab11_vb_packed_2026-11-08")
    parser.add_argument("--norms", type=Path, default=ROOT/"data/tfidf/lab13_2026-11-08")
    parser.add_argument("--dict", type=Path, default=ROOT/"data/lemmas/ru_en_v1/dictionary.bin")
    parser.add_argument("--binary", type=Path, default=ROOT/"build/boolean-native/boolean_search.exe")
    parser.add_argument("--output", type=Path, default=ROOT/"doc/lab13")
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    base = [str(args.binary), "--index", str(args.packed/"index"), "--positions", str(args.packed/"positions"),
            "--compact", str(args.packed/"compact"), "--limit", "10"]
    data, summary = [], []
    for space in ("surface", "lemma"):
        commands = {}
        for mode in ("boolean", "tfidf"):
            command = base + ["--space", space]
            if space == "lemma":
                command += ["--dict", str(args.dict)]
            if mode == "tfidf":
                command += ["--ranking", "tfidf", "--norms", str(args.norms)]
            commands[mode] = command
        samples = {(mode, query): [] for mode in commands for query in QUERIES}
        reference = {}
        for round_index in range(args.repeats):
            for mode in (("boolean", "tfidf") if round_index % 2 == 0 else ("tfidf", "boolean")):
                ordered = list(QUERIES if round_index % 2 == 0 else reversed(QUERIES))
                native_queries = [" OR ".join(query.split()) if mode == "boolean" else query for query in ordered]
                # Same process: one warm-up batch, then one measured batch.
                rows, wall_ms = run(commands[mode], native_queries+native_queries)
                measured = rows[len(ordered):]
                for query, row in zip(ordered, measured):
                    samples[mode, query].append(row["elapsed_ms"])
                    signature = (row["total"], row["results"])
                    key = (mode, query)
                    if key in reference and reference[key] != signature:
                        raise ValueError("Results changed between repetitions")
                    reference[key] = signature
                    data.append({"round": round_index, "mode": mode, "space": space,
                                 "plain_query": query, "process_wall_ms": wall_ms, **row})
        for query in QUERIES:
            baseline, ranked = reference["boolean", query], reference["tfidf", query]
            # These fixture terms have 0 < df < N, so both modes enumerate the same union.
            if baseline[0] != ranked[0]:
                raise ValueError("OR candidates differ from positive-score candidates")
            summary.append({"query": query, "space": space, "total": ranked[0],
                            "boolean_ms": statistics.median(samples["boolean", query]),
                            "tfidf_ms": statistics.median(samples["tfidf", query]),
                            "boolean_top": baseline[1], "tfidf_top": ranked[1]})
        # AND is the relevance illustration: it imposes a stricter condition
        # than either OR or positive TF-IDF scores, so its total is separate.
        and_queries = [" AND ".join(query.split()) for query in QUERIES]
        and_rows, _ = run(commands["boolean"], and_queries)
        for entry, row in zip(summary[-len(QUERIES):], and_rows):
            entry["and_query"] = row["query"]
            entry["and_total"] = row["total"]
            entry["and_top"] = row["results"]
    (args.output/"queries.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in data), encoding="utf-8")
    files = [args.binary, args.norms/"norms.bin", args.dict]
    files += sorted(p for p in args.packed.rglob("*.bin"))
    report = {"repeats": args.repeats, "queries": summary, "hashes": {str(p): sha256(p) for p in files},
              "norm_bytes": (args.norms/"norms.bin").stat().st_size,
              "timing": "Median after a warm-up batch in each fresh process; OS cache retained; native time excludes opening indices, loading morphology/norms, metadata and JSON output."}
    judgments = ROOT/"tests/fixtures/lab13_title_judgments.json"
    if judgments.is_file():
        report["title_topic_precision"] = evaluate_titles(summary, json.loads(judgments.read_text(encoding="utf-8")))
    (args.output/"summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    tex_rows = [f"{row['query']} & {row['total']:,} & {row['boolean_ms']:.3f} & {row['tfidf_ms']:.3f} \\\\".replace(",", r"\,")
                for row in summary if row["space"] == "surface"]
    (args.output/"query_rows.tex").write_text("\n".join(tex_rows)+"\n", encoding="utf-8")
    print(f"Saved {len(data)} measurements to {args.output}")


if __name__ == "__main__":
    main()
