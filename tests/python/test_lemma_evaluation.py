import copy
import math
import unittest
from evaluate_lemmas import metrics, validate_judgments


class LemmaEvaluationTests(unittest.TestCase):
    def test_precision_uses_fixed_k_and_recall_all_relevant_pool_documents(self):
        score = metrics([1], {1: 1, 2: 1}, 5)
        self.assertEqual(score["precision"], 0.2)
        self.assertEqual(score["recall"], 0.5)
        self.assertAlmostEqual(score["ndcg"], 1 / (1 + 1 / math.log2(3)))

    def test_graded_ndcg_and_rank_order(self):
        grades = {1: 3, 2: 1}
        self.assertEqual(metrics([1, 2], grades, 2)["ndcg"], 1)
        self.assertLess(metrics([2, 1], grades, 2)["ndcg"], 1)

    def test_empty_and_no_relevant_cases_are_defined(self):
        self.assertEqual(metrics([], {1: 1}, 5), {"precision": 0, "recall": 0, "ndcg": 0})
        self.assertEqual(metrics([1], {}, 5), {"precision": 0, "recall": 0, "ndcg": 0})

    def test_duplicate_ranked_ids_and_invalid_cutoff_rejected(self):
        for ids, k in (([1, 1], 5), ([1], 0)):
            with self.assertRaises(ValueError):
                metrics(ids, {1: 1}, k)

    def test_invalid_judgments_rejected(self):
        base = {"document_ids": [1], "k": 5, "field": "title",
                "queries": [{"id": "q", "text": "games", "relevant": {"1": 1}}]}
        validate_judgments(base)
        for key, value in (("document_ids", [1, 1]), ("k", 101), ("field", "unknown"), ("queries", [])):
            bad = copy.deepcopy(base); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_judgments(bad)
        bad = copy.deepcopy(base); bad["queries"][0]["relevant"] = {"99": 1}
        with self.assertRaises(ValueError):
            validate_judgments(bad)


if __name__ == "__main__":
    unittest.main()
