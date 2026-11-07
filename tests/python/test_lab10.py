from pathlib import Path
from unittest.mock import patch
import unittest
from serve_search import NativeSearch


class PhraseWebTests(unittest.TestCase):
    def test_coordinates_and_quoted_query_reach_native_without_shell(self):
        backend = NativeSearch(Path("native"), Path("index"), Path("dict"), Path("positions"))
        with patch("serve_search.subprocess.run") as run:
            run.return_value.stdout = b'{"total":3,"results":[]}'
            run.return_value.returncode = 0
            backend.search('"doom eternal" AND NOT quake', "surface")
            args, kwargs = run.call_args
            self.assertIn("--positions", args[0])
            self.assertEqual(args[0][args[0].index("--positions")+1], "positions")
            self.assertEqual(kwargs["input"], b'"doom eternal" AND NOT quake\n')
            self.assertFalse(kwargs.get("shell", False))

    def test_native_phrase_errors_remain_visible_to_form(self):
        backend = NativeSearch(Path("native"), Path("index"), None)
        with patch("serve_search.subprocess.run") as run:
            run.return_value.stdout = b'{"error":"Phrase search requires --positions DIR"}'
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, "--positions"):
                backend.search('"doom eternal"', "surface")
