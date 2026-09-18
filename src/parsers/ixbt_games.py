from __future__ import annotations

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


SECTION_CATEGORY = {
    "Новости": "news",
    "Статьи": "article",
    "Обзоры": "review",
}


def parse_ixbt_games(html: bytes, url: str) -> ParsedDocument:
    """Parse IXBT.games fixtures from news, articles and reviews sections.

    The dynamic numeric part of the ``publication-<id>`` element is not used
    as a selector: the stable attributes are the id prefix and ``prose``.
    """
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one('div[id^="publication-"].prose')
    section = data["category"]

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    if body:
        metadata["body_selector"] = 'div[id^="publication-"].prose'
    if section:
        metadata["source_category"] = section

    text = readable_article_text(body) if body else ""
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
