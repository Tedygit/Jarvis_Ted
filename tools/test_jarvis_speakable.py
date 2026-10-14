"""Unit tests for jarvis_speakable.speakable (no audio, no network)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_speakable import speakable  # noqa: E402


class T(unittest.TestCase):
    def test_plain_unchanged(self):
        self.assertEqual(speakable("Your project is called Ovid."), "Your project is called Ovid.")

    def test_numbered_bold_item(self):
        self.assertEqual(speakable("1. **List**: An ordered collection."), "List: An ordered collection.")

    def test_bold_and_italic(self):
        self.assertEqual(speakable("This is **very** important and *quite* useful."),
                         "This is very important and quite useful.")

    def test_backticks_keep_content(self):
        self.assertEqual(speakable("Run `git status` first."), "Run git status first.")

    def test_fence_marker_removed(self):
        self.assertEqual(speakable("```python"), "")

    def test_heading_and_bullet(self):
        self.assertEqual(speakable("## Summary"), "Summary")
        self.assertEqual(speakable("- first point"), "first point")

    def test_link_keeps_label(self):
        self.assertEqual(speakable("See [the docs](https://example.com/x) now."), "See the docs now.")

    def test_snake_case_and_math_untouched(self):
        self.assertEqual(speakable("Use my_var_name here."), "Use my_var_name here.")
        self.assertEqual(speakable("2 * 3 is six."), "2 * 3 is six.")

    def test_dunder_name_untouched(self):
        self.assertEqual(speakable("__init__ is special."), "__init__ is special.")

    def test_only_symbols_is_empty(self):
        self.assertEqual(speakable("---"), "")
        self.assertEqual(speakable("**"), "")
        self.assertEqual(speakable(""), "")

    def test_multiline_joined(self):
        self.assertEqual(speakable("1. **One**: a\n2. **Two**: b"), "One: a Two: b")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1)
    n = r.result.testsRun
    bad = len(r.result.failures) + len(r.result.errors)
    print("RESULT ALL PASS (%d/%d)" % (n - bad, n) if not bad else "RESULT FAIL (%d/%d)" % (n - bad, n))
    sys.exit(1 if bad else 0)