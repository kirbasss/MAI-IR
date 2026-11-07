from __future__ import annotations

import unittest

import mongomock

from src.raw_migration import migrate_raw_pages


URL = "https://stopgame.ru/newsdata/1/example"


class RawMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = mongomock.MongoClient()["test"]
        self.db.documents.insert_one({
            "_id": URL, "url": URL, "source": "stopgame",
            "html": "<html>Example</html>", "fetched_at": 123,
            "content_sha256": "hash1", "parsed": {"title": "Example", "text": "Text"},
        })

    def test_dry_run_apply_and_resume(self) -> None:
        preview = migrate_raw_pages(self.db)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["legacy_documents"], 1)
        self.assertEqual(self.db.raw_pages.count_documents({}), 0)
        self.assertIn("html", self.db.documents.find_one({"_id": URL}))

        result = migrate_raw_pages(self.db, apply=True)
        self.assertEqual(result["migrated"], 1)
        self.assertEqual(result["remaining_html"], 0)
        self.assertEqual(result["documents_without_raw_reference"], 0)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["html"], "<html>Example</html>")
        document = self.db.documents.find_one({"_id": URL})
        self.assertNotIn("html", document)
        self.assertEqual(document["raw_page_id"], URL)
        self.assertEqual(document["parsed"]["text"], "Text")

        repeat = migrate_raw_pages(self.db, apply=True)
        self.assertEqual(repeat["migrated"], 0)
        self.assertEqual(repeat["raw_pages"], 1)

    def test_conflict_does_not_remove_original_html(self) -> None:
        self.db.raw_pages.insert_one({
            "_id": URL, "url": URL, "html": "different", "content_sha256": "hash2"
        })
        with self.assertRaisesRegex(ValueError, "Конфликт"):
            migrate_raw_pages(self.db, apply=True)
        self.assertIn("html", self.db.documents.find_one({"_id": URL}))


if __name__ == "__main__":
    unittest.main()
