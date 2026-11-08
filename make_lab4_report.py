"""Generate small report artifacts from actual benchmark, judgments and test results."""
import argparse
import csv
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path


def escape(value):
    replacements = {"\\": r"\textbackslash{}", "_": r"\_", "&": r"\&", "%": r"\%", "#": r"\#", "$": r"\$", "{": r"\{", "}": r"\}"}
    return "".join(replacements.get(c, c) for c in str(value))


def number(value, digits=0):
    return f"{value:,.{digits}f}".replace(",", r"\,").replace(".", ",")


def make_token_examples(binary: Path, dictionary: Path, target: Path):
    """Render actual CLI answers; never maintain a second morphology implementation."""
    source = Path(__file__).resolve().parent / "tests/fixtures/lab4_demo.txt"
    raw = source.read_bytes()
    dictionary_hash = hashlib.sha256(dictionary.read_bytes()).hexdigest()
    manifest = json.loads(dictionary.with_name("manifest.json").read_text(encoding="utf-8"))
    if dictionary_hash != manifest["sha256"]:
        raise ValueError("Demo dictionary checksum differs from manifest")
    command = [str(binary.resolve()), "text", "--dict", str(dictionary.resolve()), "--input", str(source)]
    result = subprocess.run(command, capture_output=True, check=True)
    stats = json.loads(result.stderr.decode("utf-8"))
    rows = []
    records = list(csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"))
    if len(records) != stats["tokens"]:
        raise ValueError("Demo TSV and statistics disagree")
    for row in records:
        original = raw[int(row["begin_byte"]):int(row["end_byte"])].decode("utf-8")
        values = (original, row["term"], row["lemmas"].replace("|", " | "),
                  "да" if row["known"] == "1" else "нет")
        rows.append(" & ".join(escape(value) for value in values) + r" \\")
    command[1] = "demo"
    demo = subprocess.run(command, capture_output=True, check=True)
    demo_stats = json.loads(demo.stderr.decode("utf-8"))
    for key in ("tokens", "known_tokens", "changed_tokens", "ambiguous_tokens", "emitted_lemmas"):
        if stats[key] != demo_stats[key]:
            raise ValueError("Demo table and TSV analysis disagree: " + key)
    (target / "token_examples.tex").write_text(
        "\\newcommand{\\LabFourTokenRows}{%\n" + "\n".join(rows) + "\n}\n", encoding="utf-8")
    (target / "token_examples.tsv").write_bytes(result.stdout)
    (target / "token_demo.txt").write_bytes(demo.stdout)
    (target / "token_demo.json").write_text(json.dumps({
        "input": source.relative_to(Path(__file__).resolve().parent).as_posix(),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "dictionary_sha256": dictionary_hash,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "statistics": stats,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make(benchmark: Path, quality: Path, checks: Path, snapshot: Path, dictionary: Path, target: Path):
    b = json.loads(benchmark.read_text(encoding="utf-8"))
    q = json.loads(quality.read_text(encoding="utf-8"))
    tests = json.loads((checks / "tests_summary.json").read_text(encoding="utf-8"))
    s = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    d = json.loads(dictionary.with_name("manifest.json").read_text(encoding="utf-8"))
    if (b["snapshot_sha256"] != q["snapshot_sha256"] or b["snapshot_sha256"] != s["corpus_sha256"]
            or b["dictionary_sha256"] != q["dictionary_sha256"] or b["dictionary_sha256"] != d["sha256"]):
        raise ValueError("Do not mix report inputs from different snapshots/dictionaries")
    target.mkdir(parents=True, exist_ok=True)
    last = b["measurements"][-1]
    stats = last["samples"][0]
    macros = {
        "ReportDate": "22.10.2026", "CorpusDocuments": number(s["documents"]),
        "CorpusMB": number(s["input_utf8_bytes"] / 1e6, 2), "TokenCount": number(stats["tokens"]),
        "KnownPercent": number(100 * stats["known_tokens"] / stats["tokens"], 2),
        "ChangedTokens": number(stats["changed_tokens"]), "AmbiguousTokens": number(stats["ambiguous_tokens"]),
        "DictionaryEntries": number(d["entries"]), "DictionaryMB": number(d["bytes"] / 1e6, 2),
        "DictionaryMemoryMB": number(stats["dictionary_memory_bytes"] / 1e6, 2),
        "ProcessingMs": number(last["median_processing_ms"], 2),
        "ElapsedMs": number(last["median_elapsed_ms"], 2), "LoadMs": number(last["median_load_ms"], 2),
        "TokensPerSecond": number(last["tokens_per_second"]), "PoolDocuments": number(q["pool_documents"]),
        "QueryCount": number(len(q["queries"])),
        "CppChecks": number(tests.get("ctest_checks_lab4", tests.get("ctest_checks_lab3_and_lab4"))),
        "PythonChecks": number(tests["python_preparation_and_metrics_checks"]),
        "BenchmarkRuns": number(b["runs"]),
    }
    (target / "results.tex").write_text("".join("\\newcommand{\\" + key + "}{" + value + "}\n" for key, value in macros.items()), encoding="utf-8")
    labels = {"exact": "Точные токены", "lemma": "Леммы", "hybrid": "Леммы + точное совпадение"}
    summary = []
    for mode in ("exact", "lemma", "hybrid"):
        summary.append(labels[mode] + " & " + " & ".join(number(q["macro"][mode][name], 3) for name in ("precision", "recall", "ndcg")) + r" \\")
    (target / "quality_summary.tex").write_text("\\newcommand{\\LabFourQualitySummary}{%\n" + "\n".join(summary) + "\n}\n", encoding="utf-8")
    rows = []
    for query in q["queries"]:
        rows.append(escape(query["text"]) + " & " + " & ".join(number(query["metrics"][mode]["ndcg"], 3) for mode in ("exact", "lemma", "hybrid")) + r" \\")
    (target / "quality_rows.tex").write_text("\\newcommand{\\LabFourQualityRows}{%\n" + "\n".join(rows) + "\n}\n", encoding="utf-8")
    timing = []
    for row in b["measurements"]:
        timing.append(number(row["limit"]) + " & " + number(row["samples"][0]["input_utf8_bytes"] / 1e6, 2) + " & " +
                      number(row["median_processing_ms"], 2) + " & " + number(row["median_load_ms"], 2) + r" \\")
    (target / "timing_rows.tex").write_text("\\newcommand{\\LabFourTimingRows}{%\n" + "\n".join(timing) + "\n}\n", encoding="utf-8")
    for source, name in ((benchmark, "benchmark.json"), (quality, "quality.json"), (checks / "tests.txt", "tests.txt"),
                         (checks / "tests_summary.json", "tests_summary.json")):
        if source.resolve() != (target / name).resolve():
            shutil.copy2(source, target / name)
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    fig, ax = plt.subplots(figsize=(6.5, 3.2), layout="constrained")
    ax.plot([r["samples"][0]["input_utf8_bytes"] / 1e6 for r in b["measurements"]],
            [r["median_processing_ms"] / 1000 for r in b["measurements"]], "o-", color="#2563eb")
    ax.set(xlabel="Объём заголовков и текста, МБ", ylabel="Токенизация и лемматизация, с")
    ax.grid(alpha=.25)
    fig.savefig(target / "timing.pdf"); plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("benchmark", "quality", "checks", "snapshot", "dictionary"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--target", type=Path, default=Path("doc/lab4"))
    p.add_argument("--demo-binary", type=Path, help="also regenerate token examples using this C++ lemmatizer")
    a = p.parse_args()
    make(a.benchmark, a.quality, a.checks, a.snapshot, a.dictionary, a.target)
    if a.demo_binary:
        make_token_examples(a.demo_binary, a.dictionary, a.target)


if __name__ == "__main__":
    main()
