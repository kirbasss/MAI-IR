import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
from serve_search import NativeSearch, handler, page


class SearchWebTests(unittest.TestCase):
    def test_native_uses_utf8_stdin_and_no_shell(self):
        backend = NativeSearch(Path("native"), Path("index"), Path("dict"))
        with patch("serve_search.subprocess.run") as run:
            run.return_value.stdout = json.dumps({"total": 1, "results": []}).encode()
            run.return_value.returncode = 0
            backend.search("игры AND doom", "lemma")
            args, options = run.call_args
            self.assertIn("--dict", args[0])
            self.assertEqual(options["input"], "игры AND doom\n".encode())
            self.assertFalse(options.get("shell", False))
            for query, space in [("a\nb", "surface"), ("a\0b", "surface"), ("a", "bad")]:
                with self.assertRaises(ValueError):
                    backend.search(query, space)
            self.assertEqual(run.call_count, 1)

    def test_query_title_and_urls_cannot_inject_html(self):
        document = {"id": 5, "title": "<script>alert(1)</script>", "url": "javascript:alert(1)", "source": "<test>"}
        html = page('\"><img src=x onerror=alert(1)>', result={"total": 1, "elapsed_ms": 1, "results": [document]})
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("&lt;script&gt;", html)

    def test_http_form_utf8_result_and_query_error(self):
        class Backend:
            def search(self, query, space):
                if query == "()":
                    raise ValueError("Некорректный запрос")
                assert query == "игры" and space == "lemma"
                return {"total": 1, "elapsed_ms": 1, "results": [{"id": 12, "title": "Игры", "url": "https://example.test/game", "source": "test"}]}
        with HTTPServer(("127.0.0.1", 0), handler(Backend())) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base+"/") as response:
                    self.assertIn('<form method="get"', response.read().decode())
                from urllib.parse import urlencode
                with urlopen(base+"/?"+urlencode({"q": "игры", "space": "lemma"})) as response:
                    self.assertIn("Игры", response.read().decode())
                with self.assertRaises(HTTPError) as error:
                    urlopen(base+"/?q=%28%29")
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                worker.join()
