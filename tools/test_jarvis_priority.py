"""Unit tests for Brain.lower_llama_priority (ACCOUNT2_PRIORITY_V1). PowerShell is replaced by a mock."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_brain  # noqa: E402
from jarvis_brain import Brain  # noqa: E402


class T(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JARVIS_NO_LLAMA_PRIORITY", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []
        self.brain = Brain(memory_dir=self.tmp.name, resume=False, log=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("JARVIS_NO_LLAMA_PRIORITY", None)

    def test_windows_runs_powershell_once_and_logs_the_count(self):
        with mock.patch.object(Brain, "_is_windows", return_value=True), \
                mock.patch("jarvis_brain.subprocess.run", return_value=SimpleNamespace(stdout="1\n")) as run:
            self.brain.lower_llama_priority()
        self.assertEqual(run.call_count, 1)
        args = run.call_args[0][0]
        self.assertEqual(args[0], "powershell")
        self.assertIn("llama-server", args[-1])
        self.assertIn("BelowNormal", args[-1])
        self.assertTrue(any("BelowNormal (1 process" in str(m) for m in self.logs))

    def test_not_windows_does_nothing(self):
        with mock.patch.object(Brain, "_is_windows", return_value=False), \
                mock.patch("jarvis_brain.subprocess.run") as run:
            self.brain.lower_llama_priority()
        run.assert_not_called()

    def test_env_switch_disables(self):
        os.environ["JARVIS_NO_LLAMA_PRIORITY"] = "1"
        with mock.patch.object(Brain, "_is_windows", return_value=True), \
                mock.patch("jarvis_brain.subprocess.run") as run:
            self.brain.lower_llama_priority()
        run.assert_not_called()
        self.assertTrue(any("left unchanged" in str(m) for m in self.logs))

    def test_failure_is_logged_not_raised(self):
        with mock.patch.object(Brain, "_is_windows", return_value=True), \
                mock.patch("jarvis_brain.subprocess.run", side_effect=OSError("no powershell")):
            self.brain.lower_llama_priority()
        self.assertTrue(any("could not change llama-server priority" in str(m) for m in self.logs))

    def test_nothing_else_calls_it(self):
        with mock.patch("jarvis_brain.subprocess.run") as run:
            for i in range(8):
                self.brain.commit_turn("q%d" % i, "a%d" % i)
            self.brain.try_memory_command("remember that my project is called Ovid")
        run.assert_not_called()


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1)
    n = r.result.testsRun
    bad = len(r.result.failures) + len(r.result.errors)
    print("RESULT ALL PASS (%d/%d)" % (n - bad, n) if not bad else "RESULT FAIL (%d/%d)" % (n - bad, n))
    sys.exit(1 if bad else 0)