from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_lab3 import run_checks, test_commands


class Lab3RunnerTests(unittest.TestCase):
    def test_docker_targets_only_the_cpp_service(self):
        self.assertEqual(test_commands("docker", Path("unused")), [[
            "docker", "compose", "run", "--rm", "-T", "--no-deps", "tokenizer-tests",
        ]])

    def test_native_always_reconfigures_and_rebuilds_before_ctest(self):
        commands = test_commands("native", Path("build/native-tests"))
        self.assertEqual([command[0] for command in commands], ["cmake", "cmake", "ctest"])
        self.assertIn("--build", commands[1])

    def test_combined_log_and_counts_include_both_languages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("run_lab3.subprocess.run", side_effect=[
                SimpleNamespace(returncode=0, stdout=b"100% tests passed, 0 tests failed out of 28\n"),
                SimpleNamespace(returncode=0, stdout=b"Ran 9 tests in 0.05s\nOK\n"),
            ]), redirect_stdout(io.StringIO()):
                self.assertEqual(run_checks(root, "docker", root / "build"), 0)
            self.assertIn("{37}", (root / "tests_summary.tex").read_text())
            log = (root / "tests.txt").read_text()
            self.assertIn("COMMAND: docker", log)
            self.assertIn("tests.test_corpus_export", log)
            self.assertNotIn("tests.test_tokenizer", log)

    def test_failed_native_tests_stop_the_pipeline_and_keep_the_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("run_lab3.subprocess.run", return_value=SimpleNamespace(
                returncode=8, stdout=b"FAILED: native test\n"
            )) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(run_checks(root, "docker", root / "build"), 8)
            self.assertEqual(run.call_count, 1)
            self.assertFalse((root / "tests_summary.tex").exists())
            self.assertIn("FAILED", (root / "tests.txt").read_text())


if __name__ == "__main__":
    unittest.main()
