from __future__ import annotations

from src.model import ParsedDocument
from src.utils import clean_text, find_json_ld, soup, visible_text


def _split_artist_title(value: str) -> tuple[str | None, str | None]:
    """
    Genius often exposes only a combined title like
    "Artist - Song Lyrics | Genius Lyrics" in Open Graph metadata.
    """
    text = clean_text(value.replace("\xa0", " "))
    text = text.split("|", 1)[0].strip()

    if text.lower().endswith(" lyrics"):
        text = text[:-7].strip()

    for separator in (" \u2013 ", " \u2014 ", " - "):
        if separator in text:
            artist, title = text.split(separator, 1)
            return artist.strip() or None, title.strip() or None

    return None, text or None


def parse_genius(html: bytes, url: str) -> ParsedDocument:
    page = soup(html)

    # На Genius текст обычно разбит на несколько контейнеров.
    containers = page.select('[data-lyrics-container="true"]')

    for container in containers:
        # Служебные куски интерфейса Genius не являются lyrics.
        for bad in container.select('[data-exclude-from-selection="true"]'):
            bad.decompose()

    lyrics = clean_text(
        "\n".join(visible_text(container) for container in containers)
    )

    title = None
    artist = None
    album = None
    release_date = None

    # Сначала берём структурированные данные, если они есть.
    song = find_json_ld(page, "MusicRecording")
    if song:
        title = song.get("name") or title
        release_date = song.get("datePublished") or release_date

        by_artist = song.get("byArtist")
        if isinstance(by_artist, dict):
            artist = by_artist.get("name") or artist
        elif isinstance(by_artist, list) and by_artist:
            first = by_artist[0]
            if isinstance(first, dict):
                artist = first.get("name") or artist

        in_album = song.get("inAlbum")
        if isinstance(in_album, dict):
            album = in_album.get("name") or album

    # Fallback'и по мета-тегам.
    og_title = page.select_one('meta[property="og:title"]')
    if not title and og_title:
        meta_artist, meta_title = _split_artist_title(
            og_title.get("content") or ""
        )
        artist = artist or meta_artist
        title = meta_title or title

    if not artist:
        artist_tag = page.select_one(
            '[data-testid="artist_name"], a[href^="/artists/"]'
        )
        if artist_tag:
            artist = clean_text(artist_tag.get_text(" "))

    error = None if lyrics else (
        "Lyrics не найдены: проверьте HTML и актуальность "
        'селектора [data-lyrics-container="true"].'
    )

    return ParsedDocument(
        source="genius",
        url=url,
        title=title,
        artist=artist,
        album=album,
        release_date=release_date,
        lyrics=lyrics,
        parse_error=error,
    )
