import json
from pathlib import Path
import unittest
from unittest.mock import patch

from serve_search import NativeSearch, page, render_snippet


class SnippetWebTests(unittest.TestCase):
    def test_native_forwards_corpus_and_offsets(self):
        backend = NativeSearch(Path("native"), Path("index"), None,
                               corpus=Path("corpus.bin"), snippet_index=Path("offsets.bin"))
        with patch("serve_search.subprocess.run") as run:
            run.return_value.stdout = json.dumps({"total": 0, "results": []}).encode()
            run.return_value.returncode = 0
            backend.search("doom", "surface")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--corpus") + 1], "corpus.bin")
            self.assertEqual(command[command.index("--snippet-index") + 1], "offsets.bin")

    def test_utf8_spans_are_highlighted_and_html_is_escaped(self):
        snippet = "Ёж <script>doom</script>"
        begin = len("Ёж <script>".encode())
        document = {"id": 1, "title": "Title", "url": "", "source": "test",
                    "snippet": snippet, "snippet_matches": [[begin, begin + 4]],
                    "snippet_left": True, "snippet_right": False}
        html = page("doom", result={"total": 1, "elapsed_ms": 1, "results": [document]})
        self.assertIn("&lt;script&gt;<mark>doom</mark>&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("… Ёж", html)

    def test_invalid_span_is_displayed_as_plain_escaped_text(self):
        html = render_snippet({"snippet": "Ёж <b>", "snippet_matches": [[1, 4]]})
        self.assertIn("Ёж &lt;b&gt;", html)
        self.assertNotIn("<mark>", html)


if __name__ == "__main__":
    unittest.main()
