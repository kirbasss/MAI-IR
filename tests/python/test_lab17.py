"""Small unit tests for URL and metadata rules used by the crawler."""
import unittest

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


if __name__ == "__main__":
    unittest.main()
