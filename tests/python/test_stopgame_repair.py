from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mongomock

from src.stopgame_repair import repair_frontier, repair_inventory_file
from src.utils import canonicalize_source_url


OLD = "https://stopgame.ru/news/10/example"
NEW = "https://stopgame.ru/newsdata/10/example"


class StopGameRepairTests(unittest.TestCase):
    def test_only_numeric_stopgame_news_urls_change(self) -> None:
        self.assertEqual(canonicalize_source_url("stopgame", OLD), NEW)
        self.assertEqual(canonicalize_source_url("stopgame", NEW), NEW)
        self.assertEqual(canonicalize_source_url("gamemag", "https://gamemag.ru/news/10/example"),
                         "https://gamemag.ru/news/10/example")
        self.assertEqual(canonicalize_source_url("stopgame", "https://stopgame.ru/news/latest"),
                         "https://stopgame.ru/news/latest")

    def test_inventory_repair_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "url_inventory.tsv"
            original = (
                "source\tcategory\turl\tsitemap_url\n"
                f"stopgame\tnews\t{OLD}\thttps://stopgame.ru/sitemap.xml\n"
            )
            path.write_text(original, encoding="utf-8")
            self.assertEqual(repair_inventory_file(path), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(repair_inventory_file(path, apply=True), 1)
            self.assertIn(NEW, path.read_text(encoding="utf-8"))
            backup = path.with_name(path.name + ".before_stopgame_news_repair.bak")
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(repair_inventory_file(path, apply=True), 0)

    def test_frontier_repair_archives_gone_and_preserves_existing_new_state(self) -> None:
        db = mongomock.MongoClient().test
        db.frontier.insert_one({
            "_id": OLD, "url": OLD, "source": "stopgame", "state": "gone",
            "attempts": 1, "last_error": "HTTP 404", "category_hint": "news",
        })
        db.frontier.insert_one({
            "_id": NEW, "url": NEW, "source": "stopgame", "state": "done",
            "attempts": 1,
        })
        self.assertEqual(repair_frontier(db), 1)
        self.assertEqual(repair_frontier(db, apply=True), 1)
        self.assertIsNone(db.frontier.find_one({"_id": OLD}))
        self.assertEqual(db.frontier.find_one({"_id": NEW})["state"], "done")
        self.assertEqual(db.frontier_url_repairs.find_one({"_id": OLD})["original"]["last_error"],
                         "HTTP 404")
        self.assertEqual(repair_frontier(db, apply=True), 0)

    def test_frontier_repair_rejects_saved_old_document(self) -> None:
        db = mongomock.MongoClient().test
        db.frontier.insert_one({
            "_id": OLD, "url": OLD, "source": "stopgame", "state": "gone", "attempts": 1,
        })
        db.documents.insert_one({"_id": OLD})
        with self.assertRaisesRegex(RuntimeError, "сохранённая страница"):
            repair_frontier(db, apply=True)


if __name__ == "__main__":
    unittest.main()
