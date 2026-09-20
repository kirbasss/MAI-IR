from __future__ import annotations

from urllib.parse import urlsplit

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


def _path_section(url: str) -> str:
    return next((part for part in urlsplit(url).path.split("/") if part), "unknown")


def _category(path_section: str) -> str:
    if path_section == "news":
        return "news"
    if path_section == "reviews":
        return "review"
    return "article"


def _meta_tags(page) -> list[str]:
    return unique_strings([
        str(tag["content"])
        for tag in page.select('meta[property="article:tag"][content]')
    ])


def _body_text(blocks) -> str:
    # A GameMAG review can consist of several consecutive ``content-text``
    # blocks; each is a part of the same material, so retain their order.
    return clean_text("\n\n".join(
        readable_article_text(block) for block in blocks
    ))


def parse_gamemag(html: bytes, url: str) -> ParsedDocument:
    """Parse GameMAG news, articles and reviews from ``div.content-text``."""
    page = soup(html)
    data, metadata = article_metadata(page)
    blocks = page.select("div.content-text")
    text = _body_text(blocks) if blocks else ""

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    author_tag = page.select_one(".authors__name")
    author = clean_text(author_tag.get_text(" ")) if author_tag else data["author"]
    path_section = _path_section(url)
    source_section = data["category"] or path_section

    if blocks:
        metadata["body_selector"] = "div.content-text"
        metadata["body_blocks"] = len(blocks)
    metadata["source_category"] = source_section
    metadata["source_path_section"] = path_section

    return ParsedDocument(
        source="gamemag",
        url=url,
        title=clean_text(str(title)) if title else None,
        author=author,
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=_category(path_section),
        tags=unique_strings(data["tags"] + _meta_tags(page)),
        text=text,
        metadata=metadata,
        parse_error=None if text else "Не найден div.content-text.",
    )
