from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import mongomock

from src.token_corpus import MAGIC, export_snapshot, mongo_documents, snapshot_records


class CorpusExportTests(unittest.TestCase):
    def test_filters_excluded_errors_and_empty_but_keeps_title_only(self):
        db = mongomock.MongoClient().test
        db.documents.insert_many([
            {"_id": "c", "source": "stopgame", "parsed": {"title": "Фото", "text": ""}},
            {"_id": "b", "source": "pcgamer", "parsed": {"title": "PS5", "text": "a\n\x00b"}},
            {"_id": "a", "source": "stopgame", "indexable": False, "parsed": {"title": "Category"}},
            {"_id": "d", "source": "stopgame", "parsed": {"parse_error": "bad", "text": "Ignore"}},
            {"_id": "e", "source": "stopgame", "parsed": {"title": None, "text": "\n "}},
        ])
        before = list(db.documents.find())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            stats = export_snapshot(mongo_documents(db), output, origin="test")
            self.assertEqual(stats["documents"], 2)
            self.assertEqual(stats["title_only_documents"], 1)
            self.assertEqual(stats["input_utf8_bytes"], len("PS5a\n\x00bФото".encode()))
            records = list(snapshot_records(output / "corpus.bin"))
            self.assertEqual(records, [(1, "pcgamer", b"PS5", b"a\n\x00b"),
                                       (2, "stopgame", "Фото".encode(), b"")])
            metadata = [json.loads(line) for line in (output / "documents.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["url"] for row in metadata], ["b", "c"])
            self.assertNotIn("text", metadata[0])
        self.assertEqual(list(db.documents.find()), before)

    def test_failed_export_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            for documents in ([], [{"url": "a", "source": "invalid\nsource", "parsed": {"text": "abc"}}]):
                with self.subTest(documents=documents), self.assertRaises(ValueError):
                    export_snapshot(documents, output, origin="test")
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_snapshot_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot"
            doc = {"url": "a", "source": "test", "parsed": {"title": "Hello"}}
            first = export_snapshot([doc], output, origin="test")
            before = (output / "corpus.bin").read_bytes()
            with self.assertRaises(FileExistsError):
                export_snapshot([doc], output, origin="test")
            self.assertEqual((output / "corpus.bin").read_bytes(), before)
            self.assertEqual(first["sources"], {"test": 1})

    def test_reader_rejects_bad_header_and_partial_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.bin"
            for value in (b"bad", MAGIC + b"\x01", MAGIC + b"\x00" * 8):
                with self.subTest(value=value):
                    path.write_bytes(value)
                    with self.assertRaises(ValueError):
                        list(snapshot_records(path))


if __name__ == "__main__":
    unittest.main()
