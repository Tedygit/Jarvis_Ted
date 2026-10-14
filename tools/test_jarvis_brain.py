"""test_jarvis_brain.py - Account 2 regression test for tools\\jarvis_brain.py (ACCOUNT2_BRAIN_V1).
No audio, no HTTP, no real memory directory: every test uses a temporary folder.
Run: C:\\Jarvis_Ted\\.venv\\Scripts\\python.exe C:\\Jarvis_Ted\\tools\\test_jarvis_brain.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_brain as jb  # noqa: E402


def quiet(_msg):
    pass


def new_brain(d, resume=True):
    return jb.Brain(memory_dir=d, resume=resume, log=quiet)


class FinishReplyTests(unittest.TestCase):
    def test_stop_reason_untouched(self):
        self.assertEqual(jb.finish_reply("Hello there. And more", "stop"), ("Hello there. And more", False))

    def test_length_trims_to_last_sentence(self):
        self.assertEqual(jb.finish_reply("First one. Second one. Third is cut", "length"),
                         ("First one. Second one.", True))

    def test_length_complete_sentence_not_trimmed(self):
        self.assertEqual(jb.finish_reply("All done here.", "length"), ("All done here.", False))

    def test_length_no_sentence_keeps_text(self):
        self.assertEqual(jb.finish_reply("no terminator at all", "length"), ("no terminator at all", False))

    def test_question_mark_and_quote(self):
        self.assertEqual(jb.finish_reply('He said "ok?" Then it stopped mid', "length"),
                         ('He said "ok?"', True))

    def test_imitated_interrupt_tag_removed(self):
        self.assertEqual(jb.finish_reply("Sure thing. [interrupted]", "stop"), ("Sure thing.", False))

    def test_none_reason(self):
        self.assertEqual(jb.finish_reply("Hi.", None), ("Hi.", False))


class BuildMessagesTests(unittest.TestCase):
    def test_no_facts_no_history(self):
        with tempfile.TemporaryDirectory() as d:
            msgs = new_brain(d).build_messages("hello")
            self.assertEqual([m["role"] for m in msgs], ["system", "user"])
            self.assertEqual(msgs[0]["content"], jb.BASE_SYSTEM_PROMPT)
            self.assertEqual(msgs[1]["content"], "hello")

    def test_history_order_and_copy(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.commit_turn("q1", "a1")
            msgs = b.build_messages("q2")
            self.assertEqual([m["content"] for m in msgs[1:]], ["q1", "a1", "q2"])
            msgs[1]["content"] = "mutated"
            self.assertEqual(b.history[0]["content"], "q1")

    def test_facts_in_system_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.try_memory_command("Remember that my project is called Ovid.")
            sysmsg = b.build_messages("hi")[0]["content"]
            self.assertTrue(sysmsg.startswith(jb.BASE_SYSTEM_PROMPT))
            self.assertIn("- my project is called Ovid", sysmsg)


class HistoryTests(unittest.TestCase):
    def test_batch_reset_keeps_two_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            for i in range(1, 7):
                b.commit_turn(f"q{i}", f"a{i}")
            self.assertEqual(len(b.history), 12)
            b.commit_turn("q7", "a7")
            self.assertEqual([m["content"] for m in b.history], ["q6", "a6", "q7", "a7"])
            self.assertEqual(b.history[0]["role"], "user")

    def test_interrupted_marker_in_history_and_log(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.commit_turn("q", "a long answer", interrupted=True)
            self.assertEqual(b.history[1]["content"], "a long answer" + jb.INTERRUPT_TAG)
            rec = json.loads(Path(d, "conversation.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertTrue(rec["interrupted"])
            self.assertEqual(rec["assistant"], "a long answer")


class PersistenceTests(unittest.TestCase):
    def test_resume_last_two_turns(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            for i in range(1, 5):
                b.commit_turn(f"q{i}", f"a{i}")
            b2 = new_brain(d)
            self.assertEqual([m["content"] for m in b2.history], ["q3", "a3", "q4", "a4"])

    def test_resume_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            new_brain(d).commit_turn("q", "a")
            self.assertEqual(new_brain(d, resume=False).history, [])

    def test_resume_keeps_interrupted_marker(self):
        with tempfile.TemporaryDirectory() as d:
            new_brain(d).commit_turn("q", "a", interrupted=True)
            self.assertTrue(new_brain(d).history[1]["content"].endswith(jb.INTERRUPT_TAG))

    def test_corrupt_log_lines_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            new_brain(d).commit_turn("q1", "a1")
            with open(Path(d, "conversation.jsonl"), "a", encoding="utf-8") as fh:
                fh.write("{not json\n")
            b = new_brain(d)
            self.assertEqual([m["content"] for m in b.history], ["q1", "a1"])

    def test_corrupt_facts_file_survives(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "facts.json").write_text("][ broken", encoding="utf-8")
            b = new_brain(d)
            self.assertEqual(b.facts, [])
            self.assertEqual(Path(d, "facts.json").read_text(encoding="utf-8"), "][ broken")

    def test_facts_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            new_brain(d).try_memory_command("remember that I prefer Flutter")
            self.assertEqual([f["text"] for f in new_brain(d).facts], ["I prefer Flutter"])

    def test_unicode_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            new_brain(d).commit_turn("ሰላም", "ሰላም ነው።")
            self.assertEqual(new_brain(d).history[0]["content"], "ሰላም")


class MemoryCommandTests(unittest.TestCase):
    def test_remember_variants(self):
        cases = {
            "Remember that I work at Quadra.": "I work at Quadra",
            "remember my laptop has 16 gigabytes of RAM": "my laptop has 16 gigabytes of RAM",
            "Please remember that the server port is 8081": "the server port is 8081",
            "Jarvis, remember that I like short answers!": "I like short answers",
            "Hey Jarvis please remember: tests live in the tools folder": "tests live in the tools folder",
        }
        for spoken, expected in cases.items():
            with tempfile.TemporaryDirectory() as d:
                b = new_brain(d)
                self.assertEqual(b.try_memory_command(spoken), "Noted. I will remember that.", spoken)
                self.assertEqual([f["text"] for f in b.facts], [expected], spoken)

    def test_not_a_command(self):
        for spoken in ["Do you remember what a hash function is?",
                       "I am trying to remember the name of that library.",
                       "Can you remember that for me?",
                       "Remember that.", "remember it", "remember", "What is a hash function?"]:
            with tempfile.TemporaryDirectory() as d:
                b = new_brain(d)
                self.assertIsNone(b.try_memory_command(spoken), spoken)
                self.assertEqual(b.facts, [], spoken)

    def test_dedupe_and_cap(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.try_memory_command("remember that alpha one")
            b.try_memory_command("Remember that ALPHA ONE.")
            self.assertEqual(len(b.facts), 1)
            for i in range(10):
                b.try_memory_command(f"remember that fact number {i}")
            self.assertEqual(len(b.facts), jb.MAX_FACTS)
            self.assertEqual(b.facts[-1]["text"], "fact number 9")

    def test_fact_length_cap(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.try_memory_command("remember that " + "x" * 400)
            self.assertEqual(len(b.facts[0]["text"]), jb.MAX_FACT_CHARS)

    def test_forget_all(self):
        for spoken in ["Forget everything.", "forget everything you remember", "Jarvis forget all",
                       "Please forget everything you know!"]:
            with tempfile.TemporaryDirectory() as d:
                b = new_brain(d)
                b.try_memory_command("remember that something")
                self.assertTrue(b.try_memory_command(spoken).startswith("Done."), spoken)
                self.assertEqual(b.facts, [])
                self.assertEqual(new_brain(d).facts, [])
                self.assertEqual(b.try_memory_command(spoken), "There was nothing to forget.")

    def test_forget_is_not_triggered_by_other_sentences(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.try_memory_command("remember that something")
            self.assertIsNone(b.try_memory_command("Please don't forget everything is on the disk"))
            self.assertIsNone(b.try_memory_command("forget the last thing"))
            self.assertEqual(len(b.facts), 1)

    def test_log_records_commands(self):
        with tempfile.TemporaryDirectory() as d:
            b = new_brain(d)
            b.try_memory_command("remember that abc def")
            b.try_memory_command("forget everything")
            kinds = [json.loads(x)["kind"] for x in Path(d, "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(kinds, ["remember", "forget_all"])
            # commands must not be replayed as conversation turns
            self.assertEqual(new_brain(d).history, [])


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    total = result.testsRun
    bad = len(result.failures) + len(result.errors)
    print(f"RESULT {'ALL PASS' if bad == 0 else 'FAIL'} ({total - bad}/{total})")
    sys.exit(0 if bad == 0 else 1)