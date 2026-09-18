from __future__ import annotations

from bs4 import Comment

from src.model import ParsedDocument
from src.utils import clean_text, soup, visible_text


def _lyrics_from_comment(page) -> str:
    """
    AZLyrics традиционно помещает текст в classless <div>
    непосредственно после служебного HTML-комментария.
    Ищем по структуре, а не по случайному CSS-классу.
    """
    for comment in page.find_all(string=lambda x: isinstance(x, Comment)):
        normalized = comment.lower()
        if "usage of azlyrics.com content" in normalized:
            node = comment.parent.find_next_sibling("div")
            if node is not None:
                return visible_text(node)
    return ""


def _lyrics_from_classless_div(page) -> str:
    """
    Fallback: среди div без class/id выбираем наиболее похожий
    на полноценный текст песни.
    """
    candidates: list[str] = []

    for div in page.find_all("div"):
        if div.get("class") or div.get("id"):
            continue

        text = visible_text(div)
        if len(text) >= 150 and text.count("\n") >= 3:
            candidates.append(text)

    return max(candidates, key=len, default="")


def parse_azlyrics(html: bytes, url: str) -> ParsedDocument:
    page = soup(html)

    lyrics = _lyrics_from_comment(page) or _lyrics_from_classless_div(page)

    title = None
    artist = None
    album = None

    title_tag = page.select_one("h1")
    if title_tag:
        title = clean_text(title_tag.get_text(" "))
        # На странице часто написано: "Song Name" lyrics
        if title.lower().endswith(" lyrics"):
            title = title[:-7].strip().strip('"')

    artist_tag = page.select_one(".lyricsh h2, h2")
    if artist_tag:
        artist = clean_text(artist_tag.get_text(" "))
        artist = artist.removesuffix(" Lyrics").strip()

    # Альбом у AZLyrics может находиться рядом с текстом в служебной подписи.
    album_tag = page.find(string=lambda s: isinstance(s, str) and "album:" in s.lower())
    if album_tag:
        parent = album_tag.parent
        album = clean_text(parent.get_text(" ")) if parent else clean_text(album_tag)

    error = None if lyrics else (
        "Lyrics не найдены. AZLyrics мог изменить структуру страницы "
        "или вернуть защитную/служебную страницу."
    )

    return ParsedDocument(
        source="azlyrics",
        url=url,
        title=title,
        artist=artist,
        album=album,
        lyrics=lyrics,
        parse_error=error,
    )
