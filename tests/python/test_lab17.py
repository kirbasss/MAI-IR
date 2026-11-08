"""Small unit tests for URL and metadata rules used by the crawler."""
import unittest
import json
from http.server import HTTPServer
from pathlib import Path
import struct
import tempfile
import threading
from urllib.parse import urlencode
from urllib.request import urlopen
from unittest.mock import patch

from build_search_filters import build, publication_year
from serve_search import NativeSearch, filter_options, handler, page

from src.url_policy import is_listing_url
from src.utils import canonicalize_source_url, canonicalize_url, find_json_ld, get_json_ld_objects, soup


class UrlPolicyUnitTests(unittest.TestCase):
    def test_tracking_removal_preserves_meaningful_query_and_is_idempotent(self):
        raw = "HTTPS://WWW.PCGAMER.COM/games/rpg/story/?utm_source=feed&page=2&gclid=x&lang=ru#comments"
        expected = "https://www.pcgamer.com/games/rpg/story/?page=2&lang=ru"
        self.assertEqual(canonicalize_url(raw), expected)
        self.assertEqual(canonicalize_url(expected), expected)

    def test_stopgame_sitemap_alias_rewrites_only_numeric_news_ids(self):
        self.assertEqual(
            canonicalize_source_url("stopgame", "https://stopgame.ru/news/1234/story?utm_campaign=x&page=2"),
            "https://stopgame.ru/newsdata/1234/story?page=2",
        )
        self.assertEqual(
            canonicalize_source_url("stopgame", "https://stopgame.ru/news/latest"),
            "https://stopgame.ru/news/latest",
        )
        self.assertEqual(
            canonicalize_source_url("gamemag", "https://gamemag.ru/news/1234/story"),
            "https://gamemag.ru/news/1234/story",
        )

    def test_listing_policy_accepts_deep_article_but_rejects_category_pages(self):
        cases = (
            ("pcgamer", "https://www.pcgamer.com/games/rpg/dragon-age/", True),
            ("pcgamer", "https://www.pcgamer.com/games/rpg/dragon-age/page/3/", True),
            ("pcgamer", "https://www.pcgamer.com/games/rpg/dragon-age/new-trailer/", False),
            ("pcgamer", "https://www.pcgamer.com/news/dragon-age/page/3/", False),
            ("eurogamer", "https://www.eurogamer.net/games/doom", True),
            ("eurogamer", "https://www.eurogamer.net/games/doom/review", False),
            ("stopgame", "https://stopgame.ru/games/compilation", True),
            ("stopgame", "https://stopgame.ru/games/compilation/story", True),
            ("stopgame", "https://stopgame.ru/newsdata/123/story", False),
        )
        for source, url, expected in cases:
            with self.subTest(source=source, url=url):
                self.assertIs(is_listing_url(source, url), expected)


class JsonLdUnitTests(unittest.TestCase):
    def test_nested_graph_finds_article_among_unrelated_metadata(self):
        page = soup(b'''<script type="application/ld+json">{
          "@type": "WebPage", "@graph": [
            {"@type": "BreadcrumbList"},
            {"@type": ["NewsArticle", "Thing"], "headline": "Doom review"}
          ]}</script>''')
        article = find_json_ld(page, "NewsArticle")
        self.assertIsNotNone(article)
        self.assertEqual(article["headline"], "Doom review")

    def test_malformed_and_nonobject_scripts_do_not_hide_valid_metadata(self):
        page = soup(b'''<script type="application/ld+json">{bad</script>
          <script type="application/ld+json">["noise", 17, {"@type":"NewsArticle","headline":"Valid"}]</script>''')
        objects = get_json_ld_objects(page)
        self.assertEqual(len(objects), 1)
        self.assertEqual(find_json_ld(page, "NewsArticle")["headline"], "Valid")


class SearchFilterTests(unittest.TestCase):
    def test_http_form_passes_combined_filters_to_backend(self):
        class Backend:
            filter_choices = {"sources": ["pcgamer"], "types": [("review", "Обзоры")], "years": [2024]}
            def search(self, query, space, ranking, *, source, kind, year):
                assert (query, space, ranking, source, kind, year) == (
                    "doom", "surface", "tfidf", "pcgamer", "review", "2024")
                return {"total": 2, "elapsed_ms": 1, "ranking": "tfidf", "results": []}
        with HTTPServer(("127.0.0.1", 0), handler(Backend())) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                query = urlencode({"q": "doom", "ranking": "tfidf", "source": "pcgamer",
                                   "kind": "review", "year": "2024"})
                with urlopen(f"http://127.0.0.1:{server.server_port}/?{query}") as response:
                    html = response.read().decode("utf-8")
                    self.assertIn("Найдено: <b>2</b>", html)
                    self.assertIn('value="2024" selected', html)
            finally:
                server.shutdown()
                worker.join()

    def test_publication_year_uses_article_date_and_keeps_unknown_distinct(self):
        self.assertEqual(publication_year("2021-04-12T10:00:00+03:00"), 2021)
        self.assertEqual(publication_year(None), 0)
        self.assertEqual(publication_year("yesterday"), 0)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot"
            snapshot.mkdir()
            (snapshot / "manifest.json").write_text(json.dumps({"format": "MAIIR3-v1", "documents": 2}))
            rows = [
                {"document_id": 1, "url": "a", "source": "gamemag", "publication_type": "news", "content_sha256": "same", "fetched_at": 1791304338},
                {"document_id": 2, "url": "b", "source": "pcgamer", "publication_type": "review", "fetched_at": 1791304338},
            ]
            (snapshot / "documents.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            catalog = Path(directory) / "filters.bin"
            database = [{"_id": "a", "content_sha256": "same", "parsed": {"published_at": "2021-04-12"}},
                        {"_id": "b", "content_sha256": "changed", "parsed": {"published_at": "2019-01-01"}}]
            summary = build(snapshot, catalog, database)
            self.assertEqual(summary["publication_years"], {2021: 1})
            self.assertEqual(summary["without_publication_year"], 1)
            self.assertEqual(list(struct.iter_unpack("<HBB", catalog.read_bytes()[16:])), [(2021, 1, 1), (0, 3, 6)])
            choices = filter_options(catalog)
            self.assertEqual(choices["years"], [2021])
            backend = NativeSearch(Path("native"), Path("index"), None, filters=catalog)
            with patch("serve_search.subprocess.run") as run:
                run.return_value.stdout = b'{"total":0,"results":[]}'
                run.return_value.returncode = 0
                backend.search("doom", "surface", source="pcgamer", kind="review")
                self.assertIn("--filter-source", run.call_args.args[0])
                self.assertIn("--filter-type", run.call_args.args[0])
                backend.search("doom", "surface")
                self.assertNotIn("--filters", run.call_args.args[0])
                with self.assertRaises(ValueError):
                    backend.search("doom", "surface", year="2019")
            html = page("doom", choices=choices, source="pcgamer", kind="review", year="2021")
            self.assertIn('value="pcgamer" selected', html)
            self.assertIn('value="review" selected', html)
            self.assertIn('value="2021" selected', html)


if __name__ == "__main__":
    unittest.main()
