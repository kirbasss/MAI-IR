"""Run native tests and optional timing without touching the crawler/database."""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from benchmark_lemmas import measure
from evaluate_lemmas import evaluate
from run_tests import cpp_command

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new results directory")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--dict", dest="dictionary", type=Path)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/lemma-native/lemmatizer.exe")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--quality", action="store_true", help="evaluate the fixed judged pool too")
    parser.add_argument("--search-binary", type=Path, default=ROOT / "build/lemma-native/lemma_search.exe")
    parser.add_argument("--judgments", type=Path, default=ROOT / "tests/fixtures/lab4_judgments.json")
    args = parser.parse_args()
    if bool(args.snapshot) != bool(args.dictionary):
        parser.error("--snapshot and --dict are required together for benchmarking")
    if args.quality and not args.snapshot:
        parser.error("--quality requires --snapshot and --dict")
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        log = bytearray()
        for command in (
            cpp_command("4"),
            [sys.executable, "-m", "unittest", "-v", "tests.python.test_lemma_dictionary", "tests.python.test_lemma_evaluation"],
        ):
            result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            log.extend(("\nCOMMAND: " + " ".join(command) + "\n").encode())
            log.extend(result.stdout)
            (args.output / "tests.txt").write_bytes(log)
            print(result.stdout.decode("utf-8", errors="replace"), end="")
            if result.returncode:
                return result.returncode
        cpp = re.search(rb"0 tests failed out of (\d+)", log)
        python = re.search(rb"Ran (\d+) tests in [\d.]+s", log)
        if not cpp or not python:
            raise ValueError("Cannot confirm CTest/unittest results")
        (args.output / "tests_summary.json").write_text(json.dumps({
            "ctest_checks_lab4": int(cpp[1]), "python_preparation_and_metrics_checks": int(python[1]),
        }, indent=2) + "\n", encoding="utf-8")
        if args.snapshot:
            measure(args.binary, args.snapshot, args.dictionary, args.output / "benchmark.json", args.runs)
        if args.quality:
            evaluate(args.snapshot, args.dictionary, args.search_binary, args.judgments, args.output / "quality")
        print(f"Lab 4 results: {args.output.resolve()}")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[lab4] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
