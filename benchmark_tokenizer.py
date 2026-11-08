"""Reproducible warm-cache measurements on one immutable corpus snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import statistics
import struct
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_tokenizer import DEFAULT_BINARY
from src.token_corpus import MAGIC, snapshot_records


SOURCE_NAMES = {
    "eurogamer": "Eurogamer", "gamemag": "GameMAG", "gamingonlinux": "GamingOnLinux",
    "igromania": "Игромания", "ixbt_games": "IXBT.games", "pcgamer": "PC Gamer",
    "playground": "PlayGround", "stopgame": "StopGame",
}


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run_tokenizer(binary: Path, corpus: Path, *args: str) -> dict:
    start = time.perf_counter()
    result = subprocess.run([str(binary), "corpus", str(corpus), *args], capture_output=True, check=True)
    elapsed = (time.perf_counter() - start) * 1000
    summary = json.loads(result.stdout)
    summary["process_wall_ms"] = elapsed
    return summary


def make_samples(corpus: Path, output: Path) -> list[tuple[str, Path]]:
    """Systematic samples spread across URL-sorted sources; never duplicate text."""
    samples = []
    for stride in (8, 4, 2):
        path = output / f"sample_1_of_{stride}.bin"
        with path.open("xb") as handle:
            handle.write(MAGIC)
            for index, (document_id, source, title, text) in enumerate(snapshot_records(corpus)):
                if index % stride:
                    continue
                source_bytes = source.encode("ascii")
                handle.write(struct.pack("<QI", document_id, len(source_bytes)))
                handle.write(source_bytes)
                for field in (title, text):
                    handle.write(struct.pack("<Q", len(field)))
                    handle.write(field)
        samples.append((f"1/{stride}", path))
    return [*samples, ("all", corpus)]


def tex_number(value: float | int, precision: int = 0) -> str:
    return f"{value:,.{precision}f}".replace(",", "\\,").replace(".", ",")


def write_report_data(output: Path, benchmark: dict, full: dict) -> None:
    rows = benchmark["measurements"]
    largest = rows[-1]
    date = datetime.fromisoformat(benchmark["snapshot"]["created_at"]).astimezone(timezone(timedelta(hours=3)))
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")
    macros = {
        "ReportDate": date.strftime("%d.%m.%Y"),
        "ExperimentDate": f"{date.day} {months[date.month-1]} {date.year} года",
        "CorpusDocumentCount": str(full["documents"]),
        "CorpusDocuments": tex_number(full["documents"]),
        "CorpusTitleOnly": tex_number(full["title_only_documents"]),
        "CorpusKB": tex_number(full["total"]["input_utf8_bytes"] / 1000, 2),
        "TokenCount": tex_number(full["total"]["tokens"]),
        "MeanTokenLength": tex_number(full["total"]["mean_token_characters"], 3),
        "ProcessingMs": tex_number(largest["processing_ms_median"], 3),
        "ElapsedMs": tex_number(largest["elapsed_ms_median"], 3),
        "ThroughputKB": tex_number(largest["kb_per_second"], 1),
        "MsPerKB": tex_number(largest["ms_per_kb"], 4),
        "OutputElapsedMs": tex_number(full["elapsed_ms"], 3),
        "ProcessWallMs": tex_number(largest["process_wall_ms_median"], 3),
        "OutputWallMs": tex_number(full["process_wall_ms"], 3),
        "BenchmarkRuns": str(benchmark["runs"]),
    }
    (output / "results.tex").write_text("\n".join(
        f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in macros.items()
    ) + "\n", encoding="utf-8")
    (output / "source_rows.tex").write_text("\\newcommand{\\LabSourceRows}{%\n" + "\n".join(
        f"{SOURCE_NAMES.get(source, source)} & {full['source_documents'][source]} & "
        f"{tex_number(counts['tokens'])} & {tex_number(counts['mean_token_characters'], 3)} \\\\"
        for source, counts in sorted(full["sources"].items())
    ) + "\n}\n", encoding="utf-8")
    (output / "timing_rows.tex").write_text("\\newcommand{\\LabTimingRows}{%\n" + "\n".join(
        f"{row['documents']} & {tex_number(row['input_utf8_bytes']/1000, 2)} & "
        f"{tex_number(row['processing_ms_median'], 3)} & {tex_number(row['elapsed_ms_median'], 3)} & "
        f"{tex_number(row['kb_per_second'], 1)} \\\\"
        for row in rows
    ) + "\n}\n", encoding="utf-8")


def plot_measurements(rows: list[dict], output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sizes = [row["input_utf8_bytes"] / 1000 for row in rows]
    fig, ax = plt.subplots(figsize=(7.2, 4.1), constrained_layout=True)
    for key, label in (("processing_ms_median", "Токенизация и счётчики"),
                       ("elapsed_ms_median", "Чтение и обработка")):
        ax.plot(sizes, [row[key] for row in rows], marker="o", label=label)
    ax.set(xlabel="Объём заголовков и текста, КБ (1000 байт)", ylabel="Медианное время, мс")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.savefig(output / "timing.pdf")
    fig.savefig(output / "timing.svg")
    plt.close(fig)


def benchmark(snapshot: Path, output: Path, binary: Path, *, runs: int = 5) -> dict:
    if runs < 3:
        raise ValueError("Use at least 3 measured runs")
    snapshot, output, binary = snapshot.resolve(), output.resolve(), binary.resolve()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    corpus = snapshot / "corpus.bin"
    if file_hash(corpus) != manifest["corpus_sha256"]:
        raise ValueError("Corpus hash differs from snapshot manifest")
    build_info_path = binary.with_suffix(".build.json")
    build_info = json.loads(build_info_path.read_text(encoding="utf-8")) if build_info_path.exists() else None
    binary_hash = file_hash(binary)
    if build_info and build_info["binary_sha256"] != binary_hash:
        raise ValueError("Binary differs from recorded compiler output; rebuild")
    output.mkdir(parents=True, exist_ok=False)
    samples_dir = output / "samples"
    samples_dir.mkdir()
    measurements = []
    for label, path in make_samples(corpus, samples_dir):
        warmup = run_tokenizer(binary, path)
        trials = [run_tokenizer(binary, path) for _ in range(runs)]
        if any(trial["total"] != warmup["total"] or trial["documents"] != warmup["documents"]
               for trial in trials):
            raise RuntimeError("Non-deterministic tokenizer output")
        elapsed = statistics.median(trial["elapsed_ms"] for trial in trials)
        size_kb = warmup["total"]["input_utf8_bytes"] / 1000
        measurements.append({
            "sample": label, "documents": warmup["documents"],
            "input_utf8_bytes": warmup["total"]["input_utf8_bytes"],
            "tokens": warmup["total"]["tokens"],
            "processing_ms_median": statistics.median(t["processing_ms"] for t in trials),
            "elapsed_ms_median": elapsed,
            "process_wall_ms_median": statistics.median(t["process_wall_ms"] for t in trials),
            "elapsed_ms_min": min(t["elapsed_ms"] for t in trials),
            "elapsed_ms_max": max(t["elapsed_ms"] for t in trials),
            "kb_per_second": size_kb / (elapsed / 1000) if elapsed else 0,
            "ms_per_kb": elapsed / size_kb if size_kb else 0,
            "trials": trials,
        })
    full = run_tokenizer(binary, corpus, "--output", str(output / "tokens.tsv"))
    if (full["documents"] != manifest["documents"] or
            full["total"]["input_utf8_bytes"] != manifest["input_utf8_bytes"]):
        raise ValueError("Tokenizer counts disagree with snapshot manifest")
    if full["total"]["tokens"] != measurements[-1]["tokens"]:
        raise ValueError("Enabling token output changes token counts")
    result = {
        "snapshot": manifest, "rules_version": full["rules_version"], "runs": runs,
        "kilobyte_bytes": 1000, "warmups_per_sample": 1,
        "timing": "processing_ms includes scanner and counters; elapsed_ms also includes file reading; "
                  "measured trials do not write tokens; OS cache is warm; process startup and MongoDB export excluded",
        "sampling": "every 8th, 4th, 2nd record, then all; records are not duplicated",
        "environment": {"system": platform.system(), "release": platform.release(),
                        "machine": platform.machine(), "processor": platform.processor()},
        "binary_sha256": binary_hash, "build": build_info, "measurements": measurements,
    }
    (output / "benchmark.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "statistics.json").write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = [key for key in measurements[0] if key != "trials"]
    with (output / "benchmark.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(measurements)
    write_report_data(output, result, full)
    plot_measurements(measurements, output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new result directory")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    try:
        result = benchmark(args.snapshot, args.output, args.binary, runs=args.runs)
        print(json.dumps({"documents": result["snapshot"]["documents"],
                          "measurements": [{k: v for k, v in row.items() if k != "trials"}
                                           for row in result["measurements"]]}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[benchmark] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
