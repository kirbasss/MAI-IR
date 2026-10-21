"""Build, test, export and measure Lab 3, with optional report artifacts."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from benchmark_tokenizer import benchmark
from build_tokenizer import ROOT, build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--snapshot", type=Path, help="reuse an immutable snapshot")
    inputs.add_argument("--config", type=Path, help="export MongoDB using crawler YAML")
    inputs.add_argument("--parsed-dir", type=Path, help="offline parser JSON instead of MongoDB")
    parser.add_argument("--output", type=Path, required=True, help="new directory for the run")
    parser.add_argument("--report-dir", type=Path, help="copy small reproducibility artifacts here")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        binary = build()
        tests = subprocess.run([sys.executable, "-m", "unittest", "-v", "tests.test_corpus_export",
                                "tests.test_tokenizer", "tests.test_lab3_demo"], cwd=ROOT, capture_output=True)
        log = tests.stdout + tests.stderr
        (output / "tests.txt").write_bytes(log)
        print(log.decode("utf-8", errors="replace"), end="")
        if tests.returncode:
            return tests.returncode
        summary = re.search(rb"Ran (\d+) tests in ([\d.]+)s", log)
        if not summary:
            raise RuntimeError("Cannot find unittest summary")
        (output / "tests_summary.tex").write_text(
            f"\\newcommand{{\\LabTestCount}}{{{summary[1].decode()}}}\n", encoding="utf-8"
        )
        snapshot = args.snapshot.resolve() if args.snapshot else output / "snapshot"
        if not args.snapshot:
            source = (["--parsed-dir", str(args.parsed_dir.resolve())] if args.parsed_dir else
                      ["--config", str((args.config or ROOT / "crawler.batch.yaml").resolve())])
            subprocess.run([sys.executable, str(ROOT / "export_corpus.py"), *source,
                            "--output", str(snapshot)], cwd=ROOT, check=True)
        benchmark(snapshot, output / "results", binary, runs=args.runs)
        if args.report_dir:
            target = args.report_dir.resolve()
            target.mkdir(parents=True, exist_ok=True)
            for name in ("statistics.json", "benchmark.json", "benchmark.csv", "timing.pdf", "timing.svg",
                         "results.tex", "source_rows.tex", "timing_rows.tex"):
                shutil.copy2(output / "results" / name, target / name)
            shutil.copy2(output / "tests.txt", target / "tests.txt")
            shutil.copy2(output / "tests_summary.tex", target / "tests_summary.tex")
        print(f"Lab 3 results: {output / 'results'}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[lab3] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
