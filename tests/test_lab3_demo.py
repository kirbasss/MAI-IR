from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_tokenizer import ROOT, ensure_built
from demo_lab3 import demonstration, latest_snapshot
from src.token_corpus import export_snapshot


class Lab3DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = ensure_built()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.snapshot = self.root / "snapshot"
        self.documents = [
            {"url": "https://example.test/version", "source": "gamemag", "parsed": {
                "title": "Обновление игры", "text": "Игра обновилась до версии 3.0.1. Исправлены ошибки."}},
            {"url": "https://example.test/languages", "source": "pcgamer", "parsed": {
                "title": "Languages", "text": "Written in C, C++ and C#. The game uses C++17."}},
            {"url": "https://example.test/stalker", "source": "stopgame", "parsed": {
                "title": "Обзор", "text": "Новая игра S.T.A.L.K.E.R. 2 вышла."}},
        ]
        export_snapshot(self.documents, self.snapshot, origin="test")

    def test_default_demo_uses_real_metadata_and_native_rules(self):
        text = demonstration(self.snapshot, self.binary)
        self.assertIn("DOCUMENTS: 3", text)
        self.assertIn("SOURCE: GameMAG", text)
        self.assertIn("TITLE: Обновление игры", text)
        self.assertIn("https://example.test/languages", text)
        self.assertIn("RULES: gaming-v3", text)
        self.assertIn("Версия сохранена одним токеном: 3.0.1.", text)
        self.assertIn("c++, c#", text)
        self.assertIn("stalker", text)
        self.assertIn("Точки удалены", text)

    def test_byte_offsets_refer_to_original_full_utf8_field(self):
        text = demonstration(self.snapshot, self.binary, document_id=1, find="3.0.1", context=0)
        original = self.documents[0]["parsed"]["text"].encode()
        start = original.index(b"3.0.1")
        self.assertIn(f"[{start},{start+5})", text)
        self.assertIn(f"ORIGINAL [UTF-8 bytes {start}:{start+5}]:\n3.0.1\n", text)
        row = next(line for line in text.splitlines() if f"[{start},{start+5})" in line)
        self.assertEqual(row.split()[0], "4")

    def test_custom_title_and_missing_fragment(self):
        text = demonstration(self.snapshot, self.binary, document_id=1, field="title", context=0)
        self.assertIn("FIELD: title", text)
        self.assertIn("Обновление", text)
        with self.assertRaises(ValueError):
            demonstration(self.snapshot, self.binary, document_id=1, find="not in article")

    def test_cli_output_is_utf8_and_refuses_overwrite(self):
        output = self.root / "demo.txt"
        command = [sys.executable, str(ROOT / "demo_lab3.py"), "--snapshot", str(self.snapshot),
                   "--document-id", "1", "--find", "3.0.1", "--output", str(output)]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), result.stdout.decode("utf-8").replace("\r\n", "\n"))
        before = output.read_bytes()
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(output.read_bytes(), before)

    def test_latest_snapshot_ignores_unpublished_directory(self):
        staging = self.root / ".unfinished"
        staging.mkdir()
        (staging / "manifest.json").write_text(json.dumps({"created_at": "2099-01-01", "format": "MAIIR3-v1"}))
        (staging / "corpus.bin").write_bytes(b"unfinished")
        self.assertEqual(latest_snapshot(self.root), self.snapshot)


if __name__ == "__main__":
    unittest.main()
