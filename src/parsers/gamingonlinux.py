from __future__ import annotations

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


BODY_SELECTOR = "article.h-entry .e-content"


def _tags(page) -> list[str]:
    # The page also has a popular-tags widget in the sidebar.  Restrict the
    # selector to the article to keep only labels assigned to this material.
    return unique_strings([
        link.get_text(" ") for link in page.select("article.h-entry .tags.group a")
    ])


def parse_gamingonlinux(html: bytes, url: str) -> ParsedDocument:
    """Parse GamingOnLinux materials using its h-entry microformat block."""
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one(BODY_SELECTOR)
    text = readable_article_text(body) if body else ""

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    schema_type = metadata.get("structured_data_type")
    category = "news" if schema_type == "NewsArticle" else "article"

    if body:
        metadata["body_selector"] = BODY_SELECTOR
    metadata["source_category"] = str(schema_type) if schema_type else "unknown"

    return ParsedDocument(
        source="gamingonlinux",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=category,
        tags=unique_strings(data["tags"] + _tags(page)),
        text=text,
        metadata=metadata,
        parse_error=None if text else f"Не найден {BODY_SELECTOR}.",
    )
