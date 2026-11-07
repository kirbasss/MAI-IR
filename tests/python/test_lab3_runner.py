from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_lab3 import run_checks, test_commands
from run_tests import PYTHON_SUITES, ROOT, cpp_command, python_modules


class Lab3RunnerTests(unittest.TestCase):
    def test_docker_targets_only_the_cpp_service(self):
        commands = test_commands("docker", Path("unused"))
        self.assertEqual(commands, [cpp_command("3")])
        self.assertIn("--no-deps", commands[0])
        self.assertIn("-L lab3", commands[0][-1])
        self.assertNotIn("lab4", commands[0][-1])

    def test_every_python_module_belongs_to_exactly_one_lab(self):
        assigned = [name for group in PYTHON_SUITES.values() for name in group]
        existing = {path.stem for path in (ROOT / "tests" / "python").glob("test_*.py")}
        self.assertEqual(set(assigned), existing)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(python_modules("4"), ["tests.python.test_lemma_dictionary", "tests.python.test_lemma_evaluation"])

    def test_cpp_groups_include_process_checks_and_fail_if_empty(self):
        for lab in ("3", "4"):
            script = cpp_command(lab)[-1]
            self.assertIn("cmake --build", script)
            self.assertIn("--no-tests=error", script)
            self.assertIn("-L lab" + lab, script)
        self.assertNotIn(" -L ", cpp_command("all")[-1])
        with self.assertRaises(ValueError):
            cpp_command("1")

    def test_native_always_reconfigures_and_rebuilds_before_ctest(self):
        commands = test_commands("native", Path("build/native-tests"))
        self.assertEqual([command[0] for command in commands], ["cmake", "cmake", "ctest"])
        self.assertIn("--build", commands[1])
        self.assertEqual(commands[-1][-2:], ["-L", "lab3"])

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
            self.assertIn("tests.python.test_corpus_export", log)
            self.assertNotIn("tests.python.test_tokenizer", log)

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
