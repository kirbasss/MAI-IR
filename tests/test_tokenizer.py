from __future__ import annotations

import csv
import io
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_tokenizer import DEFAULT_BINARY, ROOT, ensure_built
from src.token_corpus import MAGIC, export_snapshot


class TokenizerCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_built()

    def cli(self, *args, data=None):
        return subprocess.run([str(DEFAULT_BINARY), *map(str, args)], input=data, capture_output=True)

    def text(self, value):
        raw = value.encode() if isinstance(value, str) else value
        result = self.cli("text", data=raw)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        rows = list(csv.DictReader(io.StringIO(result.stdout.decode()), delimiter="\t"))
        return rows, json.loads(result.stderr)

    def terms(self, value):
        return [row["term"] for row in self.text(value)[0]]

    def test_ru_en_case_digits_and_yo(self):
        self.assertEqual(self.terms("ПРИВЕТ, Ёж и ЕЖ! GTA VI PS5 3D 2026"),
                         ["привет", "ёж", "и", "еж", "gta", "vi", "ps5", "3d", "2026"])

    def test_connectors_are_internal_and_normalized(self):
        self.assertEqual(self.terms("'Baldur’s' Half‑Life rockʼnʼroll а-б -test- a--b a—b a–b"),
                         ["baldur's", "half-life", "rock'n'roll", "а-б", "test", "a", "b", "a", "b", "a", "b"])

    def test_original_utf8_offsets_and_positions(self):
        text = "🎮 ЁЖ Baldur’s PS5"
        rows, stats = self.text(text)
        raw = text.encode()
        expected_original = ["ЁЖ", "Baldur’s", "PS5"]
        for position, (row, original) in enumerate(zip(rows, expected_original, strict=True)):
            self.assertEqual(int(row["position"]), position)
            self.assertEqual(raw[int(row["begin_byte"]):int(row["end_byte"])].decode(), original)
        self.assertEqual(stats["total"]["token_characters"], 2 + 8 + 3)
        self.assertAlmostEqual(stats["total"]["mean_token_characters"], 13 / 3)
        self.assertEqual(stats["total"]["input_utf8_bytes"], len(raw))

    def test_latin_accents_combining_marks_and_no_nfc(self):
        self.assertEqual(self.terms("POKÉMON ŁÓDŹ Straße café cafe\u0301"),
                         ["pokémon", "łódź", "straße", "café", "cafe\u0301"])

    def test_punctuation_emoji_nul_bom_and_linebreaks(self):
        self.assertEqual(self.terms("\ufeffa\r\nb\x00c_д\t🎮中文"), ["a", "b", "c", "д"])

    def test_empty_and_punctuation_only(self):
        for value in ("", " \n—🎮...", "\u0301"):
            with self.subTest(value=value):
                rows, stats = self.text(value)
                self.assertEqual(rows, [])
                self.assertEqual(stats["total"]["tokens"], 0)
                self.assertEqual(stats["total"]["mean_token_characters"], 0)

    def test_documented_difficult_names(self):
        self.assertEqual(self.terms("C++ C# S.T.A.L.K.E.R. v1.2.3"),
                         ["c++", "c#", "stalker", "v1.2.3"])

    def test_dotted_acronyms_three_or_more_letters(self):
        self.assertEqual(self.terms("S.T.A.L.K.E.R. S.T.A.L.K.E.R stalker A.B.C. a.b.c U.S.A. А.Б.В. Ё.Ж.И. É.T.É."),
                         ["stalker", "stalker", "stalker", "abc", "abc", "usa", "абв", "ёжи", "été"])
        self.assertEqual(self.terms("S.T.A.L.K.E.R.2 A.B.C.3.0.1 (A.B.C), D.E.F!"),
                         ["stalker", "2", "abc", "3.0.1", "abc", "def"])

    def test_dotted_acronyms_do_not_merge_short_or_ordinary_segments(self):
        self.assertEqual(self.terms("A.B a.b ab.cd.ef A.B.Cword AB.C.D.E word.A.B.C A.2.C A..B.C"),
                         ["a", "b", "a", "b", "ab", "cd", "ef", "a", "b", "cword", "ab", "c", "d", "e",
                          "word", "a", "b", "c", "a", "2", "c", "a", "b", "c"])
        self.assertEqual(self.terms("e.g. т.е. 3.0.1 v1.2.3"), ["e", "g", "т", "е", "3.0.1", "v1.2.3"])

    def test_dotted_acronym_offsets_and_normalized_length(self):
        value = "Игра S.T.A.L.K.E.R. и Ё.Ж.И. 2"
        rows, stats = self.text(value)
        originals = ["Игра", "S.T.A.L.K.E.R.", "и", "Ё.Ж.И.", "2"]
        raw = value.encode()
        for position, (row, original) in enumerate(zip(rows, originals, strict=True)):
            self.assertEqual(int(row["position"]), position)
            self.assertEqual(raw[int(row["begin_byte"]):int(row["end_byte"])].decode(), original)
        self.assertEqual([r["term"] for r in rows], ["игра", "stalker", "и", "ёжи", "2"])
        self.assertEqual(stats["total"]["token_characters"], 4 + 7 + 1 + 3 + 1)

    def test_long_dotted_chains_valid_and_rejected(self):
        rows, stats = self.text("A." * 10000)
        self.assertEqual([r["term"] for r in rows], ["a" * 10000])
        self.assertEqual(stats["total"]["token_characters"], 10000)
        rows, stats = self.text("A." * 10000 + "word")
        self.assertEqual(len(rows), 10001)
        self.assertEqual(rows[-1]["term"], "word")

    def test_language_names_boundaries_and_standards(self):
        self.assertEqual(self.terms("C c++ C# c++17 C#12 (C++), C#/CLI C+++ C## abc++ C++Builder"),
                         ["c", "c++", "c#", "c++17", "c#12", "c++", "c#", "cli", "c", "c", "abc", "c", "builder"])
        self.assertEqual(self.terms("a+b 2+2 C+ D++ F#"), ["a", "b", "2", "2", "c", "d", "f"])

    def test_versions_decimals_and_sentence_punctuation(self):
        self.assertEqual(self.terms("3.0.1 v1.2.3 V2.10 1.5 3.0.1-rc1 3.0.1. 3. Обзор 1..2 .3 1."),
                         ["3.0.1", "v1.2.3", "v2.10", "1.5", "3.0.1-rc1", "3.0.1", "3", "обзор", "1", "2", "3", "1"])
        self.assertEqual(self.terms("word.next GTA1.2 127.0.0.1"), ["word", "next", "gta1", "2", "127.0.0.1"])

    def test_special_token_offsets_and_character_counts(self):
        value = "ЁЖ: C++17, C# и версия V3.0.1."
        rows, stats = self.text(value)
        expected = ["ЁЖ", "C++17", "C#", "и", "версия", "V3.0.1"]
        raw = value.encode()
        for row, original in zip(rows, expected, strict=True):
            self.assertEqual(raw[int(row["begin_byte"]):int(row["end_byte"])].decode(), original)
        self.assertEqual(stats["total"]["token_characters"], sum(map(len, expected)))

    def test_real_corpus_examples(self):
        path = ROOT / "tests" / "fixtures" / "tokenizer_cases.json"
        for case in json.loads(path.read_text(encoding="utf-8")):
            with self.subTest(url=case["url"]):
                self.assertEqual(self.terms(case["text"]), case["expected"])

    def test_malformed_utf8_is_rejected(self):
        for raw in (b"\x80", b"a\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2\x82", b"a-\xff", b"C++\xff", b"1.\xff", b"A.B.\xff", b"A.B.C.\xff"):
            with self.subTest(raw=raw):
                result = self.cli("text", data=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"Invalid UTF-8", result.stderr)

    def test_large_multilingual_field(self):
        rows, stats = self.text("Игра PS5 " * 10000)
        self.assertEqual(len(rows), 20000)
        self.assertEqual(rows[-1]["position"], "19999")
        self.assertEqual(stats["total"]["tokens"], 20000)

    def test_corpus_fields_title_only_limit_and_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_snapshot([
                {"url": "a", "source": "ru", "parsed": {"title": "Заголовок", "text": "Игра PS5"}},
                {"url": "b", "source": "en", "parsed": {"title": "Gallery", "text": ""}},
            ], root / "snapshot", origin="test")
            corpus = root / "snapshot" / "corpus.bin"
            tokens = root / "tokens.tsv"
            stats_file = root / "stats.json"
            result = self.cli("corpus", corpus, "--output", tokens, "--stats", stats_file)
            self.assertEqual(result.returncode, 0, result.stderr)
            stats = json.loads(result.stdout)
            self.assertEqual(stats, json.loads(stats_file.read_text()))
            self.assertEqual(stats["documents"], 2)
            self.assertEqual(stats["title_only_documents"], 1)
            self.assertEqual(stats["total"]["tokens"], 4)
            rows = list(csv.DictReader(io.StringIO(tokens.read_text(encoding="utf-8")), delimiter="\t"))
            self.assertEqual([(r["document_id"], r["field"], r["position"]) for r in rows],
                             [("1", "title", "0"), ("1", "text", "0"), ("1", "text", "1"), ("2", "title", "0")])
            limited = self.cli("corpus", corpus, "--limit", 1)
            self.assertEqual(json.loads(limited.stdout)["documents"], 1)
            self.assertFalse(json.loads(limited.stdout)["token_output_enabled"])

    def test_corrupt_corpus_and_oversized_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.bin"
            cases = [b"bad", MAGIC + b"\x01", MAGIC + struct.pack("<QI", 1, 1000),
                     MAGIC + struct.pack("<QI", 1, 1) + b"x" + struct.pack("<Q", 2**40),
                     MAGIC + struct.pack("<QI", 0, 1)]
            for raw in cases:
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    self.assertNotEqual(self.cli("corpus", path).returncode, 0)

    def test_partial_output_is_not_published_and_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.tsv"
            result = self.cli("text", "--output", path, data=b"hello \xff")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])
            path.write_bytes(b"user data")
            result = self.cli("text", "--output", path, data=b"hello")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), b"user data")

    def test_invalid_arguments(self):
        for args in ([], ["unknown"], ["text", "--limit", "1"],
                     ["corpus", "x", "--limit", "0"], ["corpus", "x", "--limit", "-1"],
                     ["corpus", "x", "--limit", "1x"]):
            with self.subTest(args=args):
                self.assertNotEqual(self.cli(*args, data=b"").returncode, 0)


if __name__ == "__main__":
    unittest.main()
