from __future__ import annotations

import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest

from src.discovery import (
    discover_sitemaps,
    document_category,
    merge_inventories,
    parse_sitemap,
    sitemap_category,
)


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
        self.assertEqual(
            sitemap_category("pcgamer", "https://www.pcgamer.com/sitemap.xml"),
            "publication",
        )
        self.assertIsNone(
            sitemap_category("pcgamer", "https://example.org/sitemap.xml")
        )
        self.assertEqual(
            sitemap_category("eurogamer", "https://www.eurogamer.net/sitemap.xml"),
            "publication",
        )
        self.assertEqual(
            sitemap_category("gamingonlinux", "https://www.gamingonlinux.com/sitemap.xml"),
            "publication",
        )
        self.assertEqual(
            sitemap_category("siliconera", "https://www.siliconera.com/post-sitemap3.xml"),
            "publication",
        )
        self.assertIsNone(
            sitemap_category("siliconera", "https://www.siliconera.com/category-sitemap.xml")
        )

    def test_ixbt_document_category_is_read_from_url(self) -> None:
        self.assertEqual(
            document_category("ixbt_games", "https://ixbt.games/reviews/2026/01/01/example.html", "publication"),
            "review",
        )
        self.assertIsNone(
            document_category("ixbt_games", "https://ixbt.games/tags/rpg", "publication")
        )

    def test_gamemag_document_category_is_read_from_url(self) -> None:
        self.assertEqual(
            document_category("gamemag", "https://gamemag.ru/news/137784/example", "publication"),
            "news",
        )
        self.assertEqual(
            document_category("gamemag", "https://gamemag.ru/specials/24557/example", "publication"),
            "article",
        )
        self.assertIsNone(
            document_category("gamemag", "https://gamemag.ru/games/example", "publication")
        )

    def test_cache_only_rebuilds_inventory_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory) / "data"
            source_dir = data_dir / "discovery" / "gamingonlinux"
            source_dir.mkdir(parents=True)
            (source_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
            (source_dir / "gamingonlinux_sitemap.xml").write_text(
                "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
                "<url><loc>https://www.gamingonlinux.com/2026/example</loc></url>\n"
                "</urlset>",
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                summary = discover_sitemaps(
                    data_dir=data_dir,
                    output_dir=data_dir / "inventory",
                    sources=["gamingonlinux"],
                    cache_only=True,
                )

            self.assertTrue(summary["cache_only"])
            self.assertEqual(summary["total_unique_urls"], 1)
            self.assertEqual(summary["errors"], [])

    def test_merge_inventories_deduplicates_urls_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            header = "source\tcategory\turl\tsitemap_url\n"
            (first / "url_inventory.tsv").write_text(
                header
                + "one\tnews\thttps://example.org/a?utm_source=test\thttps://example.org/one.xml\n"
                + "one\tnews\thttps://example.org/b\thttps://example.org/one.xml\n",
                encoding="utf-8",
            )
            (second / "url_inventory.tsv").write_text(
                header
                + "two\tarticle\thttps://example.net/c\thttps://example.net/two.xml\n"
                + "one\tnews\thttps://example.org/a\thttps://example.org/other.xml\n",
                encoding="utf-8",
            )

            summary = merge_inventories([first, second], root / "merged")

            self.assertEqual(summary["input_rows"], 4)
            self.assertEqual(summary["total_unique_urls"], 3)
            self.assertEqual(summary["duplicate_urls_removed"], 1)
            merged = (root / "merged" / "url_inventory.tsv").read_text(encoding="utf-8")
            self.assertEqual(len(merged.splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
