from __future__ import annotations

import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock

import requests
from bs4 import XMLParsedAsHTMLWarning

from src.discovery import _leaf_path, discover_sitemaps, parse_sitemap, parse_sitemap_entries
from src.downloader import _response_class
from src.sitemap_cache import SitemapCache
from tests.test_crawler import FakeResponse


ROOT = "https://stopgame.ru/sitemap.xml"
LEAF = "https://stopgame.ru/sitemap/news_1.xml"
XML = b"<urlset><url><loc>https://stopgame.ru/newsdata/1/example</loc></url></urlset>"


class SitemapCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = 1000.0
        self.cache = SitemapCache(self.root, ttl_seconds=3600, recheck_seconds=86400, now=lambda: self.clock)

    def response(self, status=200, xml=XML, **headers):
        return FakeResponse(LEAF, status, xml, headers)

    def test_unchanged_hint_is_cached_changed_hint_is_downloaded(self):
        request = Mock(return_value=self.response(ETag='"v1"'))
        self.assertEqual(self.cache.fetch("stopgame", LEAF, "2026-10-21", request), XML)
        self.clock += 10
        self.assertEqual(self.cache.fetch("stopgame", LEAF, "2026-10-21", request), XML)
        self.assertEqual(request.call_count, 1)
        self.cache.fetch("stopgame", LEAF, "2026-10-22", request)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0], {"If-None-Match": '"v1"'})

    def test_no_lastmod_uses_short_ttl_and_304_reuses_body(self):
        request = Mock(return_value=self.response(ETag='"v1"', **{"Last-Modified": "yesterday"}))
        self.cache.fetch("stopgame", LEAF, None, request)
        self.clock += 3599
        self.cache.fetch("stopgame", LEAF, None, request)
        self.assertEqual(request.call_count, 1)
        self.clock += 1
        request.return_value = self.response(304, b"")
        self.assertEqual(self.cache.fetch("stopgame", LEAF, None, request), XML)
        self.assertEqual(request.call_args.args[0], {"If-None-Match": '"v1"', "If-Modified-Since": "yesterday"})
        self.assertEqual(self.cache.stats["not_modified"], 1)

    def test_root_is_always_checked_over_http(self):
        request = Mock(return_value=self.response(ETag='"root"'))
        self.cache.fetch("stopgame", ROOT, None, request)
        request.return_value = self.response(304, b"")
        self.cache.fetch("stopgame", ROOT, None, request)
        self.assertEqual(request.call_count, 2)

    def test_lastmod_hint_does_not_disable_periodic_http_recheck(self):
        request = Mock(return_value=self.response())
        self.cache.fetch("stopgame", LEAF, "2026-10-21", request)
        self.clock += 86400 - 1
        self.cache.fetch("stopgame", LEAF, "2026-10-21", request)
        self.assertEqual(request.call_count, 1)
        self.clock += 1
        self.cache.fetch("stopgame", LEAF, "2026-10-21", request)
        self.assertEqual(request.call_count, 2)

    def test_fresh_legacy_xml_adopted_but_ttl_is_not_extended(self):
        path = _leaf_path(self.root, "stopgame", LEAF)
        path.parent.mkdir(parents=True)
        path.write_bytes(XML)
        os.utime(path, (900, 900))
        request = Mock(return_value=self.response())
        self.assertEqual(self.cache.fetch("stopgame", LEAF, "2026-10-21", request), XML)
        request.assert_not_called()
        self.clock = 4500
        self.cache.fetch("stopgame", LEAF, "2026-10-21", request)
        request.assert_called_once_with({})

    def test_corrupt_or_modified_cache_does_not_send_old_validators(self):
        request = Mock(return_value=self.response(ETag='"old"'))
        self.cache.fetch("stopgame", LEAF, None, request)
        path = _leaf_path(self.root, "stopgame", LEAF)
        path.write_bytes(b"corrupt")
        self.cache.fetch("stopgame", LEAF, None, request)
        self.assertEqual(request.call_args.args[0], {})

    def test_304_without_cache_is_retried_unconditionally(self):
        request = Mock(side_effect=[self.response(304, b""), self.response()])
        self.assertEqual(self.cache.fetch("stopgame", LEAF, None, request), XML)
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertEqual(call.args[0], {})

    def test_repeated_304_without_cache_fails(self):
        with self.assertRaises(requests.RequestException):
            self.cache.fetch("stopgame", LEAF, None, Mock(return_value=self.response(304, b"")))

    def test_bad_xml_or_http_error_never_replaces_cache(self):
        request = Mock(return_value=self.response())
        self.cache.fetch("stopgame", LEAF, None, request)
        self.cache.force = True
        path = _leaf_path(self.root, "stopgame", LEAF)
        before = path.read_bytes()
        request.return_value = self.response(xml=b"<html>CAPTCHA</html>")
        with self.assertRaises(ValueError):
            self.cache.fetch("stopgame", LEAF, None, request)
        self.assertEqual(path.read_bytes(), before)
        request.return_value = self.response(503)
        with self.assertRaises(requests.HTTPError):
            self.cache.fetch("stopgame", LEAF, None, request)
        self.assertEqual(path.read_bytes(), before)

    def test_force_and_zero_ttl_bypass_fresh_cache(self):
        request = Mock(return_value=self.response())
        self.cache.fetch("stopgame", LEAF, None, request)
        self.cache.force = True
        self.cache.fetch("stopgame", LEAF, None, request)
        self.cache.force = False
        self.cache.ttl_seconds = 0
        self.cache.fetch("stopgame", LEAF, None, request)
        self.assertEqual(request.call_count, 3)

    def test_sitemap_xml_with_challenge_words_does_not_use_html_parser(self):
        xml = "<?xml version='1.0'?><urlset><url><loc>https://example.test/captcha-challenge</loc></url></urlset>"
        with warnings.catch_warnings():
            warnings.simplefilter("error", XMLParsedAsHTMLWarning)
            self.assertEqual(_response_class(200, xml), "valid_content_page")
            self.assertEqual(_response_class(200, "\ufeff<!-- generated sitemap -->\n" + xml), "valid_content_page")
        self.assertEqual(_response_class(200, "<title>Just a moment</title>"), "anti_bot_or_challenge")

    def test_index_lastmod_is_propagated_through_nested_maps(self):
        root_url = "https://gamemag.ru/sitemap.xml"
        nested_url = "https://gamemag.ru/sitemap/news.xml"
        leaf_url = "https://gamemag.ru/sitemap/news-1.xml"
        payloads = {
            root_url: f"<sitemapindex><sitemap><loc>{nested_url}</loc><lastmod>2026-10-20</lastmod></sitemap></sitemapindex>".encode(),
            nested_url: f"<sitemapindex><sitemap><loc>{leaf_url}</loc><lastmod>2026-10-21</lastmod></sitemap></sitemapindex>".encode(),
            leaf_url: b"<urlset><url><loc>https://gamemag.ru/news/1/example</loc><lastmod>2026-10-22</lastmod></url></urlset>",
        }
        calls = []
        def fetch(source, url, lastmod):
            calls.append((url, lastmod))
            return payloads[url]
        result = discover_sitemaps(data_dir=self.root, output_dir=self.root / "output", sources=["gamemag"], fetcher=fetch)
        self.assertEqual(result["total_unique_urls"], 1)
        self.assertEqual(calls, [(root_url, None), (nested_url, "2026-10-20"), (leaf_url, "2026-10-21")])
        self.assertEqual(parse_sitemap(payloads[nested_url]), ("sitemapindex", [leaf_url]))
        self.assertEqual(parse_sitemap_entries(payloads[nested_url]), ("sitemapindex", [(leaf_url, "2026-10-21")]))


if __name__ == "__main__":
    unittest.main()
