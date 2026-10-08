"""Idempotent migration from embedded HTML to the raw_pages collection."""

from __future__ import annotations


RAW_FIELDS = (
    "url", "source", "html", "fetched_at", "content_sha256",
    "final_url", "http_status", "content_type",
)


def migrate_raw_pages(db, *, apply: bool = False) -> dict[str, int | bool]:
    """Copy and verify each raw page before removing HTML from documents."""
    documents = db["documents"]
    raw_pages = db["raw_pages"]
    legacy = documents.count_documents({"html": {"$exists": True}})
    migrated = 0

    for document in documents.find({"html": {"$exists": True}}):
        url = document["_id"]
        if any(field not in document for field in ("url", "source", "html", "fetched_at", "content_sha256")):
            raise ValueError(f"Неполный исходный документ: {url}")
        raw_page = {"_id": url, **{
            field: document[field] for field in RAW_FIELDS if field in document
        }}
        existing = raw_pages.find_one({"_id": url})
        if existing and (
            existing.get("content_sha256") != raw_page["content_sha256"]
            or existing.get("html") != raw_page["html"]
        ):
            raise ValueError(f"Конфликт с существующей raw_pages: {url}")
        if not apply:
            continue
        if not existing:
            raw_pages.insert_one(raw_page)
        result = documents.update_one(
            {"_id": url, "content_sha256": document["content_sha256"],
             "html": {"$exists": True}},
            {"$set": {"raw_page_id": url}, "$unset": {"html": ""}},
        )
        if result.matched_count != 1:
            raise RuntimeError(f"Документ изменился во время переноса: {url}")
        migrated += 1

    remaining = documents.count_documents({"html": {"$exists": True}})
    missing_reference = documents.count_documents({"raw_page_id": {"$exists": False}})
    if apply and (remaining or missing_reference):
        raise RuntimeError(
            f"Перенос не завершён: HTML в documents={remaining}, "
            f"документов без raw_page_id={missing_reference}"
        )
    return {
        "dry_run": not apply,
        "legacy_documents": legacy,
        "migrated": migrated,
        "remaining_html": remaining,
        "documents_without_raw_reference": missing_reference,
        "raw_pages": raw_pages.estimated_document_count(),
    }
