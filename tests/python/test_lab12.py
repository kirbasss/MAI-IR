from pathlib import Path
from unittest.mock import patch
import unittest

from serve_search import NativeSearch
from run_tests import cpp_command, python_modules


class SkipWebTests(unittest.TestCase):
    def test_packed_index_and_skips_reach_native_search_with_utf8_stdin(self):
        backend = NativeSearch(Path("native"), Path("index"), Path("dict"), Path("positions"), Path("compact"), Path("skips"))
        with patch("serve_search.subprocess.run") as run:
            run.return_value.stdout = b'{"total":3,"results":[]}'
            run.return_value.returncode = 0
            backend.search('"god of war" AND NOT игры', "lemma")
            args, options = run.call_args
            for flag, value in (("--skips", "skips"), ("--compact", "compact"), ("--positions", "positions"), ("--dict", "dict")):
                self.assertEqual(args[0][args[0].index(flag)+1], value)
            self.assertEqual(options["input"], '"god of war" AND NOT игры\n'.encode("utf-8"))
            self.assertFalse(options.get("shell", False))

    def test_lab12_runner_selects_native_label_and_python_web_test(self):
        self.assertIn("-L lab12", cpp_command("12")[-1])
        self.assertEqual(python_modules("12"), ["tests.python.test_lab12"])
