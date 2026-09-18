from __future__ import annotations

from copy import copy
from typing import Any

from src.model import ParsedDocument
from src.utils import clean_text, find_json_ld, meta_content, soup, visible_text


ARTICLE_TYPES = ("NewsArticle", "Article", "Review", "ReportageNewsArticle")
NOISE_WORDS = (
    "comment", "коммент", "recommend", "related", "similar", "advert", "реклам",
    "share", "footer", "sidebar", "navigation", "menu",
)


def _author_name(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("name")
        return clean_text(str(raw)) if raw else None
    if isinstance(value, list):
        names = [_author_name(item) for item in value]
        values = [item for item in names if item]
        return ", ".join(values) or None
    if isinstance(value, str):
        return clean_text(value) or None
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [clean_text(str(item)) for item in value if str(item).strip()]
    return []


def unique_strings(values: list[str]) -> list[str]:
    """Keep first occurrences and remove empty, whitespace-only values."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def schema_names(value: Any) -> list[str]:
    """Extract human-readable names from schema.org ``about`` values."""
    if isinstance(value, dict):
        name = value.get("name")
        return unique_strings([str(name)]) if name else []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(schema_names(item))
        return unique_strings(values)
    if isinstance(value, str):
        return unique_strings([value])
    return []


def article_metadata(page: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return known structured article data and source-preserving metadata."""
    structured = find_json_ld(page, *ARTICLE_TYPES) or {}
    category = structured.get("articleSection")
    if not isinstance(category, str):
        category = meta_content(page, property_="article:section")

    data = {
        "title": structured.get("headline") or meta_content(page, property_="og:title"),
        "author": _author_name(structured.get("author"))
        or meta_content(page, property_="article:author:username"),
        "published_at": structured.get("datePublished")
        or meta_content(page, property_="article:published_time"),
        "category": clean_text(category) if isinstance(category, str) else None,
        "tags": _string_list(structured.get("keywords")),
    }
    original_type = structured.get("@type")
    metadata = {
        "structured_data_type": original_type,
        "description": structured.get("description")
        or meta_content(page, name="description"),
        "date_modified": structured.get("dateModified")
        or meta_content(page, property_="article:modified_time"),
        "schema_about": structured.get("about"),
    }
    return data, {key: value for key, value in metadata.items() if value is not None}


def _is_noise(node: Any) -> bool:
    attributes = " ".join([
        " ".join(node.get("class", [])),
        str(node.get("id", "")),
        str(node.get("role", "")),
    ]).lower()
    return any(word in attributes for word in NOISE_WORDS)


def _clean_node(node: Any) -> Any:
    """Copy node before removing local non-article elements."""
    result = copy(node)
    for bad in result.select("script, style, noscript, form, nav, footer, aside, iframe"):
        bad.decompose()
    for descendant in result.find_all(True):
        # ``find_all`` returns a snapshot. A child whose parent was just
        # decomposed remains in that snapshot but no longer has attributes.
        if descendant.attrs is None:
            continue
        if _is_noise(descendant):
            descendant.decompose()
    return result


def semantic_article_body(page: Any) -> tuple[str, str | None]:
    """Conservative fallback for a source without a validated selector.

    It uses semantic HTML only.  If a site's DOM has no article-like region,
    returning an explicit parser error is safer than indexing navigation.
    """
    candidates: list[tuple[int, str, str]] = []
    for selector in ("[itemprop='articleBody']", "article", "main"):
        for node in page.select(selector):
            if _is_noise(node):
                continue
            text = visible_text(_clean_node(node))
            words = len(text.split())
            if words >= 30:
                candidates.append((words, text, selector))
    if not candidates:
        return "", None
    _, text, selector = max(candidates, key=lambda item: item[0])
    return text, selector


def parse_semantic_article(source: str, html: bytes, url: str) -> ParsedDocument:
    page = soup(html)
    data, metadata = article_metadata(page)
    text, selector = semantic_article_body(page)
    if selector:
        metadata["body_selector"] = selector
        metadata["parser_mode"] = "semantic_fallback_requires_fixture_validation"
    error = None if text else "Не найден семантический блок текста статьи."
    return ParsedDocument(
        source=source,
        url=url,
        title=clean_text(str(data["title"])) if data["title"] else None,
        author=data["author"],
        published_at=str(data["published_at"]) if data["published_at"] else None,
        category=data["category"],
        tags=data["tags"],
        text=text,
        metadata=metadata,
        parse_error=error,
    )
