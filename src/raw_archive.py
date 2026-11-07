"""Versioned, indexed lossless raw BSON archives; verified eviction leaves URL stubs."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
import zlib
from collections import Counter
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from bson import BSON
from bson.errors import InvalidBSON
from pymongo import UpdateOne


FORMAT = "MAIIR-RAW-BSON-GZIP-v1"
MAX_BSON = 16 * 1024 * 1024
MAX_COMPRESSED = MAX_BSON + 1024 * 1024
CHUNK_RE = re.compile(r"raw_[0-9]{6}\.bson\.gz\Z")
GIB = 1024 ** 3


class RawArchiveError(RuntimeError):
    pass


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ensure_archive_disk(path, minimum_gib=5):
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        raise RawArchiveError(f"Диск архива недоступен: {path}: {exc}") from exc
    if usage.free < minimum_gib * GIB:
        raise RawArchiveError(f"Мало места для архива на {path}: {usage.free / GIB:.2f} ГиБ")


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def open_index(root):
    connection = sqlite3.connect((Path(root) / "index.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    return connection


def manifest(root):
    root = Path(root).resolve()
    if (root / "INCOMPLETE").exists():
        raise RawArchiveError("Архив не завершён: INCOMPLETE; удаление raw запрещено")
    value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("format") != FORMAT
            or not re.fullmatch(r"[0-9a-f]{32}", str(value.get("batch_id", "")))):
        raise RawArchiveError("Неизвестный формат архива")
    if (not isinstance(value.get("database"), str) or not isinstance(value.get("chunks"), list)
            or not value["chunks"] or not isinstance(value.get("sources"), dict)):
        raise RawArchiveError("Неполный манифест архива")
    for key in ("records", "bson_bytes", "html_utf8_bytes", "compressed_bytes"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise RawArchiveError(f"Некорректный счётчик: {key}")
    for chunk in value["chunks"]:
        if (not isinstance(chunk, dict) or not isinstance(chunk.get("name"), str)
                or not CHUNK_RE.fullmatch(chunk["name"])
                or type(chunk.get("bytes")) is not int or chunk["bytes"] <= 0
                or type(chunk.get("records")) is not int or chunk["records"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(chunk.get("sha256", "")))):
            raise RawArchiveError("Некорректная часть архива")
    names = [row["name"] for row in value["chunks"]]
    if len(set(names)) != len(names):
        raise RawArchiveError("Некорректные имена частей архива")
    return value


def decode_member(data, row):
    if not 0 < len(data) <= MAX_COMPRESSED or len(data) != row["compressed_bytes"]:
        raise RawArchiveError("Неверная длина gzip-записи")
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(data, MAX_BSON + 1)
        if (len(raw) > MAX_BSON or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or len(raw) != row["bson_bytes"]):
            raise RawArchiveError("Повреждённая или слишком большая BSON-запись")
        if hashlib.sha256(raw).hexdigest() != row["bson_sha256"]:
            raise RawArchiveError("SHA-256 BSON-записи не совпадает")
        document = BSON(raw).decode()
        if document.get("_id") != row["url"] or not isinstance(document.get("html"), str):
            raise RawArchiveError("ID/HTML записи не соответствует индексу")
        if hashlib.sha256(document["html"].encode("utf-8")).hexdigest() != row["html_sha256"]:
            raise RawArchiveError("SHA-256 HTML не совпадает")
        return document
    except (zlib.error, ValueError, InvalidBSON) as exc:
        raise RawArchiveError(f"Не удалось прочитать gzip/BSON: {exc}") from exc


def read_archived_raw(stub):
    """Random access reads exactly one gzip member, not a whole batch or chunk."""
    ref = stub.get("raw_archive") or {}
    try:
        root = Path(ref["directory"])
        if not root.is_absolute() or not CHUNK_RE.fullmatch(ref["chunk"]):
            raise RawArchiveError("Небезопасный путь к архиву")
        offset, length = ref["offset"], ref["compressed_bytes"]
        if type(offset) is not int or offset < 0 or type(length) is not int or not 0 < length <= MAX_COMPRESSED:
            raise RawArchiveError("Неверное смещение архивной записи")
        with (root / ref["chunk"]).open("rb") as stream:
            stream.seek(offset); data = stream.read(length)
        document = decode_member(data, {**ref, "url": stub["_id"]})
        for key in ("content_sha256", "raw_generation", "fetched_at", "source"):
            if document.get(key) != stub.get(key):
                raise RawArchiveError(f"Архив содержит другую версию страницы: {key}")
        return document
    except (OSError, KeyError, TypeError) as exc:
        raise RawArchiveError(f"Архив HTML недоступен или ссылка повреждена: {exc}") from exc


def archive_reference(root, batch_id, row):
    return {"batch_id": batch_id, "directory": str(Path(root).resolve()),
            **{key: row[key] for key in ("chunk", "offset", "compressed_bytes", "bson_bytes", "bson_sha256", "html_sha256")}}


def eligible(raw, document, active):
    if raw["_id"] in active:
        return False
    return bool(document and isinstance(document.get("parsed"), dict)
                and document.get("indexable") is not False and not document["parsed"].get("parse_error")
                and document.get("raw_page_id") == raw["_id"]
                and all(document.get(key) == raw.get(key) for key in ("content_sha256", "raw_generation", "fetched_at")))


def has_ready_raw(db):
    """Cheap metadata-only preflight; an empty auto run creates no batch."""
    active = {row["_id"] for row in db.frontier.find({"state": "in_progress"}, {"_id": 1})}
    fields = {"raw_page_id": 1, "content_sha256": 1, "raw_generation": 1, "fetched_at": 1,
              "indexable": 1, "parsed.parse_error": 1, "parsed.title": 1}
    cursor = db.raw_pages.find(
        {"html": {"$type": "string"}, "parse_state": {"$in": [None, "done"]}},
        {"content_sha256": 1, "raw_generation": 1, "fetched_at": 1}).batch_size(64)
    try:
        while batch := list(islice(cursor, 64)):
            documents = {row["_id"]: row for row in db.documents.find(
                {"_id": {"$in": [raw["_id"] for raw in batch]}}, fields)}
            if any(eligible(raw, documents.get(raw["_id"]), active) for raw in batch):
                return True
        return False
    finally:
        cursor.close()


def choose_auto_batch(db, root, *, progress=print):
    """Resume a complete unfinished batch, otherwise choose a fresh directory."""
    root = Path(root).resolve()
    # Require the existing archive root: never recreate a disconnected mount.
    if not root.is_dir():
        raise RawArchiveError(f"Каталог архива недоступен: {root}. Подключите диск и проверьте путь.")
    ensure_archive_disk(root)
    for directory in sorted(root.glob("raw_*")):
        if not directory.is_dir():
            continue
        if (directory / "INCOMPLETE").exists():
            progress(f"[archive] Пропущен незавершённый экспорт: {directory}; исходные HTML сохранены", flush=True)
            continue
        if not (directory / "manifest.json").is_file():
            continue
        value = manifest(directory)
        if value["database"] != db.name:
            continue
        catalog = db.raw_archives.find_one({"_id": value["batch_id"]})
        if catalog and catalog.get("status") == "evicted":
            continue
        return directory, True
    if not has_ready_raw(db):
        return None, False
    name = datetime.now(UTC).strftime("raw_%Y-%m-%d_%H%M%S_") + uuid.uuid4().hex[:8]
    return root / name, False


def export_raw(db, output, *, chunk_bytes=128 * 1024 * 1024, minimum_gib=5, progress=print):
    """No MongoDB writes. Incomplete output is retained; never reused or pruned."""
    output = Path(output).resolve()
    if chunk_bytes < 1024 or output.exists():
        raise RawArchiveError("Укажите новый каталог и положительный размер части")
    # Validate the nearest existing parent before creating any output files.
    parent = output.parent
    while not parent.exists():
        if parent == parent.parent: raise RawArchiveError("Диск назначения не найден")
        parent = parent.parent
    ensure_archive_disk(parent, minimum_gib)
    output.mkdir(parents=True, exist_ok=False)
    (output / "INCOMPLETE").touch(exist_ok=False)
    index = sqlite3.connect(output / "index.sqlite")
    index.execute("PRAGMA journal_mode=DELETE")
    index.execute("PRAGMA cache_size=-4096")
    index.execute("CREATE TABLE records (url TEXT PRIMARY KEY, source TEXT NOT NULL, chunk TEXT NOT NULL, offset INTEGER NOT NULL, "
                  "compressed_bytes INTEGER NOT NULL, bson_bytes INTEGER NOT NULL, bson_sha256 TEXT NOT NULL, html_sha256 TEXT NOT NULL)")
    active = {row["_id"] for row in db.frontier.find({"state": "in_progress"}, {"_id": 1})}
    projection = {"raw_page_id": 1, "content_sha256": 1, "raw_generation": 1, "fetched_at": 1,
                  "indexable": 1, "parsed.parse_error": 1, "parsed.title": 1}
    count = logical = html_bytes = compressed = skipped = 0
    sources = Counter(); chunks = []
    stream = None; chunk_hash = None; chunk_count = 0

    def finish_chunk():
        nonlocal stream
        if stream is not None:
            size = stream.tell(); stream.flush(); os.fsync(stream.fileno()); stream.close()
            chunks.append({"name": Path(stream.name).name, "bytes": size, "sha256": chunk_hash.hexdigest(), "records": chunk_count})
            stream = None

    def flush_batch(batch):
        nonlocal stream, chunk_hash, chunk_count, count, logical, html_bytes, compressed, skipped
        docs = {r["_id"]: r for r in db.documents.find({"_id": {"$in": [r[0]["_id"] for r in batch]}}, projection)}
        for raw, encoded in batch:
            if not eligible(raw, docs.get(raw["_id"]), active):
                skipped += 1; continue
            ensure_archive_disk(output, minimum_gib)
            member = gzip.compress(encoded, compresslevel=3, mtime=0)
            if stream is not None and stream.tell() + len(member) > chunk_bytes:
                finish_chunk()
            if stream is None:
                stream = (output / f"raw_{len(chunks) + 1:06d}.bson.gz").open("xb")
                chunk_hash = hashlib.sha256(); chunk_count = 0
            offset = stream.tell(); stream.write(member); chunk_hash.update(member); chunk_count += 1
            html = raw["html"].encode("utf-8")
            index.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?)", (
                raw["_id"], raw["source"], Path(stream.name).name, offset, len(member), len(encoded),
                hashlib.sha256(encoded).hexdigest(), hashlib.sha256(html).hexdigest()))
            count += 1; logical += len(encoded); html_bytes += len(html); compressed += len(member)
            sources[raw["source"]] += 1
            if count % 1000 == 0:
                index.commit()
                progress(f"[archive/export] records={count} BSON={logical / GIB:.2f} GiB gzip={compressed / GIB:.2f} GiB", flush=True)

    query = {"html": {"$type": "string"}, "parse_state": {"$in": [None, "done"]}}
    cursor = db.raw_pages.find(query).batch_size(16)
    try:
        batch = []; batch_size = 0
        for raw in cursor:
            encoded = BSON.encode(raw)
            if len(encoded) > MAX_BSON: raise RawArchiveError("BSON превышает максимальный размер")
            if batch and (len(batch) >= 64 or batch_size + len(encoded) > 32 * 1024 * 1024):
                flush_batch(batch); batch = []; batch_size = 0
            batch.append((raw, encoded)); batch_size += len(encoded)
        if batch: flush_batch(batch)
        finish_chunk()
        if count == 0: raise RawArchiveError("Нет готовых согласованных HTML для архивирования")
        index.execute("CREATE INDEX record_location ON records(chunk,offset)")
        index.commit(); index.close()
        with (output / "index.sqlite").open("r+b") as index_file: os.fsync(index_file.fileno())
        value = {"format": FORMAT, "batch_id": uuid.uuid4().hex, "database": db.name,
                 "created_at": datetime.now(UTC).isoformat(), "records": count, "bson_bytes": logical,
                 "html_utf8_bytes": html_bytes, "compressed_bytes": compressed, "sources": dict(sources),
                 "skipped_inconsistent_or_active": skipped, "chunks": chunks,
                 "index_sha256": file_sha(output / "index.sqlite"),
                 "selection": "Raw with done/legacy parse state, matching ready indexable parsed version; active HTTP tasks excluded."}
        write_json(output / "manifest.json", value)
        (output / "INCOMPLETE").unlink()
        return value
    finally:
        cursor.close()
        if stream is not None: stream.close()
        index.close()


def verify_archive(root, *, progress=print):
    root = Path(root).resolve(); value = manifest(root)
    manifest_hash = file_sha(root / "manifest.json")
    if file_sha(root / "index.sqlite") != value["index_sha256"]:
        raise RawArchiveError("SHA-256 индекса архива не совпадает")
    count = logical = compressed = html_bytes = 0; sources = Counter()
    index = open_index(root)
    try:
        if index.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RawArchiveError("Повреждён SQLite-индекс архива")
        for chunk in value["chunks"]:
            path = root / chunk["name"]
            if path.stat().st_size != chunk["bytes"] or file_sha(path) != chunk["sha256"]:
                raise RawArchiveError(f"Повреждена часть архива: {path.name}")
            local_count = 0
            with path.open("rb") as stream:
                for row in index.execute("SELECT * FROM records WHERE chunk=? ORDER BY offset", (chunk["name"],)):
                    if row["offset"] != stream.tell() or not 0 < row["compressed_bytes"] <= MAX_COMPRESSED:
                        raise RawArchiveError("Неверные границы записи в индексе")
                    raw = decode_member(stream.read(row["compressed_bytes"]), row)
                    if raw.get("source") != row["source"]: raise RawArchiveError("Источник не соответствует индексу")
                    count += 1; local_count += 1; logical += row["bson_bytes"]; compressed += row["compressed_bytes"]
                    html_bytes += len(raw["html"].encode("utf-8"))
                    sources[row["source"]] += 1
                    if count % 1000 == 0: progress(f"[archive/verify] records={count}", flush=True)
                if stream.read(1) or local_count != chunk["records"]:
                    raise RawArchiveError("Число записей или конец части архива не совпадают")
        if (count != value["records"] or count != index.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                or logical != value["bson_bytes"] or html_bytes != value["html_utf8_bytes"]
                or compressed != value["compressed_bytes"] or dict(sources) != value["sources"]):
            raise RawArchiveError("Итоговые счётчики архива не совпадают")
    finally:
        index.close()
    if (file_sha(root / "manifest.json") != manifest_hash
            or file_sha(root / "index.sqlite") != value["index_sha256"]):
        raise RawArchiveError("Манифест изменился во время проверки")
    result = {"batch_id": value["batch_id"], "manifest_sha256": manifest_hash, "records": count,
              "verified_at": datetime.now(UTC).isoformat(), "verified": True}
    write_json(root / "verification.json", result)
    return result


def _row_batches(index):
    batch = []; size = 0
    for row in index.execute("SELECT * FROM records ORDER BY chunk,offset"):
        if batch and (len(batch) >= 64 or size + row["bson_bytes"] > 32 * 1024 * 1024):
            yield batch; batch = []; size = 0
        batch.append(dict(row)); size += row["bson_bytes"]
    if batch: yield batch


def sample_restore(db, root):
    """Round-trip samples through a new disposable MongoDB database, not mai_ir."""
    root = Path(root).resolve(); value = manifest(root); index = open_index(root)
    name = "mai_ir_archive_verify_" + uuid.uuid4().hex
    if name == db.name or not re.fullmatch(r"mai_ir_archive_verify_[0-9a-f]{32}", name):
        raise RawArchiveError("Небезопасное имя тестовой базы")
    target = db.client[name]
    try:
        chosen = {r[0] for r in index.execute("SELECT MIN(rowid) FROM records GROUP BY source")}
        chosen.update({1, max(1, value["records"] // 2), value["records"]})
        count = 0
        for number in sorted(chosen):
            row = index.execute("SELECT * FROM records WHERE rowid=?", (number,)).fetchone()
            if row is None: raise RawArchiveError("Образец отсутствует в индексе")
            # The original source metadata is independently read from BSON.
            with (root / row["chunk"]).open("rb") as stream:
                stream.seek(row["offset"]); original = decode_member(stream.read(row["compressed_bytes"]), row)
            target.raw_pages.insert_one(original)
            restored = target.raw_pages.find_one({"_id": row["url"]})
            if hashlib.sha256(BSON.encode(restored)).hexdigest() != row["bson_sha256"]:
                raise RawArchiveError("Проверка восстановления BSON через MongoDB не прошла")
            count += 1
        return {"sample_records_restored": count, "ok": True}
    finally:
        index.close()
        # Only the freshly generated private database is removed; never a
        # user-specified namespace. Source raw/documents are not touched here.
        db.client.drop_database(name)


def evict_archive(db, root, *, disk_guard=None, minimum_gib=5, progress=print):
    """Full verification precedes any source mutation; reruns are idempotent."""
    root = Path(root).resolve(); proof = verify_archive(root, progress=progress); value = manifest(root)
    if value["database"] != db.name: raise RawArchiveError("Архив создан для другой базы")
    if disk_guard is not None: disk_guard.check(force=True)
    restore_result = sample_restore(db, root)
    db.raw_archives.update_one({"_id": value["batch_id"]}, {"$set": {
        "directory": str(root), "manifest_sha256": proof["manifest_sha256"], "format": FORMAT,
        "records": value["records"], "compressed_bytes": value["compressed_bytes"],
        "status": "evicting", "verified_at": proof["verified_at"], "restore_check": restore_result,
    }}, upsert=True)
    counts = {"evicted": 0, "already_archived": 0, "changed_or_missing": 0}
    index = open_index(root); processed = 0
    try:
        for batch in _row_batches(index):
            ensure_archive_disk(root, minimum_gib)
            if disk_guard is not None: disk_guard.check()
            by_id = {row["url"]: row for row in batch}; operations = []; found = set()
            cursor = db.raw_pages.find({"_id": {"$in": list(by_id)}}).batch_size(16)
            try:
                for raw in cursor:
                    url = raw["_id"]; found.add(url); row = by_id[url]
                    if raw.get("raw_storage") == "archive" and (raw.get("raw_archive") or {}).get("batch_id") == value["batch_id"]:
                        counts["already_archived"] += 1; continue
                    # Check ALL original fields before issuing a version-fenced
                    # update. New/changed/reparsed versions stay hot and intact.
                    if hashlib.sha256(BSON.encode(raw)).hexdigest() != row["bson_sha256"]:
                        counts["changed_or_missing"] += 1; continue
                    ref = archive_reference(root, value["batch_id"], row)
                    expected = {"_id": url, "content_sha256": raw.get("content_sha256"),
                                "raw_generation": raw.get("raw_generation"), "fetched_at": raw.get("fetched_at"),
                                "parse_state": raw.get("parse_state"), "html": {"$type": "string"}}
                    operations.append(UpdateOne(expected, {
                        "$set": {"html_present": False, "raw_storage": "archive", "raw_archive": ref,
                                  "parse_state": "archived", "archived_at": int(time.time())},
                        "$unset": {"html": "", "parse_available_at": "", "parse_lease_token": ""},
                    }))
            finally:
                cursor.close()
            counts["changed_or_missing"] += len(by_id) - len(found)
            if operations:
                # MongoDB-specific operations in real deployment; mongomock's
                # bulk compatibility is exercised separately by tests.
                result = db.raw_pages.bulk_write(operations, ordered=False)
                counts["evicted"] += result.modified_count
                counts["changed_or_missing"] += len(operations) - result.modified_count
            processed += len(batch)
            if processed % 1024 < len(batch): progress(f"[archive/evict] processed={processed} {counts}", flush=True)
        result = {**proof, **counts, **restore_result, "finished_at": datetime.now(UTC).isoformat()}
        write_json(root / "eviction.json", result)
        db.raw_archives.update_one({"_id": value["batch_id"]}, {"$set": {"status": "evicted", "eviction": result}})
        return result
    finally:
        index.close()
