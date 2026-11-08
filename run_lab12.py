"""Build/verify Lab 12 skips and compare three execution modes on a fixed index."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

from run_lab7 import file_hash

QUERIES = ('"god of war"', "god AND of AND war", '"doom eternal"', '"eternal doom"',
           '"red dead redemption"', '"даты выхода"', "doom AND eternal", "doom AND of",
           "игры AND NOT doom", "NOT doom", "doom OR eternal", "doom", "absentxyzz AND of")
MODES = ("linear", "advance", "skips")


def run(command: list[str], data: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=data, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")+result.stdout.decode("utf-8", errors="replace"))
    return result.stdout.replace(b"\r\n", b"\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packed", type=Path, required=True)
    parser.add_argument("--skips", type=Path, required=True)
    parser.add_argument("--dict", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, default=Path("build/boolean-native"))
    parser.add_argument("--report-dir", type=Path, default=Path("doc/lab12"))
    parser.add_argument("--build", action="store_true", help="create skips in a NEW directory before verifying")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--reserve-mib", type=int, default=4608)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    target = args.report_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    skip_binary = str((args.bin_dir/("skip_index"+suffix)).resolve())
    search_binary = str((args.bin_dir/("boolean_search"+suffix)).resolve())
    base = ["--index", str((args.packed/"index").resolve()), "--positions", str((args.packed/"positions").resolve()),
            "--compact", str((args.packed/"compact").resolve())]
    result = {"date_utc": datetime.now(timezone.utc).isoformat(), "repeats": args.repeats,
              "search_sha256": file_hash(Path(search_binary)), "skip_sha256": file_hash(Path(skip_binary)),
              "dictionary_sha256": file_hash(args.dict), "queries": [],
              "method": "Each round starts a fresh process per mode, warms all queries once, then measures one batch. "
                        "Mode order rotates each round; query order alternates. OS cache retained. Timing excludes index "
                        "opening, morphology loading and metadata rendering; includes query compilation, skip point "
                        "loading and complete result enumeration. All modes use the same VB index and counters."}
    if args.build:
        args.skips.parent.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        output = run([skip_binary, "build", *base, "--output", str(args.skips.resolve()), "--reserve-mib", str(args.reserve_mib)])
        (target/"build.txt").write_bytes(output)
        result["build_seconds"] = time.perf_counter()-start
    start = time.perf_counter()
    (target/"verify.txt").write_bytes(run([skip_binary, "verify", *base, "--skips", str(args.skips.resolve())]))
    result["verify_seconds"] = time.perf_counter()-start
    print("All skip checkpoints verified", flush=True)
    samples = {(space, mode, query): [] for space in ("surface", "lemma") for mode in MODES for query in QUERIES}
    for space in ("surface", "lemma"):
        for repeat in range(args.repeats):
            order = MODES[repeat % 3:]+MODES[:repeat % 3]
            queries = QUERIES if repeat % 2 == 0 else QUERIES[::-1]
            for mode in order:
                command = [search_binary, *base, "--space", space, "--limit", "5", "--stats",
                           "--execution", "linear" if mode == "linear" else "optimized"]
                if space == "lemma":
                    command += ["--dict", str(args.dict.resolve())]
                if mode == "skips":
                    command += ["--skips", str(args.skips.resolve())]
                output = run(command, ("\n".join(queries*2)+"\n").encode("utf-8"))
                (target/f"{space}_{mode}_{repeat+1}.jsonl").write_bytes(output)
                rows = [json.loads(line) for line in output.splitlines()]
                if len(rows) != len(queries)*2:
                    raise ValueError("Unexpected response count")
                for i, query in enumerate(queries):
                    first, measured = rows[i], rows[i+len(queries)]
                    for row in (first, measured):
                        if row.get("query") != query or "stats" not in row:
                            raise ValueError("Unexpected query response")
                    if (first["total"], first["results"], first["stats"]) != (measured["total"], measured["results"], measured["stats"]):
                        raise ValueError("Query results or counters differ after warmup")
                    samples[space, mode, query].append(measured)
            print(f"{space}: round {repeat+1}/{args.repeats}", flush=True)
    for space in ("surface", "lemma"):
        for query in QUERIES:
            ref = samples[space, "linear", query][0]
            for mode in MODES:
                rows = samples[space, mode, query]
                if any((r["total"], r["results"], r["stats"]["result_digest"]) !=
                       (ref["total"], ref["results"], ref["stats"]["result_digest"]) for r in rows):
                    raise ValueError(f"Results differ: {space} {query} {mode}")
                result["queries"].append({"space": space, "mode": mode, "query": query,
                                          "total": ref["total"], "result_digest": ref["stats"]["result_digest"],
                                          "samples_ms": [r["elapsed_ms"] for r in rows],
                                          "median_ms": statistics.median(r["elapsed_ms"] for r in rows), "stats": rows[0]["stats"]})
    files = {p.name: {"bytes": p.stat().st_size, "sha256": file_hash(p)} for p in sorted(args.skips.glob("*.bin"))}
    result["files"] = files
    result["skip_bytes"] = sum(f["bytes"] for f in files.values())
    result["packed_bytes"] = sum(p.stat().st_size for p in args.packed.rglob("*.bin"))
    result["overhead_percent"] = 100*result["skip_bytes"]/result["packed_bytes"]
    (target/"summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    rows_by_key = {(r["space"], r["mode"], r["query"]): r for r in result["queries"]}
    lines = ["# ЛР12: ускорение и прыжки по индексу", "",
             f"Медианы {args.repeats} повторений после прогрева, кэш ОС не очищался. Порядок режимов чередовался.",
             "linear — алгоритм ЛР11; advance — продвижение до цели и упрощение отрицания; skips — те же оптимизации с указателями.",
             f"Размер указателей с таблицами: {result['skip_bytes']:,} байт, +{result['overhead_percent']:.2f}% к рабочему индексу.",
             "Совпали количество, первые ID и контрольная сумма полного упорядоченного потока результатов.", "",
             "| Пространство | Запрос | Совпадений | linear, мс | advance, мс | skips, мс | linear/skips | Декодировано linear/skips | Прыжков |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    tex = []
    selected = ('"god of war"', "god AND of AND war", "doom AND of", "doom AND eternal", "игры AND NOT doom", "NOT doom", "doom OR eternal")
    for space in ("surface", "lemma"):
        for query in QUERIES:
            a, b, c = (rows_by_key[space, mode, query] for mode in MODES)
            decoded = lambda r: r["stats"]["decoded_postings"]+r["stats"]["decoded_coordinates"]
            lines.append(f"| {space} | `{query}` | {a['total']} | {a['median_ms']:.3f} | {b['median_ms']:.3f} | {c['median_ms']:.3f} | "
                         f"{a['median_ms']/c['median_ms']:.2f} | {decoded(a)}/{decoded(c)} | {c['stats']['jumps']} |")
            if query in selected and space == "surface":
                label = query.replace('"', r'\textquotedbl{}')
                tex.append(f"\\texttt{{{label}}} & {a['median_ms']:.2f} & {b['median_ms']:.2f} & {c['median_ms']:.2f}\\\\\n")
    (target/"query_rows.tex").write_text("".join(tex).replace(".", ","), encoding="utf-8")
    counters = []
    for query in selected:
        a, c = (rows_by_key["surface", mode, query] for mode in ("linear", "skips"))
        label = query.replace('"', r'\textquotedbl{}')
        counters.append(f"\\texttt{{{label}}} & {a['stats']['decoded_postings']+a['stats']['decoded_coordinates']} & "
                        f"{c['stats']['decoded_postings']+c['stats']['decoded_coordinates']} & {c['stats']['jumps']}\\\\\n")
    (target/"counter_rows.tex").write_text("".join(counters), encoding="utf-8")
    god_a, god_c = (rows_by_key["surface", mode, '"god of war"'] for mode in ("linear", "skips"))
    macros = {"LabTwelveSkipMB": f"{result['skip_bytes']/1e6:.2f}", "LabTwelveOverhead": f"{result['overhead_percent']:.1f}",
              "LabTwelveRepeats": str(args.repeats), "LabTwelveGodLinear": f"{god_a['median_ms']:.2f}",
              "LabTwelveGodSkips": f"{god_c['median_ms']:.2f}", "LabTwelveGodSpeedup": f"{god_a['median_ms']/god_c['median_ms']:.2f}"}
    (target/"report_data.tex").write_text("".join(f"\\newcommand{{\\{k}}}{{{v.replace('.', ',')}}}\n" for k, v in macros.items()), encoding="utf-8")
    lines += ["", "Счётчики измеряют логически декодированные записи, а не физическое чтение накопителя. Пропущенные записи не декодировались.",
              "Время включает загрузку указателей нужных терминов; весь индекс в память не загружается.",
              "Для NOT требуется перечислить всё дополнение, а OR не может пропускать потенциальные результаты."]
    (target/"README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
