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


def test_commands(backend: str, native_build: Path) -> list[list[str]]:
    if backend == "docker":
        return [["docker", "compose", "run", "--rm", "-T", "--no-deps", "tokenizer-tests"]]
    return [
        ["cmake", "-S", str(ROOT), "-B", str(native_build), "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON"],
        ["cmake", "--build", str(native_build), "--config", "Release", "--parallel", "2"],
        ["ctest", "--test-dir", str(native_build), "-C", "Release", "--output-on-failure"],
    ]


def run_checks(output: Path, backend: str, native_build: Path) -> int:
    """Run native C++ assertions, then Python export/demo checks, saving both logs."""
    commands = test_commands(backend, native_build) + [[
        sys.executable, "-m", "unittest", "-v", "tests.test_corpus_export", "tests.test_lab3_demo",
    ]]
    log = bytearray()
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log.extend(("\nCOMMAND: " + " ".join(command) + "\n").encode())
        log.extend(result.stdout)
        (output / "tests.txt").write_bytes(log)
        print(result.stdout.decode("utf-8", errors="replace"), end="")
        if result.returncode:
            return result.returncode
    cpp = re.search(rb"0 tests failed out of (\d+)", log)
    python = re.search(rb"Ran (\d+) tests in [\d.]+s", log)
    if not cpp or not python or int(cpp[1]) == 0:
        raise RuntimeError("Cannot find successful CTest and unittest summaries")
    cpp_count, python_count = int(cpp[1]), int(python[1])
    (output / "tests_summary.tex").write_text(
        f"\\newcommand{{\\LabTestCount}}{{{cpp_count + python_count}}}\n"
        f"\\newcommand{{\\LabCppTestCount}}{{{cpp_count}}}\n"
        f"\\newcommand{{\\LabPythonTestCount}}{{{python_count}}}\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--snapshot", type=Path, help="reuse an immutable snapshot")
    inputs.add_argument("--config", type=Path, help="export MongoDB using crawler YAML")
    inputs.add_argument("--parsed-dir", type=Path, help="offline parser JSON instead of MongoDB")
    parser.add_argument("--output", type=Path, required=True, help="new directory for the run")
    parser.add_argument("--report-dir", type=Path, help="copy small reproducibility artifacts here")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--cpp-tests", choices=["docker", "native"], default="docker",
                        help="GoogleTest runner; Docker image must be built beforehand")
    parser.add_argument("--cpp-build-dir", type=Path, default=ROOT / "build" / "native-tests",
                        help="CMake build directory for --cpp-tests native")
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        binary = build()
        test_status = run_checks(output, args.cpp_tests, args.cpp_build_dir.resolve())
        if test_status:
            return test_status
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
