from __future__ import annotations

from urllib.parse import urlsplit

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


def _unique(values: list[str]) -> list[str]:
    return unique_strings(values)


def _category(url: str, schema_type: str | None, title: str | None) -> tuple[str, str]:
    source_category = next(
        (part for part in urlsplit(url).path.split("/") if part), "unknown"
    )
    if schema_type == "NewsArticle" or source_category == "newsdata":
        return "news", source_category
    if source_category == "show" and title and "обзор" in title.lower():
        return "review", source_category
    return "article", source_category


def parse_stopgame(html: bytes, url: str) -> ParsedDocument:
    """Parse StopGame material pages validated on saved 2026-09-18 fixtures."""
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one("article#material_content")
    games: list[str] = []
    if body:
        # These blocks contain links to other publications, not the article.
        for related in body.select("[class*='_read-more']"):
            related.decompose()
        games = _unique([
            clean_text(link.get_text(" "))
            for link in body.select("a.game-link[data-gamelink-game]")
        ])
    text = readable_article_text(body) if body else ""
    tags = _unique(data["tags"] + [
        link.get_text(" ")
        for link in page.select("a[class*='_tag_']")
    ])

    title_tag = page.select_one("h1")
    title = data["title"]
    if title_tag:
        title = clean_text(title_tag.get_text(" ")) or title

    if body:
        metadata["body_selector"] = "article#material_content"
    category, source_category = _category(
        url, metadata.get("structured_data_type"), title if isinstance(title, str) else None
    )
    metadata["source_category"] = source_category
    error = None if text else "Не найден article#material_content: проверьте сохранённый HTML."
    return ParsedDocument(
        source="stopgame",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=category,
        tags=tags,
        games=games,
        text=text,
        metadata=metadata,
        parse_error=error,
    )
