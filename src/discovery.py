from __future__ import annotations

import csv
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests

from src.downloader import SOURCE_HOSTS, USER_AGENT, fetch_robots, safe_name
from src.utils import canonicalize_url, dump_json


@dataclass(frozen=True)
class SitemapRoot:
    url: str
    filename: str


SITEMAP_ROOTS = {
    "playground": SitemapRoot(
        "https://www.playground.ru/sitemap.xml", "playground_sitemap.xml"
    ),
    "stopgame": SitemapRoot(
        "https://stopgame.ru/sitemap.xml", "stopgame_sitemap.xml"
    ),
    "ixbt_games": SitemapRoot(
        "https://ixbt.games/export/sitemapindex.xml", "ixbt_sitemapindex.xml"
    ),
    "igromania": SitemapRoot(
        "https://www.igromania.ru/sitemap.xml", "igromania_sitemap.xml"
    ),
    # PC Gamer is an English-language specialist gaming outlet.  The sitemap
    # URL is declared in its robots.txt; collection is intentionally added
    # only after the site's article selector is validated on saved fixtures.
    "pcgamer": SitemapRoot(
        "https://www.pcgamer.com/sitemap.xml", "pcgamer_sitemap.xml"
    ),
}


# Discovery may precede a source-specific HTML parser.  Keep its host allow
# list separate from downloader.SOURCE_HOSTS so ``collect`` does not pretend
# that an unvalidated source is ready for corpus collection.
DISCOVERY_HOSTS = {
    **SOURCE_HOSTS,
    "pcgamer": {"pcgamer.com", "www.pcgamer.com"},
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(xml: bytes) -> tuple[str, list[str]]:
    """Return sitemap kind (``sitemapindex`` or ``urlset``) and its locations."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"Некорректный XML sitemap: {exc}") from exc

    kind = _local_name(root.tag)
    if kind not in {"sitemapindex", "urlset"}:
        raise ValueError(f"Неизвестный корневой тег sitemap: {kind}")
    entry_name = "sitemap" if kind == "sitemapindex" else "url"
    locations = []
    for entry in root:
        if _local_name(entry.tag) != entry_name:
            continue
        location = next((
            (node.text or "").strip()
            for node in entry
            if _local_name(node.tag) == "loc" and (node.text or "").strip()
        ), "")
        if location:
            locations.append(location)
    return kind, locations


def sitemap_category(source: str, sitemap_url: str) -> str | None:
    """Classify a child sitemap, returning None for service/non-document maps."""
    path = urlsplit(sitemap_url).path.lower()

    if source == "playground":
        if path.endswith("/sitemap/news/list.xml"):
            return "news"
        if path.endswith("/sitemap/opinion/list.xml"):
            return "review"
        return None

    if source == "stopgame":
        for prefix, category in (
            ("/sitemap/news_", "news"),
            ("/sitemap/blogs_", "blog"),
            ("/sitemap/reviews_", "review"),
            ("/sitemap/articles_", "article"),
            ("/sitemap/compilations_", "article"),
        ):
            if path.startswith(prefix):
                return category
        return None

    if source == "ixbt_games":
        return "publication" if re.search(r"/export/sitemap-\d+\.xml$", path) else None

    if source == "igromania":
        if path.endswith("/sitemap-news.xml"):
            return "news"
        if path.endswith("/sitemap-articles.xml"):
            return "article"
        if path.endswith("/sitemap-reviews.xml"):
            return "review"
        return None

    if source == "pcgamer":
        # PC Gamer is a specialised gaming publication.  Its sitemap layout
        # may be a URL set or a nested index, therefore child maps are kept
        # under one provisional publication category until sampled.
        hostname = urlsplit(sitemap_url).hostname
        return "publication" if hostname in DISCOVERY_HOSTS[source] else None

    raise ValueError(f"Неизвестный source: {source}")


def document_category(source: str, url: str, sitemap_type: str) -> str | None:
    """Map a document URL to the corpus taxonomy used in the URL inventory."""
    if source != "ixbt_games":
        return sitemap_type

    path = urlsplit(url).path.lower()
    if path.startswith("/news/"):
        return "news"
    if path.startswith("/articles/"):
        return "article"
    if path.startswith("/reviews/"):
        return "review"
    return None


def _matches_source(source: str, url: str) -> bool:
    return urlsplit(url).hostname in DISCOVERY_HOSTS[source]


def _root_path(data_dir: Path, source: str) -> Path:
    return data_dir / "discovery" / source / SITEMAP_ROOTS[source].filename


def _leaf_path(data_dir: Path, source: str, sitemap_url: str) -> Path:
    return data_dir / "discovery" / source / "sitemaps" / f"{safe_name(sitemap_url)}.xml"


def _download_sitemap(session: requests.Session, sitemap_url: str) -> bytes:
    response = session.get(sitemap_url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response.content


def _allowed_by_saved_robots(data_dir: Path, source: str, url: str) -> bool:
    robots_path = data_dir / "discovery" / source / "robots.txt"
    if not robots_path.is_file():
        return False
    parser = robotparser.RobotFileParser()
    parser.parse(robots_path.read_text(encoding="utf-8", errors="replace").splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _load_root(
    session: requests.Session,
    data_dir: Path,
    source: str,
    *,
    refresh: bool,
) -> bytes:
    path = _root_path(data_dir, source)
    if path.is_file() and not refresh:
        return path.read_bytes()

    root = SITEMAP_ROOTS[source]
    content = _download_sitemap(session, root.url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def discover_sitemaps(
    data_dir: Path = Path("data"),
    output_dir: Path | None = None,
    sources: list[str] | None = None,
    delay_seconds: float = 0.5,
    refresh_roots: bool = False,
    roots_only: bool = False,
) -> dict:
    """Build a deduplicated inventory of publication URLs listed in sitemaps.

    In ``roots_only`` mode no network requests are made: locally saved root
    sitemap indexes are parsed to prepare an auditable list of child maps.
    Normal mode refreshes robots.txt, reads cached root maps (or downloads
    them), and requests only child sitemaps that can contain publications.
    """
    selected_sources = sources or list(SITEMAP_ROOTS)
    invalid = set(selected_sources) - set(SITEMAP_ROOTS)
    if invalid:
        raise ValueError(f"Неизвестные источники: {', '.join(sorted(invalid))}")

    output_dir = output_dir or data_dir / "discovery" / "inventory"
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
    })

    candidates: list[dict[str, str]] = []
    inventory: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen_sitemaps: set[str] = set()
    seen_urls: set[str] = set()

    for source in selected_sources:
        root = SITEMAP_ROOTS[source]
        try:
            if roots_only:
                root_path = _root_path(data_dir, source)
                root_xml = root_path.read_bytes()
            else:
                robots = fetch_robots(session, source, root.url, data_dir)
                if not robots.allowed:
                    errors.append({
                        "source": source,
                        "url": root.url,
                        "error": robots.error or "Корневой sitemap запрещён robots.txt",
                    })
                    continue
                root_xml = _load_root(
                    session, data_dir, source, refresh=refresh_roots
                )
            root_kind, locations = parse_sitemap(root_xml)
        except (OSError, requests.RequestException, ValueError) as exc:
            errors.append({"source": source, "url": root.url, "error": str(exc)})
            continue

        if root_kind == "urlset":
            locations = [root.url]

        queue: list[tuple[str, str, bytes | None]] = []
        for location in locations:
            category = sitemap_category(source, location)
            if category is None:
                continue
            canonical_sitemap = canonicalize_url(location)
            if not roots_only and not _allowed_by_saved_robots(
                data_dir, source, canonical_sitemap
            ):
                errors.append({
                    "source": source,
                    "url": canonical_sitemap,
                    "error": "Дочерний sitemap запрещён robots.txt",
                })
                continue
            if canonical_sitemap in seen_sitemaps:
                continue
            seen_sitemaps.add(canonical_sitemap)
            candidates.append({
                "source": source,
                "category": category,
                "sitemap_url": canonical_sitemap,
            })
            queue.append((canonical_sitemap, category, None))

        if roots_only:
            print(f"[{source}] maps={len(queue):3d} (без сетевых запросов)")
            continue

        source_start = len(inventory)
        while queue:
            sitemap_url, sitemap_type, cached_xml = queue.pop(0)
            try:
                xml = cached_xml or _download_sitemap(session, sitemap_url)
                local_path = _leaf_path(data_dir, source, sitemap_url)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(xml)
                kind, child_locations = parse_sitemap(xml)
            except (OSError, requests.RequestException, ValueError) as exc:
                errors.append({"source": source, "url": sitemap_url, "error": str(exc)})
                continue

            if kind == "sitemapindex":
                for child_url in child_locations:
                    if not _matches_source(source, child_url):
                        continue
                    canonical_child = canonicalize_url(child_url)
                    if not _allowed_by_saved_robots(data_dir, source, canonical_child):
                        errors.append({
                            "source": source,
                            "url": canonical_child,
                            "error": "Дочерний sitemap запрещён robots.txt",
                        })
                        continue
                    if canonical_child in seen_sitemaps:
                        continue
                    seen_sitemaps.add(canonical_child)
                    candidates.append({
                        "source": source,
                        "category": sitemap_type,
                        "sitemap_url": canonical_child,
                    })
                    queue.append((canonical_child, sitemap_type, None))
            else:
                for document_url in child_locations:
                    if not _matches_source(source, document_url):
                        continue
                    category = document_category(source, document_url, sitemap_type)
                    if category is None:
                        continue
                    canonical_url = canonicalize_url(document_url)
                    if canonical_url in seen_urls:
                        continue
                    seen_urls.add(canonical_url)
                    inventory.append({
                        "source": source,
                        "category": category,
                        "url": canonical_url,
                        "sitemap_url": sitemap_url,
                    })

            if queue:
                time.sleep(delay_seconds)

        print(f"[{source}] maps={sum(row['source'] == source for row in candidates):3d} "
              f"urls={len(inventory) - source_start:7d}")

    candidates.sort(key=lambda row: (row["source"], row["category"], row["sitemap_url"]))
    inventory.sort(key=lambda row: (row["source"], row["category"], row["url"]))
    _write_tsv(
        output_dir / "sitemap_candidates.tsv",
        ["source", "category", "sitemap_url"],
        candidates,
    )
    _write_tsv(
        output_dir / "url_inventory.tsv",
        ["source", "category", "url", "sitemap_url"],
        inventory,
    )

    by_source: dict[str, dict] = {}
    for source in selected_sources:
        source_rows = [row for row in inventory if row["source"] == source]
        source_candidates = [row for row in candidates if row["source"] == source]
        by_source[source] = {
            "selected_sitemaps": len(source_candidates),
            "urls": len(source_rows),
            "categories": dict(sorted(Counter(row["category"] for row in source_rows).items())),
        }
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "roots_only": roots_only,
        "total_selected_sitemaps": len(candidates),
        "total_unique_urls": len(inventory),
        "sources": by_source,
        "errors": errors,
    }
    dump_json(output_dir / "summary.json", summary)
    return summary
