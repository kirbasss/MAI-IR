"""Build the standalone C++17 tokenizer (requires GCC or Clang)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_BINARY = ROOT / "build" / ("tokenizer.exe" if os.name == "nt" else "tokenizer")


def ensure_built(output: Path = DEFAULT_BINARY) -> Path:
    """Never run a stale executable after editing rules or changing branches."""
    try:
        info = json.loads(output.with_suffix(".build.json").read_text(encoding="utf-8"))
        sources = [ROOT / "cpp" / name for name in ("tokenizer.cpp", "tokenizer_main.cpp", "tokenizer.hpp")]
        if (info["binary_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest() and
                all(info["sources_sha256"].get(path.name) == hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sources)):
            return output
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return build(output)


def build(output: Path = DEFAULT_BINARY, compiler: str | None = None) -> Path:
    compiler = compiler or os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise RuntimeError("Install g++/clang++ with C++17 support, or set CXX to its executable")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / "cpp" / name for name in ("tokenizer.cpp", "tokenizer_main.cpp")]
    flags = ["-std=c++17", "-O3", "-DNDEBUG", "-Wall", "-Wextra", "-Wpedantic"]
    command = [compiler, *flags, *map(str, sources), "-o", str(output)]
    subprocess.run(command, check=True, cwd=ROOT)
    version = subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True).stdout
    info = {
        "compiler": version.splitlines()[0], "flags": flags,
        "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "sources_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in [*sources, ROOT / "cpp" / "tokenizer.hpp"]},
    }
    output.with_suffix(".build.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler")
    parser.add_argument("--output", type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args()
    try:
        print(build(args.output, args.compiler))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[build] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
