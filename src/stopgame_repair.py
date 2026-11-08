"""Repair StopGame news sitemap aliases in inventories and the Mongo frontier."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from src.utils import canonicalize_source_url


OLD_NEWS = re.compile(r"^https://(?:www\.)?stopgame\.ru/news/\d+(?:/|$)")


def repair_inventory_file(path: Path, *, apply: bool = False) -> int:
    """Count or replace aliased URLs in one TSV, keeping a copy of the original."""
    temporary: Path | None = None
    changed = 0
    seen_news: set[str] = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            fields = reader.fieldnames
            if not fields or not {"source", "url"}.issubset(fields):
                raise ValueError(f"Некорректный URL-инвентарь: {path}")
            if apply:
                fd, name = tempfile.mkstemp(prefix=".stopgame-repair-", suffix=".tsv", dir=path.parent)
                temporary = Path(name)
                output = os.fdopen(fd, "w", encoding="utf-8", newline="")
                writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t")
                writer.writeheader()
            else:
                output = None
                writer = None
            try:
                for row in reader:
                    url = row["url"]
                    if row["source"] == "stopgame":
                        fixed = canonicalize_source_url("stopgame", url)
                        if fixed != url:
                            # Only the known sitemap alias is repaired here.
                            if not OLD_NEWS.match(url):
                                raise ValueError(f"Неожиданное изменение URL: {url}")
                            changed += 1
                            row["url"] = fixed
                        if OLD_NEWS.match(url) or "/newsdata/" in url:
                            if fixed in seen_news:
                                raise ValueError(f"Дублирующийся URL после исправления: {fixed}")
                            seen_news.add(fixed)
                    if writer:
                        writer.writerow(row)
            finally:
                if output:
                    output.close()
        if apply and changed:
            backup = path.with_name(path.name + ".before_stopgame_news_repair.bak")
            if backup.exists():
                raise FileExistsError(f"Резервная копия уже существует: {backup}")
            shutil.copy2(path, backup)
            os.replace(temporary, path)
            temporary = None
        return changed
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def repair_frontier(db, *, apply: bool = False) -> int:
    """Replace old queue IDs; archive originals so the operation is recoverable."""
    frontier = db["frontier"]
    archived = db["frontier_url_repairs"]
    count = 0
    query = {"source": "stopgame", "url": {"$regex": OLD_NEWS.pattern}}
    for old in frontier.find(query):
        old_url = old["url"]
        fixed = canonicalize_source_url("stopgame", old_url)
        if fixed == old_url:
            continue
        if old["state"] in {"in_progress", "done"}:
            raise RuntimeError(f"Не изменён активный или загруженный URL: {old_url}")
        if db["documents"].find_one({"_id": old_url}) or db["raw_pages"].find_one({"_id": old_url}):
            raise RuntimeError(f"У старого URL уже есть сохранённая страница: {old_url}")
        existing = frontier.find_one({"_id": fixed})
        if existing and existing.get("source") != "stopgame":
            raise RuntimeError(f"Новый URL занят другим источником: {fixed}")
        count += 1
        if not apply:
            continue
        replacement = {
            "url": fixed, "source": "stopgame",
            "category_hint": old.get("category_hint"), "state": "pending",
            "available_at": 0, "attempts": 0,
            "queue_order": int.from_bytes(
                hashlib.sha256(fixed.encode("utf-8")).digest()[:8], "big"
            ) & 0x7fff_ffff_ffff_ffff,
        }
        frontier.update_one({"_id": fixed}, {"$setOnInsert": replacement}, upsert=True)
        archived.update_one(
            {"_id": old_url},
            {"$setOnInsert": {
                "original": old, "replacement_url": fixed, "archived_at": int(time.time()),
            }},
            upsert=True,
        )
        deleted = frontier.delete_one({
            "_id": old_url, "state": old["state"], "attempts": old.get("attempts", 0),
        })
        if deleted.deleted_count != 1:
            raise RuntimeError(f"Задание изменилось во время исправления: {old_url}")
    return count
