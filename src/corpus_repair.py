"""Reclassify saved listing pages and reparse failed documents from stored HTML."""

from __future__ import annotations

import time

from src.parsers import PARSERS
from src.url_policy import is_listing_url


def repair_saved_documents(db, *, apply: bool = False) -> dict[str, int]:
    """Preview or apply non-destructive corrections to existing MongoDB rows."""
    if apply and db.frontier.count_documents({
        "state": "in_progress", "$or": [
            {"available_at": {"$gt": int(time.time())}},
            {"available_at": {"$exists": False}},
        ],
    }):
        raise RuntimeError("Остановите краулер: в очереди есть активная аренда in_progress.")

    result = {"documents_excluded": 0, "frontier_excluded": 0,
              "documents_reparsed": 0, "still_parse_errors": 0,
              "inconsistent_raw": 0}
    redirect_exclusions: set[str] = set()
    for doc in db.documents.find({}, {
        "url": 1, "source": 1, "final_url": 1, "raw_page_id": 1,
        "content_sha256": 1, "parsed.parse_error": 1, "indexable": 1,
        "exclusion_reason": 1,
    }):
        url, source = doc["url"], doc["source"]
        reason = (
            "listing_url" if is_listing_url(source, url)
            else "redirect_to_listing" if is_listing_url(source, doc.get("final_url", ""))
            else None
        )
        if reason:
            if reason == "redirect_to_listing":
                redirect_exclusions.add(url)
            if doc.get("indexable") is not False or doc.get("exclusion_reason") != reason:
                result["documents_excluded"] += 1
                if apply:
                    db.documents.update_one(
                        {"_id": url, "content_sha256": doc.get("content_sha256")},
                        {"$set": {"indexable": False, "exclusion_reason": reason}},
                    )
            continue
        if not isinstance(doc.get("parsed", {}).get("parse_error"), str):
            continue
        raw = db.raw_pages.find_one({"_id": url}, {"html": 1, "content_sha256": 1})
        if (not raw or doc.get("raw_page_id") != url or
                doc.get("content_sha256") != raw.get("content_sha256") or
                not isinstance(raw.get("html"), str)):
            result["inconsistent_raw"] += 1
            continue
        try:
            parsed = PARSERS[source](raw["html"].encode("utf-8"), url).to_dict()
        except Exception as exc:
            result["still_parse_errors"] += 1
            print(f"[repair] {url}: {type(exc).__name__}: {exc}")
            continue
        if parsed.get("parse_error"):
            result["still_parse_errors"] += 1
            continue
        result["documents_reparsed"] += 1
        if apply:
            db.documents.update_one(
                {"_id": url, "content_sha256": doc["content_sha256"]},
                {"$set": {"parsed": parsed, "indexable": True},
                 "$unset": {"exclusion_reason": ""}},
            )

    # Also stop already seeded but not-yet-downloaded listing URLs. Redirects
    # have a publication-shaped original URL and are found via saved documents.
    for task in db.frontier.find({}, {"url": 1, "source": 1, "state": 1}):
        if task.get("state") == "excluded" or not is_listing_url(
            task["source"], task["url"]
        ):
            continue
        result["frontier_excluded"] += 1
        if apply:
            db.frontier.update_one(
                {"_id": task["_id"], "state": {"$ne": "in_progress"}},
                {"$set": {"state": "excluded", "exclusion_reason": "listing_url",
                          "last_error": None},
                 "$unset": {"available_at": "", "lease_token": ""}},
            )

    # A historical article URL that now redirects to a listing is not caught
    # by the URL-only pass above.
    for url in redirect_exclusions:
        task = db.frontier.find_one({"_id": url}, {"state": 1})
        if task and task.get("state") != "excluded":
            result["frontier_excluded"] += 1
            if apply:
                db.frontier.update_one(
                    {"_id": url, "state": {"$ne": "in_progress"}},
                    {"$set": {"state": "excluded", "exclusion_reason": "redirect_to_listing",
                              "last_error": None},
                     "$unset": {"available_at": "", "lease_token": ""}},
                )
    return result
