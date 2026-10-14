"""test_jarvis_tools.py - unit tests for tools/jarvis_tools.py (read-only coding tools).
Run: python tools/test_jarvis_tools.py   (exit code 0 = all pass; git tests are skipped if git is missing)"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_tools import CodeTools, ToolError, try_tool_command, MAX_OUT_CHARS

HAVE_GIT = shutil.which("git") is not None


class ToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = Path(cls.tmp.name)
        (r / "src").mkdir()
        (r / ".venv").mkdir()
        (r / "main.py").write_text("print('hello')\n# TODO fix\n", encoding="utf-8")
        (r / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (r / ".venv" / "skip.py").write_text("TODO hidden\n", encoding="utf-8")
        (r / "blob.bin").write_bytes(b"\x00\x01\x02TODO")
        (r / "big.txt").write_text("line\n" * 5000, encoding="utf-8")
        cls.root = r
        cls.t = CodeTools(r)
        if HAVE_GIT:
            def g(*a):
                subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
            g("init", "-q")
            g("config", "user.email", "t@example.com")
            g("config", "user.name", "T")
            g("add", "main.py", "src/util.py")
            g("commit", "-q", "-m", "first commit")
            (r / "main.py").write_text("print('hello')\n# TODO fix\nprint('changed')\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_list_dir_orders_dirs_first_and_skips_noise(self):
        out = self.t.list_dir()
        self.assertIn("[dir] src", out)
        self.assertNotIn(".venv", out)
        self.assertLess(out.index("[dir] src"), out.index("main.py"))

    def test_read_file_numbers_and_bounds(self):
        out = self.t.read_file("src/util.py", 2, 5)
        self.assertIn("lines 2-2 of 2", out)
        self.assertIn("2:     return a + b", out)
        with self.assertRaises(ToolError):
            self.t.read_file("src/util.py", 99)

    def test_outside_root_refused(self):
        for bad in ("..", "../x", "src/../../x"):
            with self.assertRaises(ToolError):
                self.t.read_file(bad)

    def test_binary_and_large_refused_or_clipped(self):
        with self.assertRaises(ToolError):
            self.t.read_file("blob.bin")
        out = self.t.read_file("big.txt", 1, 4000)
        self.assertLessEqual(len(out), MAX_OUT_CHARS + 60)
        self.assertIn("more characters cut", out)

    def test_search_finds_and_skips(self):
        out = self.t.search("todo")
        self.assertIn("main.py:2:", out)
        self.assertNotIn("skip.py", out)
        self.assertNotIn("blob.bin", out)
        self.assertIn("no matches", self.t.search("zzzqqq"))
        self.assertIn("util.py", self.t.search("return", glob="*.py"))

    @unittest.skipUnless(HAVE_GIT, "git not installed")
    def test_git_status_diff_log(self):
        self.assertIn("main.py", self.t.git_status())
        self.assertIn("main.py", self.t.git_diff())
        self.assertIn("+print('changed')", self.t.git_diff("main.py"))
        self.assertIn("first commit", self.t.git_log())

    def test_voice_commands_route_and_ignore_chat(self):
        self.assertIn("main.py", try_tool_command(self.t, "Please list files."))
        self.assertIn("return a + b", try_tool_command(self.t, "read file src/util.py from line 2"))
        self.assertIn("main.py:2:", try_tool_command(self.t, "Search the code for TODO"))
        self.assertIsNone(try_tool_command(self.t, "What is a hash function?"))
        self.assertIsNone(try_tool_command(self.t, "I would like to read the file later"))
        self.assertTrue(try_tool_command(self.t, "read file ../secret.txt").startswith("Tool problem:"))

    @unittest.skipUnless(HAVE_GIT, "git not installed")
    def test_voice_git_commands(self):
        self.assertIn("main.py", try_tool_command(self.t, "git status"))
        self.assertIn("first commit", try_tool_command(self.t, "git log"))


if __name__ == "__main__":
    res = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ToolsTests))
    bad = len(res.failures) + len(res.errors)
    print(("RESULT ALL PASS (%d/%d, %d skipped)" % (res.testsRun - len(res.skipped), res.testsRun, len(res.skipped))) if bad == 0
          else "RESULT FAIL (%d failed of %d)" % (bad, res.testsRun))
    sys.exit(0 if bad == 0 else 1)