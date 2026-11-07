"""Durable parsing jobs embedded in raw_pages; this worker never makes HTTP requests."""
from __future__ import annotations

import time
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DocumentTooLarge, DuplicateKeyError

from src.downloader import _response_class
from src.disk_guard import DiskGuard
from src.parsers import PARSERS
from src.url_policy import is_listing_url
from src.worker_lock import exclusive_worker
from src.raw_archive import read_archived_raw


class ParserWorker:
    def __init__(self, config, db, *, now=None):
        self.config = config
        self.raw = db["raw_pages"]
        self.documents = db["documents"]
        self.now = now or time.time
        self.disk_guard = DiskGuard(config, db)

    @staticmethod
    def source_query(query, sources):
        if sources is None:
            return query
        if isinstance(sources, str):
            raise ValueError("sources должен быть списком сайтов")
        selected = tuple(dict.fromkeys(sources))
        if not selected or any(source not in PARSERS for source in selected):
            raise ValueError("Укажите непустой список известных сайтов из PARSERS")
        return {**query, "source": {"$in": selected}}

    def prepare(self, *, sources=None):
        self.disk_guard.check()
        self.raw.create_index([("parse_state", 1), ("parse_available_at", 1), ("fetched_at", 1), ("_id", 1)])
        # Only one parser coordinator per local database. A stopped predecessor
        # cannot still own jobs; leave HTTP frontier leases entirely untouched.
        self.raw.update_many(self.source_query({"parse_state": {"$in": ["in_progress", "publishing"]}}, sources), {
            "$set": {"parse_state": "pending", "parse_available_at": 0},
            "$unset": {"parse_lease_token": ""},
        })

    def claim(self, *, sources=None):
        self.disk_guard.check()
        return self.raw.find_one_and_update(
            self.source_query({"parse_state": "pending", "parse_available_at": {"$lte": int(self.now())}}, sources),
            {"$set": {"parse_state": "in_progress", "parse_lease_token": uuid.uuid4().hex,
                      "parse_available_at": int(self.now()) + self.config.lease_seconds},
             "$inc": {"parse_attempts": 1}},
            sort=[("parse_available_at", 1), ("fetched_at", 1), ("_id", 1)],
            return_document=ReturnDocument.AFTER,
        )

    @staticmethod
    def owned(job):
        return {"_id": job["_id"], "raw_generation": job.get("raw_generation"),
                "content_sha256": job.get("content_sha256"), "parse_lease_token": job["parse_lease_token"]}

    def queue_saved(self, *, errors_only=False, sources=None):
        """Explicit, offline reparse; legacy raw rows need no migration or HTTP."""
        self.disk_guard.check()
        query = {"$or": [{"html": {"$type": "string"}}, {"raw_storage": "archive"}],
                 "parse_state": {"$nin": ["in_progress", "publishing"]}}
        if errors_only:
            query["parse_state"] = "error"
        return self.raw.update_many(self.source_query(query, sources), {
            "$set": {"parse_state": "pending", "parse_available_at": 0, "parse_attempts": 0},
            "$unset": {"parse_lease_token": "", "parse_error": ""},
        }).modified_count

    def process(self, job):
        self.disk_guard.check()
        started = time.perf_counter()
        url, source = job["_id"], job["source"]
        previous = self.documents.find_one({"_id": url}, {
            "raw_generation": 1, "content_sha256": 1, "fetched_at": 1,
        })
        # Missing/corrupt removable archives are storage failures, not selector
        # errors: stop outside the parse-error handler and retain this lease.
        archived = read_archived_raw(job) if job.get("raw_storage") == "archive" else None
        try:
            if is_listing_url(source, url) or is_listing_url(source, job.get("final_url", "")):
                raise ValueError("Страница-каталог, не публикация")
            html = archived["html"] if archived is not None else job["html"]
            classification = _response_class(job.get("http_status", 200), html)
            if classification != "valid_content_page":
                raise ValueError(f"Сохранённый ответ не является HTML-публикацией: {classification}")
            # HTML in MongoDB is already Unicode. Feeding UTF-8 bytes with an
            # old <meta charset=windows-1251> would corrupt Cyrillic a second time.
            parsed = PARSERS[source](html, url).to_dict()
        except Exception as exc:
            parsed = {"parse_error": f"{type(exc).__name__}: {exc}"}
        parse_ms = round((time.perf_counter() - started) * 1000, 2)
        owned = self.owned(job)
        self.disk_guard.check()
        if not self.raw.update_one(owned, {"$set": {"parse_state": "publishing"}}).matched_count:
            return "superseded"
        document = {key: job[key] for key in (
            "url", "source", "fetched_at", "content_sha256", "final_url", "http_status", "content_type",
        ) if key in job}
        document.update({"_id": url, "url": url, "raw_page_id": url,
                         "raw_generation": job.get("raw_generation"), "parsed": parsed,
                         "parsed_at": int(self.now()), "parse_ms": parse_ms,
                         "indexable": not bool(parsed.get("parse_error"))})
        # Never replace a document that another publisher changed while we
        # parsed. No transaction is required and no upsert may bypass this CAS.
        try:
            published = self._publish(url, previous, document)
        except DocumentTooLarge:
            parsed = {"parse_error": "Результат разбора превышает предел документа MongoDB"}
            document.update({"parsed": parsed, "indexable": False})
            published = self._publish(url, previous, document)
        if not published:
            self.raw.update_one(owned, {
                "$set": {"parse_state": "error", "parse_error": "Конфликт публикации: documents изменён другим обработчиком"},
                "$unset": {"parse_lease_token": "", "parse_available_at": ""},
            })
            return "superseded"
        error = parsed.get("parse_error")
        finished = self.raw.update_one(owned, {
            "$set": {"parse_state": "error" if error else "archived" if archived is not None else "done", "parse_error": error,
                      "parsed_at": int(self.now()), "parse_ms": parse_ms},
            "$unset": {"parse_lease_token": "", "parse_available_at": ""},
        })
        # A new download may have replaced raw while we published. The parsed
        # document keeps its OWN generation/hash; the new raw remains pending.
        # Collections are eventually consistent, not an atomic two-row snapshot.
        return ("error" if error else "done") if finished.matched_count else "superseded"

    def _publish(self, url, previous, document):
        self.disk_guard.check()
        if previous is None:
            try:
                self.documents.insert_one(document)
                return True
            except DuplicateKeyError:
                return False
        expected = {"_id": url, **{key: previous.get(key) for key in (
            "raw_generation", "content_sha256", "fetched_at",
        )}}
        return bool(self.documents.replace_one(expected, document).matched_count)

    def run(self, *, follow=False, max_documents=None, reparse=False, retry_errors=False, sources=None):
        if max_documents is not None and max_documents < 1:
            raise ValueError("max_documents должен быть положительным")
        # Validate before creating indexes/recovering leases. Freeze generators
        # so every queue operation uses the same explicitly selected sources.
        if sources is not None:
            sources = self.source_query({}, sources)["source"]["$in"]
        counts = {"done": 0, "error": 0, "superseded": 0}
        with exclusive_worker(self.config.db_name, "parser"):
            self.prepare(sources=sources)
            if reparse or retry_errors:
                print(f"[parser] Поставлено на разбор: {self.queue_saved(errors_only=retry_errors, sources=sources)}")
            while max_documents is None or sum(counts.values()) < max_documents:
                job = self.claim(sources=sources)
                if job is None:
                    if not follow:
                        break
                    time.sleep(min(self.config.poll_seconds, 2))
                    continue
                result = self.process(job)
                counts[result] += 1
                print(f"[parser] {result:10s} {job['_id']}", flush=True)
        print(f"[parser] {counts}")
        return counts
