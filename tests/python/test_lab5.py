from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
import copy
from compare_zipf import validate
from src.token_corpus import export_snapshot
from run_lab5 import sample_curves, verify_snapshot


class Lab5ExportTests(unittest.TestCase):
    def comparison_pair(self):
        base = {"snapshot": {"corpus_sha256": "same", "groups_sha256": "same-types"},
                "statistics": {"documents": 1, "input_utf8_bytes": 20, "field": "both", "tokenizer": "gaming-v3",
                               "fit_min": 10, "fit_max": 10000,
                               "groups": [{"name": "all", "documents": 1, "tokens": 4, "vocabulary": 4}]}}
        lemma = copy.deepcopy(base)
        lemma["statistics"]["representation"] = "lemma_unambiguous"
        lemma["statistics"]["groups"][0]["vocabulary"] = 3
        return base, lemma

    def test_comparison_requires_identical_snapshot_and_parameters(self):
        base, lemma = self.comparison_pair()
        validate(base, lemma)
        lemma["snapshot"]["corpus_sha256"] = "other"
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate(base, lemma)
        base, lemma = self.comparison_pair()
        lemma["statistics"]["field"] = "title"
        with self.assertRaisesRegex(ValueError, "settings"):
            validate(base, lemma)

    def test_comparison_rejects_token_duplication_and_vocabulary_growth(self):
        base, lemma = self.comparison_pair()
        lemma["statistics"]["groups"][0]["tokens"] = 5
        with self.assertRaisesRegex(ValueError, "Token mass"):
            validate(base, lemma)
        base, lemma = self.comparison_pair()
        lemma["statistics"]["groups"][0]["vocabulary"] = 5
        with self.assertRaisesRegex(ValueError, "increase vocabulary"):
            validate(base, lemma)

    def test_types_follow_document_filtering_and_have_a_verified_sidecar(self):
        documents = [
            {"url": "a", "source": "stopgame", "parsed": {"text": "Hello", "category": "news"}},
            {"url": "b", "source": "pcgamer", "parsed": {"text": "World", "category": "review"}},
            {"url": "c", "source": "test", "indexable": False, "parsed": {"text": "Excluded"}},
            {"url": "d", "source": "test", "parsed": {"text": "Other", "category": "custom"}},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot"
            export_snapshot(documents, snapshot, origin="test")
            manifest = verify_snapshot(snapshot)
            self.assertEqual(manifest["publication_types"], {"news": 1, "review": 1, "unknown": 1})
            self.assertEqual((snapshot / "groups.tsv").read_text(), "1\tnews\n2\treview\n3\tunknown\n")
            (snapshot / "groups.tsv").write_text("changed")
            with self.assertRaisesRegex(ValueError, "groups SHA-256"):
                verify_snapshot(snapshot)

    def test_old_snapshots_without_types_remain_usable_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot"
            export_snapshot([{"url": "a", "source": "test", "parsed": {"text": "Hello"}}], path, origin="test")
            manifest = json.loads((path / "manifest.json").read_text())
            del manifest["groups_sha256"]
            (path / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(verify_snapshot(path)["documents"], 1)
            (path / "corpus.bin").write_bytes(b"bad")
            with self.assertRaises(ValueError):
                verify_snapshot(path)

    def test_plot_sampling_keeps_head_and_final_tail_and_checks_vocabulary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "counts.tsv"
            path.write_text("group\trank\tterm\tfrequency\n" +
                            "".join(f"all\t{i}\tword{i}\t1\n" for i in range(1, 1002)), encoding="utf-8")
            curves, tops = sample_curves(path, [{"name": "all", "vocabulary": 1001, "tokens": 1001}])
            self.assertEqual(curves["all"][0], (1, 1))
            self.assertEqual(curves["all"][-1], (1001, 1))
            self.assertEqual(len(tops["all"]), 20)
            self.assertLess(len(curves["all"]), 1001)
            with self.assertRaises(ValueError):
                sample_curves(path, [{"name": "all", "vocabulary": 1000, "tokens": 1001}])
            with self.assertRaisesRegex(ValueError, "Frequency sums"):
                sample_curves(path, [{"name": "all", "vocabulary": 1001, "tokens": 1002}])
