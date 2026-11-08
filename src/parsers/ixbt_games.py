from __future__ import annotations

import json
from urllib.parse import urlsplit

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


SECTION_CATEGORY = {
    "Новости": "news",
    "Статьи": "article",
    "Обзоры": "review",
}


def _hydrated_body(page, url: str):
    """Use only the current publication's blocks, not sidebar/recommended stories."""
    for script in page.select('script[type="application/json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("props"), dict):
            continue
        # Inertia's payload URL binds the article data to the requested page.
        payload_url = payload.get("url")
        if not isinstance(payload_url, str):
            continue
        try:
            matches_page = urlsplit(payload_url).path.rstrip("/") == urlsplit(url).path.rstrip("/")
        except ValueError:
            continue
        if not matches_page:
            continue
        publication = payload["props"].get("publication")
        if not isinstance(publication, dict) or not isinstance(publication.get("blocks"), list):
            continue
        fragments = [block["html"] for block in publication["blocks"]
                     if isinstance(block, dict) and isinstance(block.get("html"), str)]
        if fragments:
            return soup(("<div>" + "\n".join(fragments) + "</div>").encode("utf-8")).div
    return None


def parse_ixbt_games(html: bytes, url: str) -> ParsedDocument:
    """Parse IXBT.games fixtures from news, articles and reviews sections.

    The dynamic numeric part of the ``publication-<id>`` element is not used
    as a selector: the stable attributes are the id prefix and ``prose``.
    """
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one('div[id^="publication-"].prose')
    text = readable_article_text(body) if body else ""
    if body:
        metadata["body_selector"] = 'div[id^="publication-"].prose'
    if not text:
        hydrated = _hydrated_body(page, url)
        if hydrated is not None:
            body = hydrated
            text = readable_article_text(body)
            metadata["body_selector"] = "props.publication.blocks[].html"
            metadata["parser_mode"] = "embedded_publication_json"
    section = data["category"]

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    if section:
        metadata["source_category"] = section

    return ParsedDocument(
        source="ixbt_games",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=SECTION_CATEGORY.get(str(section), "article"),
        tags=unique_strings(data["tags"]),
        text=text,
        metadata=metadata,
        parse_error=None if text else 'Не найден div[id^="publication-"].prose.',
    )
