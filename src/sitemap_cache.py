"""Bounded sitemap cache: parent lastmod, HTTP validators, and periodic rechecks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

import requests

from src.discovery import SITEMAP_ROOTS, _leaf_path, _root_path, parse_sitemap


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sitemap-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SitemapCache:
    def __init__(self, data_dir: Path, *, ttl_seconds: float, recheck_seconds: float, now):
        self.data_dir = data_dir
        self.ttl_seconds = ttl_seconds
        self.recheck_seconds = recheck_seconds
        self.now = now
        self.force = False
        self.reset_stats()

    def reset_stats(self) -> None:
        self.stats = {"http_requests": 0, "cache_hits": 0, "not_modified": 0, "adopted_legacy": 0}

    def _read(self, path: Path, metadata: Path, url: str):
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        digest = hashlib.sha256(raw).hexdigest()
        if metadata.is_file():
            try:
                state = json.loads(metadata.read_text(encoding="utf-8"))
                checked = state["checked_at"]
                if (state.get("url") != url or state.get("sha256") != digest or
                        state.get("version") != 1 or isinstance(checked, bool) or
                        not isinstance(checked, (int, float)) or not math.isfinite(checked)):
                    return None
                return raw, state
            except (OSError, ValueError, KeyError, TypeError):
                return None
        # Reuse recently downloaded legacy XML without another complete crawl.
        # Its parent hint/HTTP validators are unknown, so only the short TTL
        # applies until an actual HTTP revalidation has established them.
        try:
            parse_sitemap(raw)
        except ValueError:
            return None
        state = {"version": 1, "url": url, "sha256": digest, "checked_at": path.stat().st_mtime,
                 "lastmod": None, "unknown_hint": True, "etag": None, "last_modified": None}
        self.stats["adopted_legacy"] += 1
        return raw, state

    def fetch(self, source: str, url: str, lastmod: str | None, request) -> bytes:
        is_root = url == SITEMAP_ROOTS[source].url
        path = _root_path(self.data_dir, source) if is_root else _leaf_path(self.data_dir, source, url)
        metadata = path.with_name(path.name + ".http.json")
        cached = self._read(path, metadata, url)
        if cached is not None and not is_root and not self.force and self.ttl_seconds > 0:
            raw, state = cached
            age = self.now() - state["checked_at"]
            unknown = state.get("unknown_hint", False)
            same_hint = lastmod == state.get("lastmod")
            lifetime = self.recheck_seconds if lastmod and same_hint and not unknown else self.ttl_seconds
            if 0 <= age < lifetime and (unknown or same_hint):
                self.stats["cache_hits"] += 1
                if not metadata.is_file():
                    _atomic_bytes(metadata, json.dumps(state).encode("utf-8"))
                return raw
        headers = {}
        if cached is not None:
            state = cached[1]
            if state.get("etag"):
                headers["If-None-Match"] = state["etag"]
            if state.get("last_modified"):
                headers["If-Modified-Since"] = state["last_modified"]
        self.stats["http_requests"] += 1
        response = request(headers)
        if response.status_code == 304 and cached is None:
            # Never accept a 304 without a complete, hash-consistent local body.
            self.stats["http_requests"] += 1
            response = request({})
        if response.status_code == 304:
            if cached is None:
                raise requests.RequestException(f"304 без кеша sitemap: {url}")
            self.stats["not_modified"] += 1
            raw, previous = cached
        else:
            response.raise_for_status()
            if response.status_code != 200:
                raise requests.RequestException(f"HTTP {response.status_code} вместо sitemap: {url}")
            raw, previous = response.content, {}
            parse_sitemap(raw)  # A captcha/invalid XML must never replace a usable cache.
            _atomic_bytes(path, raw)
        state = {
            "version": 1, "url": url, "sha256": hashlib.sha256(raw).hexdigest(),
            "checked_at": self.now(), "lastmod": lastmod, "unknown_hint": False,
            "etag": response.headers.get("ETag", previous.get("etag")),
            "last_modified": response.headers.get("Last-Modified", previous.get("last_modified")),
        }
        _atomic_bytes(metadata, json.dumps(state).encode("utf-8"))
        return raw
