from __future__ import annotations

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


TYPE_CATEGORY = {
    "NewsArticle": "news",
    "Review": "review",
    "Article": "article",
}


def _tags(page) -> list[str]:
    # Desktop and mobile versions duplicate this block; retain each tag once.
    return unique_strings([
        link.get_text(" ").lstrip("# ")
        for link in page.select('div[class*="TagsOfArticle_tags"] a')
    ])


def parse_igromania(html: bytes, url: str) -> ParsedDocument:
    """Parse Igromania materials validated on news, article and review HTML."""
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one('div[class*="material-content_"]')
    schema_type = metadata.get("structured_data_type")

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    if body:
        metadata["body_selector"] = 'div[class*="material-content_"]'
    if data["category"]:
        metadata["source_category"] = data["category"]

    text = readable_article_text(body) if body else ""
    return ParsedDocument(
        source="igromania",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=TYPE_CATEGORY.get(str(schema_type), "article"),
        tags=unique_strings(data["tags"] + _tags(page)),
        text=text,
        metadata=metadata,
        parse_error=None if text else 'Не найден div[class*="material-content_"].',
    )
