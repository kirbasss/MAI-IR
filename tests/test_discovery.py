from __future__ import annotations

import unittest

from src.discovery import document_category, parse_sitemap, sitemap_category


class SitemapDiscoveryTests(unittest.TestCase):
    def test_parse_sitemap_index(self) -> None:
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://stopgame.ru/sitemap/news_1.xml</loc></sitemap>
          <sitemap><loc>https://stopgame.ru/sitemap/games_1.xml</loc></sitemap>
        </sitemapindex>'''
        kind, locations = parse_sitemap(xml)
        self.assertEqual(kind, "sitemapindex")
        self.assertEqual(locations, [
            "https://stopgame.ru/sitemap/news_1.xml",
            "https://stopgame.ru/sitemap/games_1.xml",
        ])

    def test_parse_urlset_ignores_image_locations(self) -> None:
        xml = b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
          <url>
            <loc>https://ixbt.games/news/2026/01/01/example.html</loc>
            <image:image><image:loc>https://ixbt.games/images/example.jpg</image:loc></image:image>
          </url>
        </urlset>'''
        kind, locations = parse_sitemap(xml)
        self.assertEqual(kind, "urlset")
        self.assertEqual(locations, ["https://ixbt.games/news/2026/01/01/example.html"])

    def test_source_specific_sitemap_filters(self) -> None:
        self.assertEqual(
            sitemap_category("playground", "https://www.playground.ru/sitemap/news/list.xml"),
            "news",
        )
        self.assertIsNone(
            sitemap_category("playground", "https://www.playground.ru/sitemap/news/category.xml")
        )
        self.assertEqual(
            sitemap_category("stopgame", "https://stopgame.ru/sitemap/blogs_4.xml"),
            "blog",
        )
        self.assertIsNone(
            sitemap_category("stopgame", "https://stopgame.ru/sitemap/games_4.xml")
        )
        self.assertEqual(
            sitemap_category("ixbt_games", "https://ixbt.games/export/sitemap-17.xml"),
            "publication",
        )
        self.assertIsNone(
            sitemap_category("ixbt_games", "https://ixbt.games/export/sitemap-tags.xml")
        )
        self.assertEqual(
            sitemap_category("igromania", "https://www.igromania.ru/sitemap-news.xml?p=2"),
            "news",
        )

    def test_ixbt_document_category_is_read_from_url(self) -> None:
        self.assertEqual(
            document_category("ixbt_games", "https://ixbt.games/reviews/2026/01/01/example.html", "publication"),
            "review",
        )
        self.assertIsNone(
            document_category("ixbt_games", "https://ixbt.games/tags/rpg", "publication")
        )


if __name__ == "__main__":
    unittest.main()
