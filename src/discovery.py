from __future__ import annotations

import csv
import re
import sqlite3
import tempfile
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
    # Eurogamer is an English-language specialist gaming outlet.  Its sitemap
    # is declared in robots.txt; as with PC Gamer, this entry is limited to
    # URL discovery until the article-body selector is validated on fixtures.
    "eurogamer": SitemapRoot(
        "https://www.eurogamer.net/sitemap.xml", "eurogamer_sitemap.xml"
    ),
    "gamemag": SitemapRoot(
        "https://gamemag.ru/sitemap.xml", "gamemag_sitemap.xml"
    ),
    "gamingonlinux": SitemapRoot(
        "https://www.gamingonlinux.com/sitemap.xml", "gamingonlinux_sitemap.xml"
    ),
    "siliconera": SitemapRoot(
        "https://www.siliconera.com/sitemap_index.xml", "siliconera_sitemap_index.xml"
    ),
}


# Discovery may precede a source-specific HTML parser.  Keep its host allow
# list separate from downloader.SOURCE_HOSTS so ``collect`` does not pretend
# that an unvalidated source is ready for corpus collection.
DISCOVERY_HOSTS = {
    **SOURCE_HOSTS,
    "pcgamer": {"pcgamer.com", "www.pcgamer.com"},
    "eurogamer": {"eurogamer.net", "www.eurogamer.net"},
    "gamemag": {"gamemag.ru", "www.gamemag.ru"},
    "gamingonlinux": {"gamingonlinux.com", "www.gamingonlinux.com"},
    "siliconera": {"siliconera.com", "www.siliconera.com"},
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

    if source == "eurogamer":
        # Eurogamer's main sitemap covers gaming editorial material.  The
        # exact section labels are resolved later from validated HTML samples.
        hostname = urlsplit(sitemap_url).hostname
        return "publication" if hostname in DISCOVERY_HOSTS[source] else None

    if source in {"gamemag", "gamingonlinux"}:
        # The sites' own sitemaps are publication maps or nested indexes.  URL
        # classification below removes GameMAG's game, profile and taxonomy pages.
        hostname = urlsplit(sitemap_url).hostname
        return "publication" if hostname in DISCOVERY_HOSTS[source] else None

    if source == "siliconera":
        # Siliconera uses a Yoast index; only post maps contain publications.
        return (
            "publication"
            if re.search(r"/(?:post|article)-sitemap\d*\.xml$", path)
            else None
        )

    raise ValueError(f"Неизвестный source: {source}")


def document_category(source: str, url: str, sitemap_type: str) -> str | None:
    """Map a document URL to the corpus taxonomy used in the URL inventory."""
    path = urlsplit(url).path.lower()

    if source == "gamemag":
        for prefix, category in (
            ("/news/", "news"),
            ("/reviews/", "review"),
            ("/articles/", "article"),
            ("/specials/", "article"),
        ):
            if path.startswith(prefix):
                return category
        return None

    if source != "ixbt_games":
        return sitemap_type

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


def merge_inventories(input_dirs: list[Path], output_dir: Path) -> dict:
    """Merge URL inventories using a temporary on-disk database.

    A corpus-scale inventory can contain over a million rows, so deduplication
    is done by SQLite rather than by loading every URL into a Python set.
    """
    if not input_dirs:
        raise ValueError("Нужен хотя бы один каталог инвентаря")

    output_dir.mkdir(parents=True, exist_ok=True)
    rows_seen = 0
    inserted = 0

    with tempfile.TemporaryDirectory(prefix="mai-ir-inventory-") as temporary_dir:
        database_path = Path(temporary_dir) / "inventory.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript("""
                PRAGMA journal_mode = OFF;
                PRAGMA synchronous = OFF;
                CREATE TABLE documents (
                    url TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    sitemap_url TEXT NOT NULL
                );
            """)

            for directory in input_dirs:
                inventory_path = directory / "url_inventory.tsv"
                if not inventory_path.is_file():
                    raise FileNotFoundError(f"Не найден URL-инвентарь: {inventory_path}")
                with inventory_path.open(encoding="utf-8", newline="") as stream:
                    reader = csv.DictReader(stream, delimiter="\t")
                    required = {"source", "category", "url", "sitemap_url"}
                    if not reader.fieldnames or not required.issubset(reader.fieldnames):
                        raise ValueError(f"Некорректный TSV-инвентарь: {inventory_path}")
                    for row in reader:
                        rows_seen += 1
                        source = row["source"].strip()
                        category = row["category"].strip()
                        url = canonicalize_url(row["url"])
                        sitemap_url = canonicalize_url(row["sitemap_url"])
                        if not all((source, category, url, sitemap_url)):
                            raise ValueError(f"Пустое обязательное поле: {inventory_path}")
                        cursor = connection.execute(
                            "INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?)",
                            (url, source, category, sitemap_url),
                        )
                        inserted += cursor.rowcount
            connection.commit()

            sources: dict[str, dict] = {}
            output_path = output_dir / "url_inventory.tsv"
            with output_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["source", "category", "url", "sitemap_url"],
                    delimiter="\t",
                )
                writer.writeheader()
                for source, category, url, sitemap_url in connection.execute(
                    "SELECT source, category, url, sitemap_url "
                    "FROM documents ORDER BY source, category, url"
                ):
                    source_summary = sources.setdefault(
                        source, {"urls": 0, "categories": Counter()}
                    )
                    source_summary["urls"] += 1
                    source_summary["categories"][category] += 1
                    writer.writerow({
                        "source": source,
                        "category": category,
                        "url": url,
                        "sitemap_url": sitemap_url,
                    })
        finally:
            connection.close()

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_inventories": [str(directory) for directory in input_dirs],
        "input_rows": rows_seen,
        "duplicate_urls_removed": rows_seen - inserted,
        "total_unique_urls": inserted,
        "sources": {
            source: {
                "urls": values["urls"],
                "categories": dict(sorted(values["categories"].items())),
            }
            for source, values in sorted(sources.items())
        },
        "errors": [],
    }
    dump_json(output_dir / "summary.json", summary)
    return summary


def discover_sitemaps(
    data_dir: Path = Path("data"),
    output_dir: Path | None = None,
    sources: list[str] | None = None,
    delay_seconds: float = 0.5,
    refresh_roots: bool = False,
    roots_only: bool = False,
    cache_only: bool = False,
) -> dict:
    """Build a deduplicated inventory of publication URLs listed in sitemaps.

    In ``roots_only`` mode no network requests are made: locally saved root
    sitemap indexes are parsed to prepare an auditable list of child maps.
    In ``cache_only`` mode the saved robots.txt, root map and child maps are
    used to rebuild the full URL inventory without network access.  Normal
    mode refreshes robots.txt, reads cached root maps (or downloads them),
    and requests only child sitemaps that can contain publications.
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
            if roots_only or cache_only:
                root_path = _root_path(data_dir, source)
                root_xml = root_path.read_bytes()
                if cache_only and not _allowed_by_saved_robots(
                    data_dir, source, root.url
                ):
                    errors.append({
                        "source": source,
                        "url": root.url,
                        "error": "Корневой sitemap запрещён сохранённым robots.txt",
                    })
                    continue
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
            cached_xml = None
            if cache_only:
                try:
                    cached_xml = (
                        root_xml
                        if canonical_sitemap == canonicalize_url(root.url)
                        else _leaf_path(data_dir, source, canonical_sitemap).read_bytes()
                    )
                except OSError as exc:
                    errors.append({
                        "source": source,
                        "url": canonical_sitemap,
                        "error": f"Сохранённый sitemap не найден: {exc}",
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
            queue.append((canonical_sitemap, category, cached_xml))

        if roots_only:
            print(f"[{source}] maps={len(queue):3d} (без сетевых запросов)")
            continue

        source_start = len(inventory)
        while queue:
            sitemap_url, sitemap_type, cached_xml = queue.pop(0)
            try:
                if cached_xml is not None:
                    xml = cached_xml
                elif cache_only:
                    raise OSError("Сохранённый sitemap не найден")
                else:
                    xml = _download_sitemap(session, sitemap_url)
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
                    cached_child_xml = None
                    if cache_only:
                        try:
                            cached_child_xml = _leaf_path(
                                data_dir, source, canonical_child
                            ).read_bytes()
                        except OSError as exc:
                            errors.append({
                                "source": source,
                                "url": canonical_child,
                                "error": f"Сохранённый sitemap не найден: {exc}",
                            })
                            continue
                    seen_sitemaps.add(canonical_child)
                    candidates.append({
                        "source": source,
                        "category": sitemap_type,
                        "sitemap_url": canonical_child,
                    })
                    queue.append((canonical_child, sitemap_type, cached_child_xml))
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

            if queue and not cache_only:
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
        "cache_only": cache_only,
        "total_selected_sitemaps": len(candidates),
        "total_unique_urls": len(inventory),
        "sources": by_source,
        "errors": errors,
    }
    dump_json(output_dir / "summary.json", summary)
    return summary
