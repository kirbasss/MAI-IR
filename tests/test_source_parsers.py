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
    "gamemag": "div.content-text",
    "gamingonlinux": "article.h-entry .e-content",
    "pcgamer": "div#article-body",
    "eurogamer": "div.article_body_content",
}
EXPECTED_COUNTS = {
    "playground": 3,
    "stopgame": 5,
    "ixbt_games": 4,
    "igromania": 3,
    "gamemag": 3,
    "gamingonlinux": 2,
    "pcgamer": 2,
    "eurogamer": 2,
}


def saved_documents() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(PARSED_ROOT.rglob("*.json"))
    ]


class SourceParserFixtureTests(unittest.TestCase):
    def test_new_sources_use_validated_source_selectors(self) -> None:
        html = b"""
        <html><head><title>Example</title></head><body>
          <div class="content-text">GameMAG article.</div>
          <article class="h-entry"><div class="e-content">GamingOnLinux article.</div></article>
          <div id="article-body">PC Gamer article.</div>
          <div class="article_body_content">Eurogamer article.</div>
        </body></html>
        """
        for source, selector in {
            "gamemag": "div.content-text",
            "gamingonlinux": "article.h-entry .e-content",
            "pcgamer": "div#article-body",
            "eurogamer": "div.article_body_content",
        }.items():
            parsed = PARSERS[source](html, f"https://example.org/{source}").to_dict()
            self.assertIsNone(parsed["parse_error"])
            self.assertEqual(parsed["metadata"]["body_selector"], selector)
            self.assertNotIn("parser_mode", parsed["metadata"])

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
            selector_count = len(soup(raw).select(EXPECTED_SELECTORS[source]))
            self.assertGreaterEqual(selector_count, 1, saved["url"])
            if source != "gamemag":
                self.assertEqual(selector_count, 1, saved["url"])
            self.assertIsNone(parsed["parse_error"], saved["url"])
            self.assertEqual(
                parsed["metadata"]["body_selector"],
                EXPECTED_SELECTORS[source],
                saved["url"],
            )
            self.assertNotIn("parser_mode", parsed["metadata"], saved["url"])
            self.assertGreater(len(parsed["text"].split()), 30, saved["url"])
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

        reparsed: dict[str, list[dict]] = {}
        for saved in documents:
            if saved["source"] not in {"gamemag", "gamingonlinux", "pcgamer", "eurogamer"}:
                continue
            raw_path = ROOT / Path(saved["raw_file"].replace("\\", "/"))
            parsed = PARSERS[saved["source"]](raw_path.read_bytes(), saved["url"]).to_dict()
            reparsed.setdefault(saved["source"], []).append(parsed)

        self.assertEqual(
            {item["category"] for item in reparsed["gamemag"]},
            {"news", "article", "review"},
        )
        self.assertEqual({item["category"] for item in reparsed["gamingonlinux"]}, {"news"})
        self.assertEqual({item["category"] for item in reparsed["pcgamer"]}, {"news", "review"})
        self.assertEqual({item["category"] for item in reparsed["eurogamer"]}, {"article"})
        self.assertTrue(all(item["tags"] for item in reparsed["gamingonlinux"]))
        self.assertTrue(all(item["author"] for item in reparsed["pcgamer"]))
        self.assertTrue(all(
            "copy link" not in item["text"].casefold()
            and "share this article" not in item["text"].casefold()
            for item in reparsed["pcgamer"]
        ))
        self.assertEqual(
            max(item["metadata"].get("body_blocks", 0) for item in reparsed["gamemag"]),
            4,
        )


if __name__ == "__main__":
    unittest.main()
