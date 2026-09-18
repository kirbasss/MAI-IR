from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.parsers import PARSERS
from src.utils import soup


ROOT = Path(__file__).resolve().parents[1]
PARSED_ROOT = ROOT / "data" / "parsed"
EXPECTED_SELECTORS = {
    "playground": "div.article-content",
    "stopgame": "article#material_content",
    "ixbt_games": 'div[id^="publication-"].prose',
    "igromania": 'div[class*="material-content_"]',
}
EXPECTED_COUNTS = {
    "playground": 3,
    "stopgame": 5,
    "ixbt_games": 4,
    "igromania": 3,
}


def saved_documents() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(PARSED_ROOT.rglob("*.json"))
    ]


class SourceParserFixtureTests(unittest.TestCase):
    def test_all_saved_html_uses_confirmed_source_selector(self) -> None:
        documents = saved_documents()
        counts = {source: 0 for source in EXPECTED_COUNTS}

        for saved in documents:
            source = saved["source"]
            self.assertIn(source, EXPECTED_SELECTORS)
            raw_path = ROOT / Path(saved["raw_file"].replace("\\", "/"))
            raw = raw_path.read_bytes()
            parsed = PARSERS[source](raw, saved["url"]).to_dict()

            counts[source] += 1
            self.assertEqual(
                len(soup(raw).select(EXPECTED_SELECTORS[source])), 1, saved["url"]
            )
            self.assertIsNone(parsed["parse_error"], saved["url"])
            self.assertEqual(
                parsed["metadata"]["body_selector"],
                EXPECTED_SELECTORS[source],
                saved["url"],
            )
            self.assertGreater(len(parsed["text"].split()), 100, saved["url"])
            self.assertIn(parsed["category"], {"news", "article", "review"})

        self.assertEqual(counts, EXPECTED_COUNTS)

    def test_source_specific_categories_and_taxonomy(self) -> None:
        documents = saved_documents()
        by_source: dict[str, list[dict]] = {}
        for document in documents:
            by_source.setdefault(document["source"], []).append(document)

        self.assertEqual(
            {item["category"] for item in by_source["playground"]},
            {"news", "review"},
        )
        self.assertTrue(all(item["games"] for item in by_source["playground"]))
        self.assertEqual(
            {item["category"] for item in by_source["ixbt_games"]},
            {"news", "article", "review"},
        )
        self.assertTrue(
            any("GTA 6" in item["tags"] for item in by_source["igromania"])
        )
        self.assertTrue(any(item["games"] for item in by_source["stopgame"]))


if __name__ == "__main__":
    unittest.main()
