from __future__ import annotations

from copy import copy

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, meta_content, readable_article_text, soup


BODY_SELECTOR = "div#article-body"


def _category(page) -> str:
    raw = meta_content(page, property_="mrf:tags") or ""
    values = {item.casefold() for item in raw.split(";")}
    if "articletype:news" in values:
        return "news"
    if "articletype:reviews" in values or "articletype:review" in values:
        return "review"
    return "article"


def _meta_tags(page) -> list[str]:
    return unique_strings([
        str(tag["content"])
        for tag in page.select('meta[property="article:tag"][content]')
    ])


def _body_text(body) -> str:
    """Remove widgets adjacent to prose inside PC Gamer's article body."""
    cleaned = copy(body)
    for selector in (
        "script, style, .ad-unit, .utility-bar, #utility-bar, "
        "[data-analytics-id='utility-bar'], [data-jwp-carousel], aside, .ecom-root, "
        ".inline-gallery, .inlinegallery, .youtube-video, .youtube-facade, "
        "[class*='newsletter'], [class*='video']",
    ):
        for node in cleaned.select(selector):
            node.decompose()
    return readable_article_text(cleaned)


def parse_pcgamer(html: bytes, url: str) -> ParsedDocument:
    """Parse PC Gamer articles from the stable ``article-body`` container."""
    page = soup(html)
    data, metadata = article_metadata(page)
    body = page.select_one(BODY_SELECTOR)
    text = _body_text(body) if body else ""

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    author = meta_content(page, property_="mrf:authors") or data["author"]
    section = data["category"]

    if body:
        metadata["body_selector"] = BODY_SELECTOR
    if section:
        metadata["source_category"] = section

    return ParsedDocument(
        source="pcgamer",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=author,
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=_category(page),
        tags=unique_strings(data["tags"] + _meta_tags(page)),
        text=text,
        metadata=metadata,
        parse_error=None if text else f"Не найден {BODY_SELECTOR}.",
    )
