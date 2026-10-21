from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit, urlunsplit

import requests

from src.parsers import PARSERS
from src.utils import canonicalize_url, dump_json, sha256_text, soup


USER_AGENT = (
    "MAI-IR-Lab/1.0 "
    "(educational corpus collection)"
)
SOURCE_HOSTS = {
    "playground": {"playground.ru", "www.playground.ru"},
    "stopgame": {"stopgame.ru", "www.stopgame.ru"},
    "ixbt_games": {"ixbt.games", "www.ixbt.games"},
    "igromania": {"igromania.ru", "www.igromania.ru"},
    "gamemag": {"gamemag.ru", "www.gamemag.ru"},
    "gamingonlinux": {"gamingonlinux.com", "www.gamingonlinux.com"},
    "pcgamer": {"pcgamer.com", "www.pcgamer.com"},
    "eurogamer": {"eurogamer.net", "www.eurogamer.net"},
}
CHALLENGE_MARKERS = (
    "verify you are human", "checking your browser", "just a moment",
    "attention required", "access denied", "security check", "robot check",
    "подтвердите, что вы не робот", "подтвердите, что вы человек",
    "проверка браузера", "проверка безопасности", "доступ ограничен",
)
ARTICLE_BODY_SELECTORS = (
    "article#material_content, div.article-content, div[id^='publication-'].prose, "
    "div[class*='material-content_'], div.content-text, article.h-entry .e-content, "
    "#article-body, #gallery-body .gallery-text, div.article_body_content"
)


@dataclass(frozen=True)
class RobotsResult:
    source: str
    robots_url: str
    allowed: bool
    http_status: int | None
    error: str | None = None


def safe_name(url: str) -> str:
    parsed = urlsplit(url)
    tail = parsed.path.strip("/").replace("/", "__") or "index"
    tail = "".join(char if char.isalnum() or char in "._-" else "_" for char in tail)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{tail[:100]}__{digest}"


def _robots_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


def _valid_source_url(source: str, url: str) -> bool:
    return urlsplit(url).hostname in SOURCE_HOSTS[source]


def _response_class(status: int, html: str) -> str:
    # Bounded work even for multi-megabyte pages. A plain word "captcha" in a
    # story or a site's shared JavaScript is not evidence of an interstitial.
    sample = html[:200_000]
    # Sitemap URLs can legitimately contain "captcha" or "challenge". Do not
    # feed their XML to BeautifulSoup's HTML parser; discovery validates XML.
    if re.match(r"[\s\ufeff]*(?:(?:<\?xml[^>]*>|<!--.*?-->)\s*)*<(?:[\w.-]+:)?(?:urlset|sitemapindex)(?:\s|>)", sample, re.DOTALL):
        if 200 <= status <= 299:
            return "valid_content_page"
        if status in {404, 410}:
            return "not_found"
        if status == 429:
            return "rate_limited"
        return "server_error" if status >= 500 else "http_error"
    lowered = sample.casefold()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", lowered, re.DOTALL)
    title = " ".join(title_match.group(1).split()) if title_match else ""
    if any(marker in title for marker in CHALLENGE_MARKERS):
        return "anti_bot_or_challenge"
    if title.strip(" .!:") in {"captcha", "капча", "ddos-guard"}:
        return "anti_bot_or_challenge"
    if any(marker in lowered for marker in (
        "captcha", "challenge", "turnstile", "ddos-guard", *CHALLENGE_MARKERS,
    )):
        page = soup(sample.encode("utf-8"))
        # Visible interstitial forms are stronger evidence than words in scripts.
        if page.select_one(
            "#challenge-form, #cf-challenge-running, #px-captcha, "
            "form[action*='/cdn-cgi/challenge-platform/']"
        ):
            return "anti_bot_or_challenge"
        has_article = any(node.get_text(" ", strip=True)
                          for node in page.select(ARTICLE_BODY_SELECTORS))
        if not has_article:
            for node in page.select("script, style, noscript, template"):
                node.decompose()
            visible = " ".join(page.get_text(" ", strip=True).casefold().split())
            widget = page.select_one(
                ".g-recaptcha, .h-captcha, .cf-turnstile, "
                "iframe[src*='recaptcha'], iframe[src*='hcaptcha']"
            )
            if widget or (len(visible) < 3000 and any(marker in visible for marker in CHALLENGE_MARKERS)):
                return "anti_bot_or_challenge"
    if status in {404, 410}:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "server_error"
    if 300 <= status <= 399:
        return "redirect"
    if 200 <= status <= 299:
        if not html.strip():
            return "empty_response"
        return "valid_content_page"
    return "http_error"


def fetch_robots(
    session: requests.Session,
    source: str,
    url: str,
    data_dir: Path,
) -> RobotsResult:
    """Fetch, persist and apply robots.txt before any document request."""
    robots_url = _robots_url(url)
    output_path = data_dir / "discovery" / source / "robots.txt"
    diagnostic_path = data_dir / "discovery" / source / "robots_status.json"
    try:
        response = session.get(robots_url, timeout=30, allow_redirects=True)
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        allowed = parser.can_fetch(USER_AGENT, url)
        result = RobotsResult(source, robots_url, allowed, response.status_code)
    except requests.RequestException as exc:
        result = RobotsResult(
            source, robots_url, False, None, f"{type(exc).__name__}: {exc}"
        )
    dump_json(
        diagnostic_path,
        {
            "source": result.source,
            "robots_url": result.robots_url,
            "allowed": result.allowed,
            "http_status": result.http_status,
            "error": result.error,
            "checked_at": datetime.now(UTC).isoformat(),
        },
    )
    return result


def _error_document(source: str, url: str, message: str) -> dict:
    return {
        "source": source,
        "url": url,
        "title": None,
        "author": None,
        "published_at": None,
        "category": None,
        "tags": [],
        "games": [],
        "text": "",
        "metadata": {},
        "parse_error": message,
        "http_status": None,
        "response_class": "request_error",
        "raw_size_bytes": 0,
        "text_size_bytes": 0,
        "text_sha256": None,
    }


def _write_document(
    result: dict,
    source: str,
    url: str,
    data_dir: Path,
) -> Path:
    path = data_dir / "parsed" / source / f"{safe_name(url)}.json"
    dump_json(path, result)
    return path


def collect_one(
    session: requests.Session,
    source: str,
    url: str,
    data_dir: Path,
    robots: RobotsResult,
) -> dict:
    if source not in PARSERS:
        raise ValueError(f"Неизвестный source: {source}")
    if not _valid_source_url(source, url):
        raise ValueError(f"URL {url} не соответствует источнику {source}")
    canonical_url = canonicalize_url(url)
    if not robots.allowed:
        reason = robots.error or "Путь запрещён robots.txt для нашего User-Agent."
        result = _error_document(source, canonical_url, reason)
        result["response_class"] = "disallowed_by_robots"
        _write_document(result, source, canonical_url, data_dir)
        return result

    try:
        response = session.get(canonical_url, timeout=30, allow_redirects=True)
    except requests.RequestException as exc:
        result = _error_document(source, canonical_url, f"{type(exc).__name__}: {exc}")
        _write_document(result, source, canonical_url, data_dir)
        return result

    raw = response.content
    raw_path = data_dir / "raw" / source / f"{safe_name(canonical_url)}.html"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    response_class = _response_class(response.status_code, response.text)

    if response_class != "valid_content_page":
        result = _error_document(
            source,
            canonical_url,
            f"HTTP {response.status_code}: {response_class}",
        )
        result.update({
            "http_status": response.status_code,
            "response_class": response_class,
            "raw_size_bytes": len(raw),
            "raw_file": str(raw_path),
            "final_url": canonicalize_url(response.url),
            "redirect_chain": [item.url for item in response.history],
        })
        _write_document(result, source, canonical_url, data_dir)
        return result

    try:
        result = PARSERS[source](raw, canonical_url).to_dict()
    except Exception as exc:  # Keep evidence and a machine-readable failure.
        result = _error_document(source, canonical_url, f"{type(exc).__name__}: {exc}")

    text = str(result.get("text", ""))
    result.update({
        "http_status": response.status_code,
        "response_class": response_class,
        "raw_size_bytes": len(raw),
        "text_size_bytes": len(text.encode("utf-8")),
        "text_sha256": sha256_text(text) if text else None,
        "raw_file": str(raw_path),
        "final_url": canonicalize_url(response.url),
        "redirect_chain": [item.url for item in response.history],
        "content_type": response.headers.get("content-type"),
        "fetched_at": datetime.now(UTC).isoformat(),
    })
    _write_document(result, source, canonical_url, data_dir)
    return result


def _read_urls(urls_path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line_no, raw_line in enumerate(urls_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            source, url = line.split("\t", 1)
        except ValueError as exc:
            raise ValueError(f"{urls_path}:{line_no}: ожидается source<TAB>url") from exc
        source = source.strip().lower()
        if source not in PARSERS:
            raise ValueError(f"{urls_path}:{line_no}: неизвестный source {source}")
        entries.append((source, url.strip()))
    return entries


def collect_file(
    urls_path: Path,
    data_dir: Path = Path("data"),
    delay_seconds: float = 2.0,
) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
    entries = _read_urls(urls_path)
    robots_by_source: dict[str, RobotsResult] = {}

    for index, (source, url) in enumerate(entries, 1):
        if source not in robots_by_source:
            robots_by_source[source] = fetch_robots(session, source, url, data_dir)
        result = collect_one(session, source, url, data_dir, robots_by_source[source])
        state = "OK" if result.get("text") and not result.get("parse_error") else result["response_class"]
        print(f"[{index}/{len(entries)}] {source:11s} HTTP={result.get('http_status')} {state:24s} {url}")
        if index != len(entries):
            time.sleep(delay_seconds)


def parse_saved_html(
    source: str,
    url: str,
    raw_path: Path,
    data_dir: Path = Path("data"),
) -> dict:
    """Parse a locally saved verified fixture without making a network request."""
    raw = raw_path.read_bytes()
    try:
        result = PARSERS[source](raw, url).to_dict()
    except Exception as exc:
        result = _error_document(source, url, f"{type(exc).__name__}: {exc}")
    text = str(result.get("text", ""))
    result.update({
        "http_status": 200,
        "response_class": "valid_content_page",
        "raw_size_bytes": len(raw),
        "text_size_bytes": len(text.encode("utf-8")),
        "text_sha256": sha256_text(text) if text else None,
        "raw_file": str(raw_path),
        "fixture": True,
    })
    _write_document(result, source, url, data_dir)
    return result


def parse_fixture_manifest(
    manifest_path: Path,
    data_dir: Path = Path("data"),
) -> None:
    for line_no, raw_line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            source, url, raw_file = line.split("\t", 2)
        except ValueError as exc:
            raise ValueError(
                f"{manifest_path}:{line_no}: ожидается source<TAB>url<TAB>raw_file"
            ) from exc
        result = parse_saved_html(source, url, Path(raw_file), data_dir)
        print(f"[fixture] {source:11s} words={len(result.get('text', '').split()):5d} {url}")


def reparse_saved_documents(
    parsed_root: Path = Path("data/parsed"),
) -> None:
    """Re-run parsers over saved raw files without touching the network.

    It is intended for parser development: collection diagnostics (HTTP status,
    final URL and fetch time) are retained, while text and parse metadata are
    regenerated from the raw HTML that was actually received.
    """
    for document_path in sorted(parsed_root.rglob("*.json")):
        try:
            previous = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] Не удалось прочитать {document_path}: {exc}")
            continue

        source = previous.get("source")
        url = previous.get("url")
        raw_file = previous.get("raw_file")
        if source not in PARSERS or not isinstance(url, str) or not isinstance(raw_file, str):
            print(f"[SKIP] Нет source, URL или raw_file: {document_path}")
            continue
        raw_path = Path(raw_file)
        if not raw_path.is_file():
            print(f"[SKIP] Raw HTML не найден: {raw_path}")
            continue

        raw = raw_path.read_bytes()
        try:
            result = PARSERS[source](raw, url).to_dict()
        except Exception as exc:
            result = _error_document(source, url, f"{type(exc).__name__}: {exc}")

        text = str(result.get("text", ""))
        result.update({
            "http_status": previous.get("http_status", 200),
            "response_class": "valid_content_page",
            "raw_size_bytes": len(raw),
            "text_size_bytes": len(text.encode("utf-8")),
            "text_sha256": sha256_text(text) if text else None,
            "raw_file": str(raw_path),
            "reparsed_at": datetime.now(UTC).isoformat(),
        })
        for key in ("final_url", "redirect_chain", "content_type", "fetched_at"):
            if key in previous:
                result[key] = previous[key]
        dump_json(document_path, result)
        state = "OK" if text and not result.get("parse_error") else "PARSE ERROR"
        print(f"[reparse] {source:11s} words={len(text.split()):5d} {state:11s} {url}")
