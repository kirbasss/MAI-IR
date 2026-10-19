from __future__ import annotations

import unittest
import time

import mongomock

from src.corpus_repair import repair_saved_documents


class CorpusRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = mongomock.MongoClient()["test"]
        self.listing = "https://www.pcgamer.com/games/adventure/"
        self.gallery = "https://www.pcgamer.com/fallout-gallery/"
        self.redirect = "https://www.pcgamer.com/old-story/"
        self.db.frontier.insert_many([
            {"_id": self.listing, "url": self.listing, "source": "pcgamer",
             "state": "done", "available_at": 100},
            {"_id": self.gallery, "url": self.gallery, "source": "pcgamer",
             "state": "done", "available_at": 100},
            {"_id": self.redirect, "url": self.redirect, "source": "pcgamer",
             "state": "done", "available_at": 100},
        ])
        self.db.documents.insert_many([
            {"_id": self.listing, "url": self.listing, "source": "pcgamer",
             "final_url": self.listing, "content_sha256": "listing",
             "parsed": {"parse_error": "no article"}, "fetched_at": 50},
            {"_id": self.gallery, "url": self.gallery, "source": "pcgamer",
             "final_url": self.gallery, "raw_page_id": self.gallery,
             "content_sha256": "gallery", "fetched_at": 50,
             "parsed": {"parse_error": "no body"}},
            {"_id": self.redirect, "url": self.redirect, "source": "pcgamer",
             "final_url": "https://www.pcgamer.com/news/", "content_sha256": "redirect",
             "parsed": {"parse_error": "no article"}, "fetched_at": 50},
        ])
        self.db.raw_pages.insert_one({
            "_id": self.gallery, "content_sha256": "gallery",
            "html": "<h1>Gallery</h1><div id='gallery-body'>"
                    "<div class='gallery-image'><div class='gallery-text'>"
                    "<p>First caption.</p></div></div></div>",
        })

    def test_preview_apply_and_repeat_preserve_raw_html(self) -> None:
        expected = {"documents_excluded": 2, "frontier_excluded": 2,
                    "documents_reparsed": 1, "still_parse_errors": 0,
                    "inconsistent_raw": 0}
        self.assertEqual(repair_saved_documents(self.db), expected)
        self.assertNotIn("indexable", self.db.documents.find_one({"_id": self.listing}))
        self.assertEqual(self.db.frontier.find_one({"_id": self.listing})["state"], "done")

        self.assertEqual(repair_saved_documents(self.db, apply=True), expected)
        self.assertFalse(self.db.documents.find_one({"_id": self.listing})["indexable"])
        self.assertFalse(self.db.documents.find_one({"_id": self.redirect})["indexable"])
        self.assertEqual(self.db.frontier.find_one({"_id": self.redirect})["state"], "excluded")
        self.assertTrue(self.db.documents.find_one({"_id": self.gallery})["indexable"])
        self.assertEqual(self.db.documents.find_one({"_id": self.gallery})["fetched_at"], 50)
        self.assertIn("First caption", self.db.documents.find_one({"_id": self.gallery})["parsed"]["text"])
        self.assertEqual(self.db.raw_pages.find_one({"_id": self.gallery})["content_sha256"], "gallery")
        self.assertEqual(repair_saved_documents(self.db), {
            "documents_excluded": 0, "frontier_excluded": 0,
            "documents_reparsed": 0, "still_parse_errors": 0,
            "inconsistent_raw": 0,
        })

    def test_apply_refuses_active_worker(self) -> None:
        self.db.frontier.update_one({"_id": self.gallery}, {"$set": {
            "state": "in_progress", "available_at": int(time.time()) + 600,
        }})
        with self.assertRaises(RuntimeError):
            repair_saved_documents(self.db, apply=True)


if __name__ == "__main__":
    unittest.main()
