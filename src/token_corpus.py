"""Streaming, versioned snapshots of parsed documents for the C++ tokenizer."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator


MAGIC = b"MAIIR3\x00\x01"
MAX_FIELD_BYTES = 64 * 1024 * 1024
SOURCE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def mongo_documents(db, *, batch_size: int = 100) -> Iterator[dict]:
    """Read parsed fields only; never request raw HTML or mutate the database."""
    cursor = db.documents.find(
        {"indexable": {"$ne": False}, "parsed.parse_error": None},
        {"url": 1, "source": 1, "fetched_at": 1, "content_sha256": 1,
         "parsed.title": 1, "parsed.text": 1, "parsed.parse_error": 1},
    ).sort("_id", 1).batch_size(batch_size)
    try:
        yield from cursor
    finally:
        cursor.close()


def local_documents(root: Path) -> Iterator[dict]:
    """Offline alternative for saved parser JSON, in a deterministic path order."""
    for path in sorted(root.rglob("*.json")):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        yield {"url": parsed.get("url"), "source": parsed.get("source"),
               "parsed": parsed, "indexable": not bool(parsed.get("parse_error"))}


def _field(value, name: str) -> bytes:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    encoded = value.encode("utf-8", errors="strict")
    if len(encoded) > MAX_FIELD_BYTES:
        raise ValueError(f"{name} exceeds {MAX_FIELD_BYTES} bytes")
    return encoded


def export_snapshot(documents: Iterable[dict], output: Path, *, origin: str) -> dict:
    """Publish a new directory only after all records have been written.

    IDs are local to this immutable snapshot. The caller determines record order.
    An existing output directory is never overwritten.
    """
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Snapshot already exists: {output}; choose a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    input_bytes = 0
    title_only = 0
    skipped = 0
    count = 0
    started_at = datetime.now(UTC).isoformat()
    try:
        with (staging / "corpus.bin").open("wb") as corpus, (staging / "documents.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as metadata:
            def write(value: bytes) -> None:
                corpus.write(value)
                digest.update(value)

            write(MAGIC)
            for document in documents:
                parsed = document.get("parsed") or {}
                if not isinstance(parsed, dict):
                    raise ValueError("parsed must be an object")
                if document.get("indexable") is False or parsed.get("parse_error"):
                    skipped += 1
                    continue
                title = _field(parsed.get("title"), "title")
                text = _field(parsed.get("text"), "text")
                if not title.strip() and not text.strip():
                    skipped += 1
                    continue
                source = document.get("source")
                url = document.get("url") or document.get("_id")
                if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
                    raise ValueError(f"Invalid source identifier: {source!r}")
                if not isinstance(url, str) or not url:
                    raise ValueError("Document must have a nonempty URL")
                count += 1
                encoded_source = source.encode("ascii")
                write(struct.pack("<QI", count, len(encoded_source)))
                write(encoded_source)
                for field in (title, text):
                    write(struct.pack("<Q", len(field)))
                    write(field)
                input_bytes += len(title) + len(text)
                title_only += not bool(text.strip())
                counts[source] += 1
                metadata.write(json.dumps({
                    "document_id": count, "url": url, "source": source,
                    "fetched_at": document.get("fetched_at"),
                    "content_sha256": document.get("content_sha256"),
                }, ensure_ascii=False) + "\n")
        if count == 0:
            raise ValueError("No indexable documents with a title or text")
        result = {
            "format": "MAIIR3-v1", "created_at": datetime.now(UTC).isoformat(),
            "export_started_at": started_at,
            "origin": origin, "documents": count, "title_only_documents": title_only,
            "skipped_documents": skipped, "input_utf8_bytes": input_bytes,
            "corpus_sha256": digest.hexdigest(), "sources": dict(sorted(counts.items())),
            "fields": ["title", "text"],
        }
        (staging / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(output)
        return result
    except BaseException:
        # Only the private directory created by mkdtemp above is removed.
        shutil.rmtree(staging)
        raise


def snapshot_records(path: Path) -> Iterator[tuple[int, str, bytes, bytes]]:
    """Strict reader used by benchmarks and format tests, not for tokenization."""
    with path.open("rb") as handle:
        def read_exact(size: int) -> bytes:
            value = handle.read(size)
            if len(value) != size:
                raise ValueError("Truncated corpus record")
            return value

        if read_exact(len(MAGIC)) != MAGIC:
            raise ValueError("Unsupported corpus format")
        previous = 0
        while prefix := handle.read(8):
            if len(prefix) != 8:
                raise ValueError("Truncated document ID")
            document_id = struct.unpack("<Q", prefix)[0]
            if document_id <= previous:
                raise ValueError("Document IDs must increase")
            previous = document_id
            source_size = struct.unpack("<I", read_exact(4))[0]
            if not 1 <= source_size <= 64:
                raise ValueError("Invalid source length")
            source = read_exact(source_size).decode("ascii")
            if not SOURCE_RE.fullmatch(source):
                raise ValueError("Invalid source identifier")
            fields = []
            for _ in range(2):
                size = struct.unpack("<Q", read_exact(8))[0]
                if size > MAX_FIELD_BYTES:
                    raise ValueError("Field size limit exceeded")
                fields.append(read_exact(size))
            yield document_id, source, fields[0], fields[1]
