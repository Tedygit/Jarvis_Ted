"""test_jarvis_stream.py - Account 2 regression test for tools\\jarvis_stream.py (ACCOUNT2_STREAM_V1).
Pure text tests: no audio, no servers.
Run: C:\\Jarvis_Ted\\.venv\\Scripts\\python.exe C:\\Jarvis_Ted\\tools\\test_jarvis_stream.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_stream import SentenceSplitter, parse_sse_line  # noqa: E402


def sse(content=None, finish=None):
    delta = {} if content is None else {"content": content}
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n"


def run(pieces, reason="stop", min_chars=24):
    sp = SentenceSplitter(min_chars)
    out = []
    for p in pieces:
        out += sp.feed(p)
    out += sp.finish(reason)
    return out, sp


class ParseTests(unittest.TestCase):
    def test_delta(self):
        self.assertEqual(parse_sse_line(sse("Hello")), ("Hello", None, False))

    def test_finish_chunk(self):
        self.assertEqual(parse_sse_line(sse(None, "length")), ("", "length", False))

    def test_null_content(self):
        self.assertEqual(parse_sse_line('data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n'),
                         ("", None, False))

    def test_done(self):
        self.assertEqual(parse_sse_line("data: [DONE]\n"), ("", None, True))

    def test_non_data_and_bad_lines(self):
        for line in ["", "\n", ": keep-alive\n", "event: x\n", "data: {broken\n", 'data: {"choices":[]}\n',
                     '{"error":{"message":"no slot"}}\n']:
            self.assertEqual(parse_sse_line(line), ("", None, False), line)

    def test_unicode(self):
        self.assertEqual(parse_sse_line(sse("ሰላም"))[0], "ሰላም")


class SplitterTests(unittest.TestCase):
    def test_two_sentences_streamed_in_tokens(self):
        text = "A hash map stores pairs by key. Collisions are handled by chaining."
        out, _ = run([text[i:i + 3] for i in range(0, len(text), 3)])
        self.assertEqual(out, ["A hash map stores pairs by key.", "Collisions are handled by chaining."])

    def test_first_sentence_released_before_the_rest_arrives(self):
        sp = SentenceSplitter()
        self.assertEqual(sp.feed("A hash map stores pairs by key."), [])      # no whitespace yet: not final
        self.assertEqual(sp.feed(" Collisions"), ["A hash map stores pairs by key."])

    def test_short_sentence_joined_to_next(self):
        out, _ = run(["Sure. A hash map stores pairs by key."])
        self.assertEqual(out, ["Sure. A hash map stores pairs by key."])

    def test_abbreviation_not_split(self):
        out, _ = run(["Use e.g. a dictionary to store the pairs here."])
        self.assertEqual(out, ["Use e.g. a dictionary to store the pairs here."])

    def test_decimal_and_code_not_split(self):
        out, _ = run(["Call dict.get() with version 3.14 for the lookup."])
        self.assertEqual(out, ["Call dict.get() with version 3.14 for the lookup."])

    def test_newline_is_a_boundary(self):
        out, _ = run(["First line of the answer is here\nSecond line of the answer is here"])
        self.assertEqual(out, ["First line of the answer is here", "Second line of the answer is here"])

    def test_question_and_exclamation(self):
        out, _ = run(["Is that what you wanted to ask? I can explain it further!"], min_chars=10)
        self.assertEqual(out, ["Is that what you wanted to ask?", "I can explain it further!"])

    def test_length_drops_unfinished_fragment(self):
        out, sp = run(["First full sentence is here. Second one is cut of"], reason="length")
        self.assertEqual(out, ["First full sentence is here."])
        self.assertEqual(sp.dropped, "Second one is cut of")

    def test_length_with_nothing_emitted_keeps_fragment(self):
        out, sp = run(["only a fragment without an end"], reason="length")
        self.assertEqual(out, ["only a fragment without an end"])
        self.assertEqual(sp.dropped, "")

    def test_none_reason_behaves_like_length(self):
        out, sp = run(["First full sentence is here. Second one is cut of"], reason=None)
        self.assertEqual(out, ["First full sentence is here."])

    def test_length_speaks_held_short_sentence(self):
        out, _ = run(["First full sentence is here. Yes. And a fragm"], reason="length")
        self.assertEqual(out, ["First full sentence is here.", "Yes."])

    def test_stop_flushes_remainder_without_final_space(self):
        out, _ = run(["One complete sentence right here. Last bit with no space at end"])
        self.assertEqual(out, ["One complete sentence right here.", "Last bit with no space at end"])

    def test_interrupt_marker_removed(self):
        out, _ = run(["Here is the answer you wanted. [interrupted] More text follows after it."])
        self.assertEqual(out, ["Here is the answer you wanted.", "More text follows after it."])

    def test_empty_stream(self):
        self.assertEqual(run([])[0], [])
        self.assertEqual(run(["   \n  "])[0], [])

    def test_whitespace_collapsed(self):
        out, _ = run(["Lists  are   ordered\n\n collections of items that can change."])
        self.assertEqual(out, ["Lists are ordered collections of items that can change."])

    def test_unicode_sentence(self):
        out, _ = run(["ሰላም ነው። እንዴት ነህ? ደህና ነኝ እናመሰግናለን። "], min_chars=5)
        self.assertEqual(out, ["ሰላም ነው። እንዴት ነህ?", "ደህና ነኝ እናመሰግናለን።"])


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    total = result.testsRun
    bad = len(result.failures) + len(result.errors)
    print(f"RESULT {'ALL PASS' if bad == 0 else 'FAIL'} ({total - bad}/{total})")
    sys.exit(0 if bad == 0 else 1)