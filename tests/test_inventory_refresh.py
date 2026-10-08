from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.inventory_refresh import refresh_inventory


HEADER = "source\tcategory\turl\tsitemap_url\n"
OLD = "stopgame\tnews\thttps://stopgame.ru/newsdata/1/old\thttps://stopgame.ru/sitemap/news_1.xml\n"
NEW = "stopgame\tnews\thttps://stopgame.ru/newsdata/2/new\thttps://stopgame.ru/sitemap/news_2.xml\n"


class InventoryRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = self.root / "inventory_all" / "url_inventory.tsv"
        self.inventory.parent.mkdir()
        self.inventory.write_text(HEADER + OLD, encoding="utf-8")

    def test_refresh_keeps_old_urls_and_adds_new_ones(self) -> None:
        def discover(**kwargs):
            self.assertTrue(kwargs["refresh_roots"])
            source_dir = kwargs["output_dir"]
            source_dir.mkdir()
            (source_dir / "url_inventory.tsv").write_text(
                HEADER + OLD + NEW, encoding="utf-8"
            )
            return {"sources": {"stopgame": {"urls": 2}}, "errors": []}

        with patch("src.inventory_refresh.discover_sitemaps", side_effect=discover):
            summary = refresh_inventory(
                data_dir=self.root, inventory_path=self.inventory,
                sources=["stopgame"],
            )

        self.assertEqual(summary["total_unique_urls"], 2)
        self.assertEqual(summary["refreshed_sources"], {"stopgame": 2})
        self.assertEqual(summary["new_inventory_urls"], 1)
        saved = self.inventory.read_text(encoding="utf-8")
        self.assertIn(OLD, saved)
        self.assertIn(NEW, saved)
        self.assertEqual(len(saved.splitlines()), 3)

    def test_unchanged_urls_do_not_replace_inventory_file(self) -> None:
        before = self.inventory.stat().st_mtime_ns

        def discover(**kwargs):
            source_dir = kwargs["output_dir"]
            source_dir.mkdir()
            (source_dir / "url_inventory.tsv").write_text(HEADER + OLD, encoding="utf-8")
            return {"sources": {"stopgame": {"urls": 1}}, "errors": []}

        with patch("src.inventory_refresh.discover_sitemaps", side_effect=discover):
            summary = refresh_inventory(
                data_dir=self.root, inventory_path=self.inventory,
                sources=["stopgame"],
            )
        self.assertEqual(summary["new_inventory_urls"], 0)
        self.assertEqual(self.inventory.stat().st_mtime_ns, before)

    def test_error_preserves_previous_inventory(self) -> None:
        original = self.inventory.read_bytes()
        with patch("src.inventory_refresh.discover_sitemaps", return_value={
            "sources": {"stopgame": {"urls": 0}},
            "errors": [{"error": "HTTP 503"}],
        }):
            with self.assertRaisesRegex(RuntimeError, "Не обновлён ни один источник"):
                refresh_inventory(
                    data_dir=self.root, inventory_path=self.inventory,
                    sources=["stopgame"],
                )
        self.assertEqual(self.inventory.read_bytes(), original)

    def test_refresh_replaces_old_stopgame_alias_even_without_new_urls(self) -> None:
        self.inventory.write_text(
            HEADER + OLD.replace("/newsdata/", "/news/"), encoding="utf-8"
        )

        def discover(**kwargs):
            source_dir = kwargs["output_dir"]
            source_dir.mkdir()
            (source_dir / "url_inventory.tsv").write_text(HEADER + OLD, encoding="utf-8")
            return {"sources": {"stopgame": {"urls": 1}}, "errors": []}

        with patch("src.inventory_refresh.discover_sitemaps", side_effect=discover):
            summary = refresh_inventory(
                data_dir=self.root, inventory_path=self.inventory,
                sources=["stopgame"],
            )
        self.assertEqual(summary["new_inventory_urls"], 0)
        self.assertEqual(summary["normalized_urls"], 1)
        self.assertIn(OLD, self.inventory.read_text(encoding="utf-8"))
        self.assertNotIn("/news/1/old", self.inventory.read_text(encoding="utf-8"))

    def test_failed_site_does_not_block_another_site(self) -> None:
        def discover(**kwargs):
            source = kwargs["sources"][0]
            if source == "stopgame":
                return {"sources": {source: {"urls": 0}},
                        "errors": [{"error": "HTTP 503"}]}
            source_dir = kwargs["output_dir"]
            source_dir.mkdir()
            (source_dir / "url_inventory.tsv").write_text(
                HEADER
                + "gamemag\tnews\thttps://gamemag.ru/news/3/new\t"
                  "https://gamemag.ru/sitemap.xml\n",
                encoding="utf-8",
            )
            return {"sources": {source: {"urls": 1}}, "errors": []}

        with patch("src.inventory_refresh.discover_sitemaps", side_effect=discover):
            summary = refresh_inventory(
                data_dir=self.root, inventory_path=self.inventory,
                sources=["stopgame", "gamemag"],
            )
        self.assertEqual(summary["new_inventory_urls"], 1)
        self.assertIn("stopgame", summary["refresh_errors"])
        self.assertIn(OLD, self.inventory.read_text(encoding="utf-8"))
        self.assertIn("https://gamemag.ru/news/3/new", self.inventory.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
