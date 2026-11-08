import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer
from urllib.request import urlopen
from urllib.parse import urlencode
from serve_search import NativeSearch, handler, page


class TfidfWebTests(unittest.TestCase):
    def test_native_passes_tfidf_and_norms_with_utf8(self):
        backend = NativeSearch(Path("native"), Path("index"), None, Path("positions"), norms=Path("norms"))
        with patch("serve_search.subprocess.run") as run:
            run.return_value.stdout = json.dumps({"total": 0, "results": []}).encode()
            run.return_value.returncode = 0
            backend.search("игры doom", "surface", "tfidf")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--ranking")+1], "tfidf")
            self.assertEqual(command[command.index("--norms")+1], "norms")
            self.assertEqual(run.call_args.kwargs["input"], "игры doom\n".encode())
        with self.assertRaises(ValueError):
            backend.search("doom", "surface", "bad")
        with self.assertRaises(ValueError):
            NativeSearch(Path("native"), Path("index"), None).search("doom", "surface", "tfidf")

    def test_ranked_page_shows_scores_and_correct_order(self):
        html = page("doom eternal", result={"total": 1, "elapsed_ms": 2, "ranking": "tfidf",
            "results": [{"id": 8, "title": "Doom <test>", "source": "test", "url": "https://example.test", "score": .4567}]}, ranking="tfidf")
        self.assertIn("TF-IDF по убыванию", html)
        self.assertIn("0.45670", html)
        self.assertIn("Doom &lt;test&gt;", html)
        self.assertIn('value="tfidf" selected', html)
        self.assertNotIn("ID по возрастанию", html)

    def test_http_tfidf_selection_reaches_backend(self):
        class Backend:
            def search(self, query, space, ranking):
                assert (query, space, ranking) == ("игры doom", "surface", "tfidf")
                return {"total": 0, "elapsed_ms": 1, "ranking": ranking, "results": []}
        with HTTPServer(("127.0.0.1", 0), handler(Backend())) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/?" + urlencode({"q": "игры doom", "ranking": "tfidf"})
                with urlopen(url) as response:
                    self.assertIn("TF-IDF по убыванию", response.read().decode())
            finally:
                server.shutdown()
                worker.join()
