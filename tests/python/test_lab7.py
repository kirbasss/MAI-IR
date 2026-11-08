import json
from pathlib import Path
import struct
import tempfile
import unittest
from src.token_corpus import export_snapshot
from run_lab7 import CATALOG_MAGIC, write_catalog


class IndexCatalogTests(unittest.TestCase):
    def snapshot(self,path):
        export_snapshot([{"source":"test","url":"https://example.test/игра","parsed":{"title":"Doom","text":"Game"}},
                         {"source":"test","url":"https://example.test/other","parsed":{"title":"Other"}}],path,origin="test")

    def test_catalog_preserves_ids_and_utf8_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); self.snapshot(root/"snapshot")
            self.assertEqual(write_catalog(root/"snapshot",root/"urls.bin"),2)
            with (root/"urls.bin").open("rb") as f:
                self.assertEqual(f.read(8),CATALOG_MAGIC)
                for expected in (1,2):
                    identifier,size=struct.unpack("<QI",f.read(12))
                    self.assertEqual(identifier,expected)
                    self.assertTrue(f.read(size).decode("utf-8").startswith("https://example.test/"))
                self.assertEqual(f.read(),b"")

    def test_duplicate_ids_and_missing_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); self.snapshot(root/"snapshot")
            metadata=root/"snapshot/documents.jsonl"
            rows=[json.loads(s) for s in metadata.read_text(encoding="utf-8").splitlines()]
            rows[1]["document_id"]=1
            metadata.write_text("\n".join(json.dumps(r) for r in rows),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"increasing"):
                write_catalog(root/"snapshot",root/"duplicate.bin")
            metadata.write_text(json.dumps(rows[0])+"\n",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"count"):
                write_catalog(root/"snapshot",root/"missing.bin")

    def test_existing_catalog_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); self.snapshot(root/"snapshot")
            target=root/"urls.bin"; target.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                write_catalog(root/"snapshot",target)
            self.assertEqual(target.read_bytes(),b"keep")
