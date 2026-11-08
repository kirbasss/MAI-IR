"""Measure Lab 11 list reading, decoding and complete queries independently."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys

from run_lab7 import file_hash

TERMS = ("god", "of", "war")
QUERIES = ('"god of war"', "god AND of AND war", '"doom eternal"', '"eternal doom"')
TIMINGS = ("raw_read_ms", "vb_read_ms", "raw_decode_ms", "vb_decode_ms", "raw_cursor_ms", "vb_cursor_ms")


def execute(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, timeout=300, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("index", "positions", "compact", "packed", "dict"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, default=Path("build/boolean-native"))
    parser.add_argument("--report-dir", type=Path, default=Path("doc/lab11/profile"))
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 31:
        parser.error("--repeats must be 1..31")
    target = args.report_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    compact_binary = str((args.bin_dir/("compact_index"+suffix)).resolve())
    search_binary = str((args.bin_dir/("boolean_search"+suffix)).resolve())
    base = ["--index", str(args.index.resolve()), "--positions", str(args.positions.resolve())]
    summary = {
        "date_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "repeats": args.repeats, "compact_binary_sha256": file_hash(Path(compact_binary)),
        "search_binary_sha256": file_hash(Path(search_binary)), "lists": [], "search": [],
        "method": "One untimed warm-up per list, one discarded query batch per process; "
                  "OS cache retained. Raw/VB list order alternates per repetition. Buffers allocated "
                  "and touched before timing. Memory decoding includes reconstruction and an identical "
                  "rolling checksum. Cursor scans use production FILE readers, including opening and closing. "
                  "Complete list scans are a separate workload, not an additive query-time breakdown.",
    }
    for space in ("surface", "lemma"):
        for term in TERMS:
            command = [compact_binary, "benchmark", *base, "--compact", str(args.compact.resolve()),
                       "--term", term, "--space", space, "--repeats", str(args.repeats)]
            output = execute(command).stdout
            (target/f"{space}_{term}.jsonl").write_bytes(output)
            rows = [json.loads(line) for line in output.splitlines()]
            for kind in ("postings", "coordinates"):
                samples = [row for row in rows if row["kind"] == kind]
                if len(samples) != args.repeats or len({s["checksum"] for s in samples}) != 1:
                    raise ValueError("Unexpected benchmark samples")
                entry = {"space": space, "term": term, "kind": kind,
                         **{k: samples[0][k] for k in ("count", "raw_bytes", "vb_bytes", "checksum")},
                         "samples": samples, "median": {key: statistics.median(s[key] for s in samples) for key in TIMINGS}}
                summary["lists"].append(entry)
            print(f"List measurements complete: {space} {term}", flush=True)
        for variant in (("raw", "packed") if space == "surface" else ("packed", "raw")):
            command = [search_binary]
            if variant == "raw":
                command += base
            else:
                command += ["--index", str((args.packed/"index").resolve()),
                            "--positions", str((args.packed/"positions").resolve()),
                            "--compact", str((args.packed/"compact").resolve())]
            command += ["--space", space, "--limit", "5"]
            if space == "lemma":
                command += ["--dict", str(args.dict.resolve())]
            output = execute(command, input=("\n".join(QUERIES*(args.repeats+1))+"\n").encode("utf-8")).stdout
            (target/f"{space}_{variant}_queries.jsonl").write_bytes(output)
            rows = [json.loads(line) for line in output.splitlines()]
            if len(rows) != len(QUERIES)*(args.repeats+1):
                raise ValueError("Unexpected query response count")
            for i, query in enumerate(QUERIES):
                samples = rows[i::len(QUERIES)]
                if any(s["query"] != query or s["total"] != samples[0]["total"] or s["results"] != samples[0]["results"] for s in samples):
                    raise ValueError("Query responses differ between repetitions")
                summary["search"].append({"space": space, "variant": variant, "query": query,
                                          "total": samples[0]["total"], "results": samples[0]["results"],
                                          "warmup_ms": samples[0]["elapsed_ms"],
                                          "samples_ms": [s["elapsed_ms"] for s in samples[1:]],
                                          "median_ms": statistics.median(s["elapsed_ms"] for s in samples[1:])})
            print(f"Query measurements complete: {space} {variant}", flush=True)
    search = {(s["space"], s["variant"], s["query"]): s for s in summary["search"]}
    for space in ("surface", "lemma"):
        for query in QUERIES:
            a, b = (search[space, variant, query] for variant in ("raw", "packed"))
            if (a["total"], a["results"]) != (b["total"], b["results"]):
                raise ValueError("Raw and packed query responses differ")
    (target/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# ЛР11: чтение, декодирование и поиск", "",
             f"Медианы {args.repeats} повторений после прогрева; кэш ОС не очищался.",
             "Время в мс. Чтение означает seek + fread в заранее выделенный и заполненный буфер.",
             "Декодирование выполняется в памяти, включая восстановление значений и одинаковую для обоих форматов контрольную сумму.",
             "Курсор — полный проход штатного FILE-читателя с открытием и закрытием потока.", "",
             "| Пространство | Термин | Список | Количество | МБ исходн./VB | Чтение исходн./VB | Декодирование исходн./VB | Курсор исходн./VB |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for row in summary["lists"]:
        m = row["median"]
        lines.append(f"| {row['space']} | {row['term']} | {row['kind']} | {row['count']} | "
                     f"{row['raw_bytes']/1e6:.2f}/{row['vb_bytes']/1e6:.2f} | "
                     f"{m['raw_read_ms']:.3f}/{m['vb_read_ms']:.3f} | "
                     f"{m['raw_decode_ms']:.3f}/{m['vb_decode_ms']:.3f} | "
                     f"{m['raw_cursor_ms']:.3f}/{m['vb_cursor_ms']:.3f} |")
    lines += ["", "| Пространство | Запрос | Совпадений | Исходный, мс | Рабочая копия VB, мс |", "|---|---|---:|---:|---:|"]
    tex = []
    for space in ("surface", "lemma"):
        for query in QUERIES:
            a, b = (search[space, variant, query] for variant in ("raw", "packed"))
            lines.append(f"| {space} | `{query}` | {a['total']} | {a['median_ms']:.3f} | {b['median_ms']:.3f} |")
            label = "словоформы" if space == "surface" else "леммы"
            query_tex = query.replace(chr(34), r'\textquotedbl{}')
            tex.append(f"{label} & \\texttt{{{query_tex}}} & "
                       f"{a['median_ms']:.2f} & {b['median_ms']:.2f}\\\\\n")
    (target/"query_rows.tex").write_text("".join(tex).replace(".", ","), encoding="utf-8")
    tex, cursors = [], []
    for row in summary["lists"]:
        if row["space"] != "surface" or row["kind"] != "coordinates":
            continue
        m = row["median"]
        numbers = " & ".join(f"{m[k]:.2f}".replace(".", ",") for k in TIMINGS[:4])
        tex.append(f"\\texttt{{{row['term']}}} & {numbers}\\\\\n")
        cursors.append(f"\\texttt{{{row['term']}}} & {m['raw_cursor_ms']:.2f} & {m['vb_cursor_ms']:.2f}\\\\\n")
    (target/"decode_rows.tex").write_text("".join(tex), encoding="utf-8")
    (target/"cursor_rows.tex").write_text("".join(cursors).replace(".", ","), encoding="utf-8")
    god_raw = search["surface", "raw", '"god of war"']["median_ms"]
    god_vb = search["surface", "packed", '"god of war"']["median_ms"]
    macros = {"ProfileRepeats": str(args.repeats), "ProfileGodRaw": f"{god_raw:.2f}",
              "ProfileGodVB": f"{god_vb:.2f}", "ProfileGodSlower": f"{100*(god_vb/god_raw-1):.1f}"}
    (target/"report_data.tex").write_text("".join(
        f"\\newcommand{{\\{name}}}{{{value.replace('.', ',')}}}\n" for name, value in macros.items()), encoding="utf-8")
    lines += ["", "Полные проходы списков — отдельная нагрузка: их время нельзя складывать с временем фразового запроса или вычитать из него.",
              "Фразовый поиск может читать только часть каждого списка и дополнительно выполняет пересечение координат.",
              "Время запроса включает компиляцию и полное перечисление совпадений, исключает открытие индекса, загрузку морфологии и вывод метаданных.",
              "Сырые и сжатые результаты поиска, а также контрольные суммы всех восстановленных списков совпали."]
    (target/"README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
