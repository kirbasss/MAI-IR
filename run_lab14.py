"""Reproduce Lab 14 snippets on the frozen corpus and save CLI/HTTP examples."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import HTTPServer
from urllib.parse import urlencode
from urllib.request import urlopen

from serve_search import NativeSearch, handler


ROOT = Path(__file__).resolve().parent
PACKED = ROOT / "data/compact/lab11_vb_packed_2026-11-08"
CORPUS = ROOT / "data/zipf/run_2026-11-07/snapshot/corpus.bin"
OFFSETS = ROOT / "data/snippets/lab14_offsets.bin"
NATIVE = ROOT / "build/boolean-native/boolean_search.exe"
NORMS = ROOT / "data/tfidf/lab13_2026-11-08"
DICTIONARY = ROOT / "data/lemmas/ru_en_v1/dictionary.bin"


def command(binary: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([str(binary), *args], cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "doc/lab14")
    parser.add_argument("--packed", type=Path, default=PACKED)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--offsets", type=Path, default=OFFSETS)
    parser.add_argument("--norms", type=Path, default=NORMS)
    parser.add_argument("--dict", type=Path, default=DICTIONARY)
    parser.add_argument("--binary", type=Path, default=NATIVE)
    parser.add_argument("--raw-index", type=Path, default=ROOT / "data/boolean/lab7_2026-11-07/index")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for needed in (args.binary, args.packed / "index/terms.bin", args.corpus,
                   args.norms / "norms.bin", args.dict):
        if not needed.is_file():
            parser.error(f"Required local input missing: {needed}")
    if not args.offsets.is_file():
        args.offsets.parent.mkdir(parents=True, exist_ok=True)
        command(args.binary, "--index", str(args.raw_index),
                "--corpus", str(args.corpus), "--build-snippet-index", str(args.offsets))

    base = ["--index", str(args.packed / "index"), "--positions", str(args.packed / "positions"),
            "--compact", str(args.packed / "compact"), "--corpus", str(args.corpus),
            "--snippet-index", str(args.offsets), "--limit", "5"]
    cases = [
        ("doom eternal", "surface", "tfidf"),
        ("god of war", "surface", "tfidf"),
        ("чит коды gta", "lemma", "tfidf"),
        ("doom AND eternal", "surface", "boolean"),
        ('"игры ps plus" AND сентябрь', "lemma", "boolean"),
    ]
    runs = []
    for query, space, ranking in cases:
        options = base + ["--space", space, "--query", query]
        if space == "lemma":
            options += ["--dict", str(args.dict)]
        if ranking == "tfidf":
            options += ["--ranking", "tfidf", "--norms", str(args.norms)]
        start = time.perf_counter()
        response = command(args.binary, *options)
        wall_ms = round((time.perf_counter() - start) * 1000, 2)
        data = json.loads(response.stdout.decode("utf-8"))
        runs.append({"query": query, "space": space, "ranking": ranking, "wall_ms": wall_ms,
                     "total": data["total"], "results": data["results"]})

    backend = NativeSearch(args.binary, args.packed / "index", args.dict, args.packed / "positions",
                           args.packed / "compact", norms=args.norms, corpus=args.corpus,
                           snippet_index=args.offsets)
    with HTTPServer(("127.0.0.1", 0), handler(backend)) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/?" + urlencode(
                {"q": "doom eternal", "ranking": "tfidf", "space": "surface"})
            with urlopen(url, timeout=30) as response:
                html = response.read().decode("utf-8")
                status = response.status
        finally:
            server.shutdown()
            worker.join()
    if status != 200 or "<mark>" not in html:
        raise RuntimeError("Web snippet response did not contain highlighted terms")
    (output / "web_example.html").write_text(html, encoding="utf-8")
    manifest = json.loads((args.corpus.parent / "manifest.json").read_text(encoding="utf-8"))
    (output / "examples.json").write_text(json.dumps({"cases": runs, "http_status": status,
        "web_highlights": html.count("<mark>"), "offsets_bytes": args.offsets.stat().st_size,
        "corpus_sha256": manifest["corpus_sha256"], "offsets_sha256": sha256(args.offsets)},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(runs)} CLI cases and HTTP example to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
