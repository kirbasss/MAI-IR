"""Single entry point for running the search, crawler, or test suite."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
COMMANDS = {
    "search": "serve_search.py",
    "crawl": "crawler.py",
    "test": "run_tests.py",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(sys.argv[1:2])
    return subprocess.call(
        [sys.executable, str(ROOT / COMMANDS[args.command]), *sys.argv[2:]], cwd=ROOT
    )


if __name__ == "__main__":
    raise SystemExit(main())
