from __future__ import annotations
import bz2
import contextlib
import io
import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_lemmas import MAGIC, build_dictionary, fnv, main, opencorpora_pairs, pymorphy_pairs, pymorphy_source, wordnet_pairs


class LemmaDictionaryTests(unittest.TestCase):
    def write_packaged_fixture(self, root):
        meta = {"format_version": "2.4", "language_code": "ru",
                "source_revision": "test", "compile_options": {"paradigm_prefixes": ["", "по", "наи"]}}
        (root / "meta.json").write_text(json.dumps(list(meta.items())), encoding="utf-8")
        (root / "suffixes.json").write_text(json.dumps(["а", "ы", ""]), encoding="utf-8")
        # Three forms: suffix IDs, tag IDs (unused), prefix IDs.
        values = [0, 1, 2, 0, 0, 0, 0, 1, 2]
        (root / "paradigms.array").write_bytes(struct.pack("<HH9H", 1, len(values), *values))
        (root / "words.dawg").write_bytes(b"dummy-reader-fixture")

    def test_packaged_data_reconstructs_prefix_suffix_and_empty_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_packaged_fixture(root)
            records = [("игра", (0, 0)), ("поигры", (0, 1)), ("наиигр", (0, 2)), ("c++", (0, 0))]
            self.assertEqual(list(pymorphy_pairs(root, records=records)),
                             [("ru:игра", "игра"), ("ru:поигры", "игра"), ("ru:наиигр", "игра")])

    def test_packaged_data_ambiguity_and_yo_survive_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_packaged_fixture(root)
            # "ёлки" can have its own unrelated paradigm; no e/yo folding.
            self.assertEqual(list(pymorphy_pairs(root, records=[("поёлкы", (0, 1))])), [("ru:поёлкы", "ёлка")])
            original = struct.unpack("<HH9H", (root / "paradigms.array").read_bytes())
            first = original[2:]
            # Alternative lemma ending -ы instead of -а, same observed form.
            second = (1, *first[1:])
            (root / "paradigms.array").write_bytes(struct.pack("<HH9HH9H", 2, 9, *first, 9, *second))
            pairs = pymorphy_pairs(root, records=[("поигры", (0, 1)), ("поигры", (1, 1))])
            result = build_dictionary(pairs, root / "result")
            self.assertEqual(result["max_ambiguity"], 2)

    def test_packaged_data_rejects_corrupt_paradigms_references_and_affixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_packaged_fixture(root)
            for records in ([('игра', (9, 0))], [('игра', (0, 9))], [('игра', (0, 1))], []):
                with self.subTest(records=records), self.assertRaises(ValueError):
                    list(pymorphy_pairs(root, records=records))
            data = (root / "paradigms.array").read_bytes()
            for broken in (data[:-1], data + b"x", b"\0\0", struct.pack("<HH", 1, 4),
                           struct.pack("<HH3H", 1, 3, 999, 0, 0)):
                (root / "paradigms.array").write_bytes(broken)
                with self.subTest(broken=broken), self.assertRaises(ValueError):
                    list(pymorphy_pairs(root, records=[("игра", (0, 0))]))

    def test_packaged_data_requires_expected_format_and_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for meta in ({"format_version": "3.0", "language_code": "ru"},
                         {"format_version": "2.4", "language_code": "en"}):
                (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
                with self.assertRaises(ValueError):
                    list(pymorphy_pairs(root, records=[]))

    def test_packaged_source_records_versions_and_actual_data_hashes(self):
        # Package/decoder are optional for ordinary unit tests; no imports of an analyzer.
        import types
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_packaged_fixture(root)
            modules = {"pymorphy3_dicts_ru": types.SimpleNamespace(get_path=lambda: str(root)),
                       "dawg_python": types.SimpleNamespace()}
            with patch.dict("sys.modules", modules), patch("prepare_lemmas.importlib.metadata.version", return_value="test"):
                path, source = pymorphy_source()
            self.assertEqual(path, root)
            self.assertEqual(source["distribution_version"], "test")
            self.assertEqual(source["source_revision"], "test")
            self.assertEqual(len(source["files_sha256"]), 4)
            self.assertEqual(len(source["sha256"]), 64)

    def test_packaged_source_hashes_legacy_prefix_file_when_used(self):
        import types
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_packaged_fixture(root)
            meta = dict(json.loads((root / "meta.json").read_text()))
            prefixes = meta.pop("compile_options")["paradigm_prefixes"]
            (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (root / "paradigm-prefixes.json").write_text(json.dumps(prefixes), encoding="utf-8")
            modules = {"pymorphy3_dicts_ru": types.SimpleNamespace(get_path=lambda: str(root)),
                       "dawg_python": types.SimpleNamespace()}
            with patch.dict("sys.modules", modules), patch("prepare_lemmas.importlib.metadata.version", return_value="test"):
                _, source = pymorphy_source()
            self.assertIn("paradigm-prefixes.json", source["files_sha256"])
            self.assertEqual(list(pymorphy_pairs(root, records=[("поигры", (0, 1))])), [("ru:поигры", "игра")])

    def test_packaged_cli_downloads_only_wordnet_not_unavailable_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "wordnet.tar.gz"
            archive.write_bytes(b"archive handled by mocked reader")
            source = {"name": "opencorpora", "distribution": "pymorphy3-dicts-ru"}
            with (patch("sys.argv", ["prepare_lemmas.py", "--pymorphy", "--download", "--output", str(root / "out")]),
                  patch("prepare_lemmas.pymorphy_source", return_value=(root, source)),
                  patch("prepare_lemmas.pymorphy_pairs", return_value=iter([("ru:игры", "игра")])),
                  patch("prepare_lemmas.wordnet_pairs", return_value=iter([("n:game", "game")])),
                  patch("prepare_lemmas.download", return_value=archive) as download,
                  patch("prepare_lemmas.build_dictionary") as build,
                  contextlib.redirect_stdout(io.StringIO())):
                def fake_build(pairs, output, sources):
                    self.assertEqual(list(pairs), [("ru:игры", "игра"), ("n:game", "game")])
                    self.assertEqual([s["name"] for s in sources], ["opencorpora", "wordnet"])
                    return {"entries": 2}
                build.side_effect = fake_build
                self.assertEqual(main(), 0)
                download.assert_called_once_with("wordnet", Path("data/lemma_sources"))

    def test_xml_forms_multiple_lemmas_and_yo(self):
        xml = '''<dictionary><lemmata>
        <lemma id="1"><l t="Игра"/><f t="игры"/><f t="играми"/></lemma>
        <lemma id="2"><l t="ёлка"/><f t="ёлки"/></lemma>
        <lemma id="3"><l t="сталь"/><f t="стали"/></lemma>
        <lemma id="4"><l t="стать"/><f t="стали"/></lemma>
        <lemma id="5"><l t="как-нибудь"/><f t="как-нибудь"/></lemma>
        </lemmata><links><link from="1" to="2" type="1"/></links></dictionary>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dict.xml.bz2"
            path.write_bytes(bz2.compress(xml.encode()))
            pairs = set(opencorpora_pairs(path))
            self.assertIn(("ru:игры", "игра"), pairs)
            self.assertIn(("ru:ёлки", "ёлка"), pairs)
            self.assertIn(("ru:стали", "сталь"), pairs)
            self.assertIn(("ru:стали", "стать"), pairs)
            self.assertFalse(any("как-нибудь" in key for key, _ in pairs))

    def test_wordnet_indices_exceptions_and_compounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wn.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                for pos in ("noun", "verb", "adj", "adv"):
                    for name, data in (("index." + pos, b"  license header\ngame n 1\nchild n 1\naxe n 1\naxis n 1\ngame_over n 1\n"),
                                       (pos + ".exc", b"children child\naxes axe axis\nbogus notindictionary\n")):
                        member = tarfile.TarInfo("dict/" + name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
            pairs = set(wordnet_pairs(path))
            self.assertIn(("n:game", "game"), pairs)
            self.assertIn(("x:n:children", "child"), pairs)
            self.assertIn(("x:v:axes", "axis"), pairs)
            self.assertFalse(any("_" in key for key, _ in pairs))
            self.assertFalse(any("notindictionary" == lemma for _, lemma in pairs))

    def test_dedup_binary_offsets_checksum_and_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pairs = [("ru:игры", "игра"), ("ru:стали", "стать"),
                     ("ru:стали", "сталь"), ("ru:игры", "игра")]
            manifest = build_dictionary(pairs, root / "first")
            build_dictionary(reversed(pairs), root / "second")
            data = (root / "first/dictionary.bin").read_bytes()
            self.assertEqual(data, (root / "second/dictionary.bin").read_bytes())
            self.assertEqual(data[:8], MAGIC)
            count, blob, checksum = struct.unpack_from("<QQQ", data, 8)
            self.assertEqual(count, 2)
            self.assertEqual(len(data), 32 + count * 16 + blob)
            self.assertEqual(fnv(data[32:]), checksum)
            self.assertEqual(manifest["analyses"], 3)
            self.assertEqual(manifest["max_ambiguity"], 2)
            strings = data[32 + count * 16:]
            key, size, values, length = struct.unpack_from("<IIII", data, 32)
            self.assertEqual(strings[key:key + size].decode(), "ru:игры")
            self.assertEqual(strings[values:values + length], "игра\0".encode())
            self.assertEqual(json.loads((root / "first/manifest.json").read_text(encoding="utf-8"))["sha256"], manifest["sha256"])

    def test_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dict"
            build_dictionary([("n:game", "game")], output)
            before = (output / "dictionary.bin").read_bytes()
            with self.assertRaises(FileExistsError):
                build_dictionary([("n:new", "new")], output)
            self.assertEqual(before, (output / "dictionary.bin").read_bytes())

    def test_failed_build_cleans_private_staging(self):
        for pairs in ([], [("ru:bad\nkey", "x")], [("n:game", "")],
                      [("n:game", "x" + str(i)) for i in range(65)]):
            with self.subTest(pairs=pairs[:1]), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    build_dictionary(pairs, Path(directory) / "dict")
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
