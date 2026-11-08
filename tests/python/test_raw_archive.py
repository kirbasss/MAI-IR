"""Lossless archive, fail-closed eviction and removable-storage regression checks."""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mongomock
from bson import BSON, Binary, Int64

from src.crawler import Crawler
from src.parser_worker import ParserWorker
from src.raw_archive import (RawArchiveError, choose_auto_batch, has_ready_raw, decode_member, evict_archive,
                             export_raw, file_sha, manifest, read_archived_raw,
                             sample_restore, verify_archive, write_json)
from tests.python.test_crawler import HTML_2, URL, config, responses


def quiet(*args, **kwargs):
    pass


class RawArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "batch"
        self.client = mongomock.MongoClient(); self.db = self.client["test"]
        self.path = Path(self.temp.name) / "urls.tsv"
        self.path.write_text(f"stopgame\t{URL}\n", encoding="utf-8")
        self.cfg = replace(config(self.path), parse_mode="deferred")
        self.clock = [1000]
        # mongomock 4.3 does not yet accept PyMongo's optional bulk 'sort'.
        # Translate ONLY in this test adapter; real bulk_write is covered by
        # the opt-in integration test against MongoDB.
        def bulk(operations, **kwargs):
            return SimpleNamespace(modified_count=sum(
                self.db.raw_pages.update_one(op._filter, op._doc).modified_count for op in operations))
        mock_bulk = patch.object(self.db.raw_pages, "bulk_write", side_effect=bulk)
        mock_bulk.start(); self.addCleanup(mock_bulk.stop)

    def crawl(self, session=None, mode="normal"):
        with redirect_stdout(io.StringIO()):
            return Crawler(self.cfg, self.db, session=session or responses(), now=lambda: self.clock[0]).run(mode=mode)

    def parse(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return ParserWorker(self.cfg, self.db, now=lambda: self.clock[0]).run(**kwargs)

    def ready(self):
        self.crawl(); self.parse()
        return self.db.raw_pages.find_one({"_id": URL})

    def export(self, **kwargs):
        return export_raw(self.db, self.root, minimum_gib=0, progress=quiet, **kwargs)

    def evict(self, **kwargs):
        return evict_archive(self.db, self.root, minimum_gib=0, progress=quiet, **kwargs)

    def test_auto_new_batch_uses_existing_root_and_does_not_create_files(self):
        self.ready()
        first, existing = choose_auto_batch(self.db, self.root.parent, progress=quiet)
        second, _ = choose_auto_batch(self.db, self.root.parent, progress=quiet)
        self.assertFalse(existing)
        self.assertEqual(first.parent, self.root.parent)
        self.assertTrue(first.name.startswith("raw_"))
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())

    def test_auto_empty_or_only_ineligible_data_creates_no_batch(self):
        self.assertEqual(choose_auto_batch(self.db, self.root.parent, progress=quiet), (None, False))
        self.ready()
        self.db.documents.update_one({"_id": URL}, {"$set": {"content_sha256": "different"}})
        self.assertFalse(has_ready_raw(self.db))
        self.assertEqual(choose_auto_batch(self.db, self.root.parent, progress=quiet), (None, False))

    def test_auto_missing_root_is_not_created(self):
        with self.assertRaisesRegex(RawArchiveError, "недоступен"):
            choose_auto_batch(self.db, self.root)
        self.assertFalse(self.root.exists())

    def test_auto_resumes_completed_export_without_relying_on_proof_files(self):
        self.ready(); self.root = self.root.parent / "raw_saved"
        self.export()
        self.assertEqual(choose_auto_batch(self.db, self.root.parent, progress=quiet), (self.root, True))

    def test_auto_skips_finished_batches_and_has_no_work_after_eviction(self):
        self.ready(); self.root = self.root.parent / "raw_saved"
        self.export(); self.evict()
        self.assertEqual(choose_auto_batch(self.db, self.root.parent, progress=quiet), (None, False))

    def test_auto_incomplete_export_is_retained_and_never_resumed(self):
        self.ready()
        incomplete = self.root.parent / "raw_incomplete"
        incomplete.mkdir(); (incomplete / "INCOMPLETE").touch()
        chosen, existing = choose_auto_batch(self.db, self.root.parent, progress=quiet)
        self.assertFalse(existing)
        self.assertNotEqual(chosen, incomplete)
        self.assertTrue((incomplete / "INCOMPLETE").exists())
        self.assertIn("html", self.db.raw_pages.find_one({"_id": URL}))

    def test_auto_foreign_database_archive_is_not_resumed(self):
        self.ready(); self.root = self.root.parent / "raw_foreign"
        self.export()
        value = manifest(self.root); value["database"] = "other"
        write_json(self.root / "manifest.json", value)
        chosen, existing = choose_auto_batch(self.db, self.root.parent, progress=quiet)
        self.assertFalse(existing)
        self.assertNotEqual(chosen, self.root)

    def test_lossless_bson_metadata_cyrillic_and_no_mutations_during_export(self):
        self.ready()
        self.db.raw_pages.update_one({"_id": URL}, {"$set": {
            "html": "<h1>Привет, игроки! Ёж 😀</h1>", "blob": Binary(b"\x00\xff"), "large": Int64(2**40)}})
        before = self.db.raw_pages.find_one({"_id": URL})
        result = self.export()
        self.assertEqual(result["records"], 1)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(self.db.raw_archives.count_documents({}), 0)
        self.assertTrue(verify_archive(self.root, progress=quiet)["verified"])
        self.assertTrue(sample_restore(self.db, self.root)["ok"])
        self.assertEqual(self.client.list_database_names(), ["test"])
        self.evict()
        stub = self.db.raw_pages.find_one({"_id": URL})
        self.assertNotIn("html", stub)
        self.assertEqual(stub["parse_state"], "archived")
        self.assertEqual(BSON.encode(read_archived_raw(stub)), BSON.encode(before))

    def test_legacy_raw_without_parse_state_or_generation_is_eligible(self):
        self.ready()
        for collection in (self.db.raw_pages, self.db.documents):
            collection.update_one({"_id": URL}, {"$unset": {"raw_generation": "", "parse_state": ""}})
        before = self.db.raw_pages.find_one({"_id": URL})
        self.export(); self.assertEqual(self.evict()["evicted"], 1)
        self.assertEqual(read_archived_raw(self.db.raw_pages.find_one({"_id": URL})), before)

    def test_pending_error_active_and_mismatched_versions_stay_hot(self):
        original = self.ready(); document = self.db.documents.find_one({"_id": URL})
        for reason in ("pending", "error", "active", "hash", "missing", "nonindexable", "parse_error"):
            url = URL + "/" + reason
            raw = copy.deepcopy(original); raw.update({"_id": url, "url": url})
            doc = copy.deepcopy(document); doc.update({"_id": url, "raw_page_id": url})
            if reason in {"pending", "error"}: raw["parse_state"] = reason
            if reason == "active": self.db.frontier.insert_one({"_id": url, "state": "in_progress"})
            if reason == "hash": doc["content_sha256"] = "other"
            if reason == "nonindexable": doc["indexable"] = False
            if reason == "parse_error": doc["parsed"] = {"parse_error": "bad selectors"}
            self.db.raw_pages.insert_one(raw)
            if reason != "missing": self.db.documents.insert_one(doc)
        self.assertEqual(self.export()["records"], 1)
        self.assertEqual(self.evict()["evicted"], 1)
        self.assertEqual(self.db.raw_pages.count_documents({"html": {"$type": "string"}}), 7)

    def test_corrupt_chunk_prevents_all_eviction(self):
        before = self.ready(); value = self.export()
        chunk = self.root / value["chunks"][0]["name"]
        data = bytearray(chunk.read_bytes()); data[-5] ^= 0xFF; chunk.write_bytes(data)
        with self.assertRaises(RawArchiveError): self.evict()
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)
        self.assertEqual(self.db.raw_archives.count_documents({}), 0)

    def test_corrupt_index_prevents_eviction(self):
        before = self.ready(); self.export()
        with (self.root / "index.sqlite").open("ab") as stream: stream.write(b"changed")
        with self.assertRaises(RawArchiveError): self.evict()
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)

    def test_incomplete_export_never_allows_eviction(self):
        before = self.ready(); self.export(); (self.root / "INCOMPLETE").touch()
        with self.assertRaisesRegex(RawArchiveError, "INCOMPLETE"): self.evict()
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL}), before)

    def test_changed_raw_and_missing_raw_are_not_evicted(self):
        self.ready(); self.export()
        self.db.raw_pages.update_one({"_id": URL}, {"$set": {"html": "New page", "raw_generation": "new"}})
        result = self.evict()
        self.assertEqual(result["evicted"], 0); self.assertEqual(result["changed_or_missing"], 1)
        self.assertEqual(self.db.raw_pages.find_one({"_id": URL})["html"], "New page")
        self.db.raw_pages.delete_one({"_id": URL})
        self.assertEqual(self.evict()["changed_or_missing"], 1)

    def test_repeated_eviction_is_idempotent_and_keeps_queue_and_documents(self):
        self.ready(); before = self.db.documents.find_one(); frontier = self.db.frontier.find_one()
        self.export(); self.assertEqual(self.evict()["evicted"], 1)
        stub = self.db.raw_pages.find_one()
        result = self.evict()
        self.assertEqual(result["already_archived"], 1); self.assertEqual(result["evicted"], 0)
        self.assertEqual(self.db.raw_pages.find_one(), stub)
        self.assertEqual(self.db.documents.find_one(), before)
        self.assertEqual(self.db.frontier.find_one(), frontier)

    def test_source_disk_guard_failure_prevents_source_mutation(self):
        before = self.ready(); self.export()
        guard = SimpleNamespace(check=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
        with self.assertRaisesRegex(RuntimeError, "disk full"): self.evict(disk_guard=guard)
        self.assertEqual(self.db.raw_pages.find_one(), before)

    def test_existing_directory_is_never_overwritten(self):
        self.ready(); value = self.export(); sha = file_sha(self.root / "manifest.json")
        with self.assertRaises(RawArchiveError): self.export()
        self.assertEqual(file_sha(self.root / "manifest.json"), sha)
        self.assertEqual(manifest(self.root)["batch_id"], value["batch_id"])

    def test_chunk_rotation_and_direct_random_access(self):
        original = self.ready(); doc = self.db.documents.find_one()
        for number in range(5):
            url = URL + str(number)
            self.db.raw_pages.insert_one({**original, "_id": url})
            self.db.documents.insert_one({**doc, "_id": url, "raw_page_id": url})
        value = self.export(chunk_bytes=1024)
        self.assertGreater(len(value["chunks"]), 1)
        self.evict()
        for stub in self.db.raw_pages.find():
            self.assertEqual(read_archived_raw(stub)["html"], original["html"])

    def test_304_and_same_hash_200_need_no_archive_disk(self):
        self.ready(); self.export(); self.evict()
        self.root.rename(self.root.with_name("offline"))
        self.clock[0] = 1010
        self.assertEqual(self.crawl(responses(status=304), mode="revisit")["unchanged"], 1)
        self.clock[0] = 1020
        self.assertEqual(self.crawl(mode="revisit")["unchanged"], 1)
        self.assertNotIn("html", self.db.raw_pages.find_one())

    def test_changed_download_replaces_stub_and_parses_without_archive(self):
        self.ready(); self.export(); self.evict(); self.root.rename(self.root.with_name("offline"))
        self.clock[0] = 1010
        self.assertEqual(self.crawl(responses(HTML_2), mode="revisit")["changed"], 1)
        raw = self.db.raw_pages.find_one()
        self.assertEqual(raw["parse_state"], "pending")
        self.assertNotIn("raw_archive", raw)
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.documents.find_one()["parsed"]["text"], "Changed text.")

    def test_explicit_reparse_reads_archive_without_http_and_preserves_stub(self):
        self.ready(); self.export(); self.evict()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP forbidden")):
            self.assertEqual(self.parse(reparse=True)["done"], 1)
        self.assertEqual(self.db.documents.find_one()["parsed"]["text"], "First text.")
        self.assertNotIn("html", self.db.raw_pages.find_one())
        self.assertEqual(self.db.raw_pages.find_one()["parse_state"], "archived")

    def test_missing_archive_does_not_masquerade_as_parse_error(self):
        self.ready(); self.export(); self.evict(); original = self.db.documents.find_one()
        self.root.rename(self.root.with_name("offline"))
        with self.assertRaises(RawArchiveError): self.parse(reparse=True)
        self.assertEqual(self.db.documents.find_one(), original)
        self.assertEqual(self.db.raw_pages.find_one()["parse_state"], "in_progress")
        self.assertFalse(self.db.documents.find_one()["parsed"].get("parse_error"))
        self.root.with_name("offline").rename(self.root)
        self.assertEqual(self.parse()["done"], 1)
        self.assertEqual(self.db.raw_pages.find_one()["parse_state"], "archived")

    def test_unsafe_pointer_or_bad_hash_is_rejected(self):
        self.ready(); self.export(); self.evict(); stub = self.db.raw_pages.find_one()
        for update in ({"chunk": "../outside.gz"}, {"offset": -1}, {"compressed_bytes": 0},
                       {"bson_sha256": "0" * 64}, {"html_sha256": "0" * 64}):
            modified = copy.deepcopy(stub); modified["raw_archive"].update(update)
            with self.assertRaises(RawArchiveError): read_archived_raw(modified)

    def test_wrong_database_never_evicted(self):
        self.ready(); self.export(); other = self.client["other"]
        with self.assertRaisesRegex(RawArchiveError, "другой базы"):
            evict_archive(other, self.root, minimum_gib=0, progress=quiet)
        self.assertEqual(other.list_collection_names(), [])
        self.assertIn("html", self.db.raw_pages.find_one())

    def test_invalid_chunk_path_in_manifest_is_rejected(self):
        self.ready(); value = self.export(); value["chunks"][0]["name"] = "../elsewhere.gz"
        write_json(self.root / "manifest.json", value)
        with self.assertRaises(RawArchiveError): self.evict()
        self.assertIn("html", self.db.raw_pages.find_one())

    def test_gzip_crc_failure_even_when_chunk_checksum_is_updated(self):
        self.ready(); value = self.export(); chunk = self.root / value["chunks"][0]["name"]
        data = bytearray(chunk.read_bytes()); data[-5] ^= 0xFF; chunk.write_bytes(data)
        value["chunks"][0]["sha256"] = file_sha(chunk); write_json(self.root / "manifest.json", value)
        with self.assertRaises(RawArchiveError): self.evict()
        self.assertIn("html", self.db.raw_pages.find_one())

    def test_bounded_decompression_rejects_oversized_bson(self):
        encoded = BSON.encode({"_id": URL, "html": "x" * 4096})
        data = gzip.compress(encoded)
        row = {"compressed_bytes": len(data), "bson_bytes": len(encoded), "url": URL,
               "bson_sha256": hashlib.sha256(encoded).hexdigest(), "html_sha256": "unused"}
        with patch("src.raw_archive.MAX_BSON", 1024), self.assertRaises(RawArchiveError):
            decode_member(data, row)


if __name__ == "__main__":
    unittest.main()
