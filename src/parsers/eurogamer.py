from __future__ import annotations

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


BODY_SELECTOR = "div.article_body_content"


def _meta_tags(page) -> list[str]:
    return unique_strings([
        str(tag["content"])
        for tag in page.select('meta[property="article:tag"][content]')
    ])


def _category(schema_type: str | None, source_section: str | None) -> str:
    if schema_type == "Review":
        return "review"
    if schema_type == "NewsArticle" or (source_section or "").casefold() == "news":
        return "news"
    # Guides and features are materials of the common article class in the
    # unified corpus taxonomy; the original section remains in metadata.
    return "article"


def parse_eurogamer(html: bytes, url: str) -> ParsedDocument:
    """Parse Eurogamer features and guides from their article-body block."""
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one(BODY_SELECTOR)
    text = readable_article_text(body) if body else ""

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    source_section = data["category"]
    schema_type = metadata.get("structured_data_type")

    if body:
        metadata["body_selector"] = BODY_SELECTOR
    if source_section:
        metadata["source_category"] = source_section

    return ParsedDocument(
        source="eurogamer",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=_category(schema_type if isinstance(schema_type, str) else None, source_section),
        tags=unique_strings(data["tags"] + _meta_tags(page)),
        text=text,
        metadata=metadata,
        parse_error=None if text else f"Не найден {BODY_SELECTOR}.",
    )
