"""Measure the native pipeline on a fixed snapshot; no exports or MongoDB writes."""
from __future__ import annotations
import argparse
import hashlib
import json
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def measure(binary: Path, snapshot: Path, dictionary: Path, output: Path, runs: int = 5) -> dict:
    if runs < 1:
        raise ValueError("runs must be positive")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if output.exists():
        raise FileExistsError(output)
    # Verify recorded inputs instead of silently timing a modified snapshot.
    with (snapshot / "corpus.bin").open("rb") as stream:
        corpus_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if corpus_digest != manifest["corpus_sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    dictionary_manifest = json.loads(dictionary.with_name("manifest.json").read_text(encoding="utf-8"))
    with dictionary.open("rb") as stream:
        dictionary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if dictionary_digest != dictionary_manifest["sha256"]:
        raise ValueError("Dictionary checksum mismatch")
    measurements = []
    for divisor in (8, 4, 2, 1):
        limit = max(1, manifest["documents"] // divisor)
        command = [str(binary.resolve()), "corpus", str((snapshot / "corpus.bin").resolve()),
                   "--dict", str(dictionary.resolve()), "--limit", str(limit)]
        subprocess.run(command, check=True, capture_output=True)  # warm-up, not measured
        samples = []
        for _ in range(runs):
            start = time.perf_counter()
            result = subprocess.run(command, check=True, capture_output=True)
            elapsed = (time.perf_counter() - start) * 1000
            stats = json.loads(result.stdout)
            samples.append({**stats, "process_wall_ms": elapsed})
        median = statistics.median(s["processing_ms"] for s in samples)
        row = {
            "limit": limit, "samples": samples, "median_processing_ms": median,
            "median_load_ms": statistics.median(s["load_ms"] for s in samples),
            "median_elapsed_ms": statistics.median(s["elapsed_ms"] for s in samples),
            "kb_per_second": samples[0]["input_utf8_bytes"] / median if median else None,
            "tokens_per_second": samples[0]["tokens"] * 1000 / median if median else None,
        }
        measurements.append(row)
        print(f"{limit} docs; {median:.2f} ms; {row['tokens_per_second']:.0f} tokens/s", flush=True)
    result = {
        "created_at": datetime.now(UTC).isoformat(), "runs": runs,
        "method": "prefix samples; one warm-up; medians; processing includes tokenization and lookup, no TSV",
        "languages": [s["name"] for s in dictionary_manifest["sources"]],
        "snapshot_sha256": corpus_digest, "dictionary_sha256": dictionary_digest,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "measurements": measurements,
        "quality_note": "Throughput/coverage counters are NOT search relevance measurements.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--dict", dest="dictionary", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=Path("build/lemma-native/lemmatizer.exe"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    try:
        measure(args.binary, args.snapshot, args.dictionary, args.output, args.runs)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[benchmark-lemmas] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
