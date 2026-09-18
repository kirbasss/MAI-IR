from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.downloader import parse_saved_html
from src.statistics import build_statistics
from src.utils import canonicalize_url


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_1 = ROOT / "data/raw/stopgame/sample_1.html"
SAMPLE_2 = ROOT / "data/raw/stopgame/sample_2.html"
URL_1 = (
    "https://stopgame.ru/newsdata/19062/"
    "rockstar_o_planah_na_buduschee_i_video_o_krasotah_gta_5"
)
URL_2 = (
    "https://stopgame.ru/newsdata/68145/"
    "boss_take_two_obsudil_cennik_gta_vi_i_poobeschal_ne_otmenyat_bioshock_4_i_drugoe_iz_otcheta"
)


class Lab1Tests(unittest.TestCase):
    def test_stopgame_parser_extracts_only_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = parse_saved_html(
                "stopgame", URL_2, SAMPLE_2, Path(directory) / "data"
            )
        self.assertIsNone(document["parse_error"])
        self.assertEqual(document["metadata"]["body_selector"], "article#material_content")
        self.assertEqual(document["author"], "Лина Скорич")
        self.assertIn("BioShock 4", document["text"])
        self.assertNotIn("Предыдущий отчёт", document["text"])
        self.assertNotIn("Читай также", document["text"])
        self.assertIn("Grand Theft Auto VI", document["games"])

    def test_statistics_for_saved_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            parse_saved_html("stopgame", URL_1, SAMPLE_1, data_dir)
            parse_saved_html("stopgame", URL_2, SAMPLE_2, data_dir)
            result = build_statistics(data_dir / "parsed", Path(directory) / "results")
        self.assertEqual(result["all"]["documents"], 2)
        self.assertEqual(result["all"]["parsed_ok"], 2)
        self.assertEqual(result["all"]["parse_errors"], 0)
        self.assertEqual(result["all"]["unique_texts"], 2)
        self.assertEqual(result["all"]["duplicate_documents"], 0)

    def test_url_canonicalisation_removes_only_tracking_data(self) -> None:
        self.assertEqual(
            canonicalize_url("https://ixbt.games/news/a.html?utm_source=x&page=2#top"),
            "https://ixbt.games/news/a.html?page=2",
        )


if __name__ == "__main__":
    unittest.main()
