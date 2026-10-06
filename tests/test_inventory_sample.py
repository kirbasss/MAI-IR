from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.inventory_sample import FIELDS, sample_inventory


class InventorySampleTests(unittest.TestCase):
    def test_balanced_deterministic_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "all.tsv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
                writer.writeheader()
                for name, count in (("stopgame", 20), ("gamemag", 8)):
                    for number in range(count):
                        writer.writerow({
                            "source": name, "category": "news",
                            "url": f"https://{name}.ru/news/{number}",
                            "sitemap_url": f"https://{name}.ru/sitemap.xml",
                        })
            first, second = root / "first.tsv", root / "second.tsv"
            summary = sample_inventory(source, first, per_source=5, seed=7)
            sample_inventory(source, second, per_source=5, seed=7)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(summary["input_rows"], 28)
            self.assertEqual(summary["selected_rows"], 10)
            self.assertEqual(summary["sources"]["stopgame"]["selected"], 5)
            self.assertEqual(summary["sources"]["gamemag"]["selected"], 5)
            with first.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len({row["url"] for row in rows}), 10)

    def test_refuses_to_overwrite_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "all.tsv"
            path.write_text("source\tcategory\turl\tsitemap_url\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                sample_inventory(path, path, per_source=5)

    def test_refuses_empty_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / "all.tsv", root / "sample.tsv"
            source.write_text("source\tcategory\turl\tsitemap_url\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                sample_inventory(source, output, per_source=5)
            self.assertFalse(output.exists())
