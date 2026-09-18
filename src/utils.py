from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


SPACE_RE = re.compile(r"[ \t\u00a0]+")
BLANK_RE = re.compile(r"\n{3,}")
TRACKING_PARAMETERS = {"fbclid", "gclid", "yclid", "_ga", "_gl"}


def clean_text(text: str) -> str:
    """Normalise whitespace while keeping paragraph and line boundaries."""
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines.append(SPACE_RE.sub(" ", line).strip())
    return BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def visible_text(node: Any) -> str:
    """Get visible text, treating HTML line breaks as line breaks."""
    for br in node.find_all("br"):
        br.replace_with("\n")
    return clean_text(node.get_text("\n"))


def readable_article_text(node: Any) -> str:
    """Extract prose without splitting every inline link into a new line."""
    for br in node.find_all("br"):
        br.replace_with("\n")
    for block in node.find_all(("p", "h2", "h3", "h4", "li", "blockquote")):
        block.append("\n\n")
    return clean_text(node.get_text(" "))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dump_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def soup(html: bytes) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def get_json_ld_objects(page: BeautifulSoup) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tag in page.select('script[type="application/ld+json"]'):
        raw = tag.string or tag.get_text()
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            result.append(data)
        elif isinstance(data, list):
            result.extend(item for item in data if isinstance(item, dict))
    return result


def walk_json(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph_items = value.get("@graph")
        if isinstance(graph_items, list):
            for item in graph_items:
                yield from walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json(item)


def find_json_ld(page: BeautifulSoup, *types: str) -> dict[str, Any] | None:
    wanted = set(types)
    for obj in get_json_ld_objects(page):
        for candidate in walk_json(obj):
            raw_type = candidate.get("@type")
            candidate_types = {raw_type} if isinstance(raw_type, str) else set(raw_type or [])
            if candidate_types & wanted:
                return candidate
    return None


def meta_content(
    page: BeautifulSoup,
    *,
    name: str | None = None,
    property_: str | None = None,
) -> str | None:
    selector = f'meta[name="{name}"]' if name else f'meta[property="{property_}"]'
    tag = page.select_one(selector)
    if tag and tag.get("content"):
        return clean_text(str(tag["content"])) or None
    return None


def canonicalize_url(url: str) -> str:
    """Remove only tracking parameters; preserve meaningful URL queries."""
    parsed = urlsplit(url)
    parameters = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMETERS and not key.lower().startswith("utm_")
    ]
    return urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
        urlencode(parameters, doseq=True), "",
    ))
