from __future__ import annotations

import json
from typing import Any

from src.model import ParsedDocument
from src.utils import clean_text, find_json_ld, soup


LYRIC_SELECTORS = (
    # В разные периоды Musixmatch использовал разные классы.
    ".lyrics__content__ok",
    "[class*='lyrics__content']",
    "[data-testid*='lyrics']",
)


def _next_data(page) -> dict[str, Any] | None:
    tag = page.select_one("script#__NEXT_DATA__")
    if tag is None:
        return None

    raw = tag.string or tag.get_text()
    if not raw.strip():
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


def _track_info_payload(page) -> dict[str, Any]:
    data = _next_data(page) or {}
    props = data.get("props")
    if not isinstance(props, dict):
        return {}

    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        return {}

    page_data = page_props.get("data")
    if not isinstance(page_data, dict):
        return {}

    track_info = page_data.get("trackInfo")
    if not isinstance(track_info, dict):
        return {}

    payload = track_info.get("data")
    return payload if isinstance(payload, dict) else {}


def _lyrics_from_structure(payload: dict[str, Any]) -> str:
    sections = payload.get("trackStructureList")
    if not isinstance(sections, list):
        return ""

    blocks: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue

        lines = []
        for line in section.get("lines") or []:
            if isinstance(line, dict) and isinstance(line.get("text"), str):
                text = line["text"].strip()
                if text:
                    lines.append(text)

        if lines:
            blocks.append("\n".join(lines))

    return clean_text("\n\n".join(blocks))


def parse_musixmatch(html: bytes, url: str) -> ParsedDocument:
    page = soup(html)

    payload = _track_info_payload(page)
    track = payload.get("track")
    if not isinstance(track, dict):
        track = {}

    lyrics_data = payload.get("lyrics")
    if not isinstance(lyrics_data, dict):
        lyrics_data = {}

    lyrics = clean_text(str(lyrics_data.get("body") or ""))
    if not lyrics:
        lyrics = _lyrics_from_structure(payload)

    blocks = []
    seen = set()

    if not lyrics:
        for selector in LYRIC_SELECTORS:
            for node in page.select(selector):
                text = clean_text(node.get_text("\n"))
                if text and text not in seen:
                    seen.add(text)
                    blocks.append(text)

            if blocks:
                break

        lyrics = clean_text("\n".join(blocks))

    title = None
    artist = None
    album = None
    release_date = None
    metadata: dict[str, Any] = {}

    if track:
        title = track.get("name") or title
        artist = track.get("artistName") or artist
        album = track.get("albumName") or album
        release_date = track.get("releaseDate") or release_date

        metadata.update(
            {
                "track_id": track.get("id"),
                "common_track_id": track.get("commonTrackId"),
                "album_id": track.get("albumId"),
                "artist_id": track.get("artistId"),
                "vanity_id": track.get("vanityId"),
                "spotify_id": track.get("spotifyId"),
                "has_sync": track.get("hasSync"),
                "has_track_structure": track.get("hasTrackStructure"),
                "is_explicit": track.get("isExplicit"),
            }
        )

    if lyrics_data:
        metadata.update(
            {
                "lyrics_id": lyrics_data.get("id"),
                "lyrics_language": lyrics_data.get("language"),
                "lyrics_language_description": lyrics_data.get(
                    "languageDescription"
                ),
                "lyrics_verifier": lyrics_data.get("verifier"),
                "copyright": lyrics_data.get("copyright"),
            }
        )

    recording = find_json_ld(page, "MusicRecording")
    if recording:
        title = title or recording.get("name")
        release_date = release_date or recording.get("datePublished")

        by_artist = recording.get("byArtist")
        if not artist and isinstance(by_artist, dict):
            artist = by_artist.get("name")

        in_album = recording.get("inAlbum")
        if not album and isinstance(in_album, dict):
            album = in_album.get("name")

    if not title:
        og_title = page.select_one('meta[property="og:title"]')
        if og_title:
            title = og_title.get("content")

    error = None if lyrics else (
        "Lyrics не найдены. Возможные причины: динамическая загрузка, "
        "изменившаяся разметка или защитная страница Musixmatch. "
        "Сырой HTML сохранён для анализа."
    )

    return ParsedDocument(
        source="musixmatch",
        url=url,
        title=title,
        artist=artist,
        album=album,
        release_date=release_date,
        lyrics=lyrics,
        metadata={
            key: value
            for key, value in metadata.items()
            if value is not None
        },
        parse_error=error,
    )
