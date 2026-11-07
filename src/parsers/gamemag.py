from __future__ import annotations

from copy import copy
from urllib.parse import urlsplit

from src.model import ParsedDocument
from src.parsers.common import article_metadata, unique_strings
from src.utils import clean_text, readable_article_text, soup


LEGACY_BODY = "div.content-text"
NEWS_BODY = "article.news-details div.content-row__content"
REVIEW_BODY = (
    "article.reviews-details div.content-row__section, "
    "article.reviews-details div.content-row__center--lg:not(.js-comment-section)"
)
MEDIA = "img, video, iframe, embed, object"
BODY_NOISE = (
    "script, style, noscript, template, nav, aside, form, "
    ".list-tags, .subscription-channel, .news-details__games, "
    ".news-details__emotion-rating, .emotion-rating, .reviews-details__score, "
    ".visual-overview, .news-history, .sidebar-content__inner, "
    ".social-nav, .js-comment-section, .comments"
)


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


def _article_body(page):
    """Prefer the legacy layout; never mix duplicate desktop/layout variants."""
    empty = ([], None, "")
    for selector in (LEGACY_BODY, NEWS_BODY, REVIEW_BODY):
        candidates = page.select(selector)
        identities = {id(node) for node in candidates}
        # A section may itself be nested in the final review column. Keep
        # outermost matches only, in DOM order, to avoid repeating paragraphs.
        blocks = [node for node in candidates
                  if not any(id(parent) in identities for parent in node.parents)]
        cleaned = []
        for block in blocks:
            node = copy(block)
            for noise in node.select(BODY_NOISE):
                # A parent and its descendants can occur in the same snapshot.
                if noise.attrs is not None:
                    noise.decompose()
            cleaned.append(node)
        text = _body_text(cleaned)
        if text or any(node.select_one(MEDIA) for node in cleaned):
            return cleaned, selector, text
        if cleaned and not empty[0]:
            empty = (cleaned, selector, "")
    return empty


def parse_gamemag(html: bytes | str, url: str) -> ParsedDocument:
    """Parse legacy and current GameMAG news, specials and multi-part reviews."""
    page = soup(html)
    data, metadata = article_metadata(page)
    blocks, selector, text = _article_body(page)

    title_tag = page.select_one("h1")
    title = clean_text(title_tag.get_text(" ")) if title_tag else data["title"]
    author_tag = page.select_one(".authors__name")
    author = clean_text(author_tag.get_text(" ")) if author_tag else data["author"]
    path_section = _path_section(url)
    source_section = data["category"] or path_section

    if blocks:
        metadata["body_selector"] = selector
        metadata["body_blocks"] = len(blocks)
    metadata["source_category"] = source_section
    metadata["source_path_section"] = path_section
    media_only = bool(blocks and title and not text and any(
        block.select_one(MEDIA) for block in blocks
    ))
    if media_only:
        metadata["content_kind"] = "media_only"

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
        parse_error=None if text or media_only else (
            "Не найден текст в контейнерах GameMAG: div.content-text, "
            "article.news-details или article.reviews-details."
        ),
    )
