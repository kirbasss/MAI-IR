"""Run tests grouped by lab. No arguments preserves the original Python-only run."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent
# Python modules are grouped by lab; native tests live separately in tests/cpp/.
PYTHON_SUITES = {
    "1": ("test_lab1", "test_source_parsers", "test_gamemag_parser", "test_inventory_sample"),
    "2": (
        "test_crawler", "test_crawler_cli", "test_crawler_discovery", "test_crawl_quality",
        "test_discovery", "test_inventory_refresh", "test_sitemap_cache", "test_parallel_crawler",
        "test_parser_worker", "test_disk_guard", "test_raw_archive", "test_raw_archive_cli",
        "test_raw_migration", "test_corpus_repair", "test_stopgame_repair",
        "test_crawler_pipeline_integration",
    ),
    "3": ("test_corpus_export", "test_lab3_demo", "test_lab3_runner"),
    "4": ("test_lemma_dictionary", "test_lemma_evaluation"),
    "5": ("test_lab5",),
    "7": ("test_lab7",),
    "8": ("test_lab8",),
    "10": ("test_lab10",),
    "11": (),
    "12": ("test_lab12",),
    "13": ("test_lab13",),
}


def python_modules(lab: str) -> list[str]:
    groups = PYTHON_SUITES.values() if lab == "all" else (PYTHON_SUITES[lab],)
    return ["tests.python." + name for group in groups for name in group]


def cpp_command(lab: str) -> list[str]:
    if lab not in ("3", "4", "5", "7", "8", "10", "11", "12", "13", "all"):
        raise ValueError("C++ tests exist for labs 3, 4, 5, 7, 8, 10, 11, 12 and 13")
    label = "" if lab == "all" else " -L lab" + lab
    # Rebuild before testing so that CTest labels and binaries match the sources.
    script = ("cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release"
              " && cmake --build /build --parallel 2"
              " && ctest --test-dir /build --output-on-failure --no-tests=error" + label)
    return ["docker", "compose", "run", "--rm", "-T", "--no-deps", "tokenizer-tests", "sh", "-ec", script]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", choices=("1", "2", "3", "4", "5", "7", "8", "10", "11", "12", "13", "all"),
                        help="selected lab, including its C++ tests when applicable")
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--python-only", action="store_true")
    kind.add_argument("--cpp-only", action="store_true")
    parser.add_argument("--list", action="store_true", help="show groups without running tests")
    args = parser.parse_args()
    lab = args.lab or "all"
    if args.cpp_only and lab in ("1", "2"):
        parser.error("Labs 1 and 2 have Python tests; C++/GoogleTest starts at lab 3")
    if args.list:
        for group in PYTHON_SUITES if lab == "all" else (lab,):
            print(f"Lab {group}: {'Python + C++' if PYTHON_SUITES[group] and group not in ('1','2') else 'Python' if PYTHON_SUITES[group] else 'C++'}")
            for name in PYTHON_SUITES[group]: print("  tests.python." + name)
            if group in ("3", "4", "5", "7", "8", "10", "11", "12", "13"):
                print(f"  C++/GoogleTest + CLI: CTest label lab{group}")
        return 0
    try:
        if lab in ("3", "4", "5", "7", "8", "10", "11", "12", "13", "all") and not args.python_only and (args.lab or args.cpp_only):
            print(f"C++ tests: {lab}", flush=True)
            code = subprocess.run(cpp_command(lab), cwd=ROOT).returncode
            if code:
                return code
        if not args.cpp_only and (lab == "all" or PYTHON_SUITES[lab]):
            # Discovery keeps the original no-argument behavior for future test files too.
            loader = unittest.defaultTestLoader
            suite = (loader.discover(str(ROOT / "tests" / "python"), top_level_dir=str(ROOT)) if args.lab is None
                     else loader.loadTestsFromNames(python_modules(lab)))
            print(f"Python tests: {lab}", flush=True)
            return int(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
        return 0
    except OSError as exc:
        parser.exit(1, f"[tests] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
