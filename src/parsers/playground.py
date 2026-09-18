from __future__ import annotations

from urllib.parse import urlsplit

from src.model import ParsedDocument
from src.parsers.common import article_metadata, schema_names, unique_strings
from src.utils import clean_text, readable_article_text, soup


PATH_CATEGORY = {
    "news": "news",
    "opinion": "review",
}


def _source_category(url: str) -> str | None:
    # A material URL has the stable shape /<game>/<section>/<slug>-<id>.
    parts = [part for part in urlsplit(url).path.split("/") if part]
    return parts[1] if len(parts) >= 2 else None


def parse_playground(html: bytes, url: str) -> ParsedDocument:
    """Parse PlayGround material pages validated on three local fixtures.

    ``div.article-content`` contains the prose only; its outer ``article``
    also contains the title, game card, comments and recommendations.
    """
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one("div.article-content")
    source_category = _source_category(url)
    games = schema_names(metadata.get("schema_about"))

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    if body:
        metadata["body_selector"] = "div.article-content"
    if source_category:
        metadata["source_category"] = source_category

    text = readable_article_text(body) if body else ""
    return ParsedDocument(
        source="playground",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=PATH_CATEGORY.get(source_category or "", data["category"]),
        tags=unique_strings(data["tags"]),
        games=games,
        text=text,
        metadata=metadata,
        parse_error=None if text else "Не найден div.article-content.",
    )
