"""Unit tests for Brain pre-warm (ACCOUNT2_PREWARM_V1). Uses a fake local HTTP server; no llama-server, no audio."""
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_brain  # noqa: E402
from jarvis_brain import Brain  # noqa: E402


class FakeLlama:
    def __init__(self, delay=0.0):
        self.requests = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                outer.requests.append(json.loads(self.rfile.read(n).decode("utf-8")))
                if delay:
                    time.sleep(delay)
                body = json.dumps({"usage": {"prompt_tokens": 100}, "timings": {"prompt_n": 90, "cache_n": 10}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1/chat/completions" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def wait_for(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class T(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JARVIS_NO_PREWARM", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []
        self.fake = FakeLlama()

    def tearDown(self):
        self.fake.stop()
        self.tmp.cleanup()
        os.environ.pop("JARVIS_NO_PREWARM", None)

    def brain(self, resume=False):
        return Brain(memory_dir=self.tmp.name, resume=resume, log=self.logs.append)

    def test_off_by_default(self):
        b = self.brain()
        for i in range(8):
            b.commit_turn("q%d" % i, "a%d" % i)
        time.sleep(0.2)
        self.assertEqual(self.fake.requests, [])

    def test_startup_sends_system_plus_history(self):
        b = self.brain()
        b.commit_turn("first question", "first answer")
        b.enable_prewarm(self.fake.url)
        self.assertTrue(wait_for(lambda: len(self.fake.requests) == 1))
        req = self.fake.requests[0]
        self.assertEqual(req["max_tokens"], 1)
        self.assertFalse(req["stream"])
        self.assertTrue(req["cache_prompt"])
        self.assertEqual(req["messages"], b.build_messages("."))
        self.assertEqual([m["role"] for m in req["messages"]], ["system", "user", "assistant", "user"])

    def test_reset_triggers_exactly_once_with_the_kept_history(self):
        b = self.brain()
        b.enable_prewarm(self.fake.url)
        self.assertTrue(wait_for(lambda: len(self.fake.requests) == 1))
        for i in range(6):  # 12 messages: no reset yet
            b.commit_turn("q%d" % i, "a%d" % i)
        time.sleep(0.3)
        self.assertEqual(len(self.fake.requests), 1)
        b.commit_turn("q6", "a6")  # 14 > 12: reset to 4 messages
        self.assertTrue(wait_for(lambda: len(self.fake.requests) == 2))
        msgs = self.fake.requests[1]["messages"]
        self.assertEqual([m["content"] for m in msgs[1:-1]], ["q5", "a5", "q6", "a6"])
        self.assertEqual(len(self.fake.requests), 2)

    def test_remember_and_forget_trigger_only_when_the_prompt_changes(self):
        b = self.brain()
        b.enable_prewarm(self.fake.url)
        self.assertTrue(wait_for(lambda: len(self.fake.requests) == 1))
        self.assertEqual(b.try_memory_command("forget everything"), "There was nothing to forget.")
        time.sleep(0.3)
        self.assertEqual(len(self.fake.requests), 1)
        b.try_memory_command("remember that my project is called Ovid")
        self.assertTrue(wait_for(lambda: len(self.fake.requests) == 2))
        self.assertIn("my project is called Ovid", self.fake.requests[1]["messages"][0]["content"])
        b.try_memory_command("remember that my project is called Ovid")  # duplicate: prompt unchanged
        time.sleep(0.3)
        self.assertEqual(len(self.fake.requests), 2)
        b.try_memory_command("forget everything")
        self.assertTrue(wait_for(lambda: len(self.fake.requests) == 3))
        self.assertNotIn("Ovid", self.fake.requests[2]["messages"][0]["content"])

    def test_server_down_is_harmless(self):
        self.fake.stop()
        b = self.brain()
        b.enable_prewarm("http://127.0.0.1:9/v1/chat/completions")
        b._prewarm_thread.join(5)
        self.assertTrue(any("prewarm skipped" in str(m) for m in self.logs))
        b.commit_turn("q", "a")  # still works

    def test_does_not_block_the_caller(self):
        self.fake.stop()
        self.fake = FakeLlama(delay=1.5)
        b = self.brain()
        b.enable_prewarm(self.fake.url)
        t0 = time.monotonic()
        for i in range(7):
            b.commit_turn("q%d" % i, "a%d" % i)
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_env_switch_disables(self):
        os.environ["JARVIS_NO_PREWARM"] = "1"
        b = self.brain()
        b.enable_prewarm(self.fake.url)
        for i in range(8):
            b.commit_turn("q%d" % i, "a%d" % i)
        time.sleep(0.3)
        self.assertEqual(self.fake.requests, [])

    def test_done_line_reports_cache_numbers(self):
        b = self.brain()
        b.enable_prewarm(self.fake.url)
        b._prewarm_thread.join(5)
        self.assertTrue(any("prewarm done" in str(m) and "newly_read=90" in str(m) and "reused=10" in str(m)
                            for m in self.logs))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1)
    n = r.result.testsRun
    bad = len(r.result.failures) + len(r.result.errors)
    print("RESULT ALL PASS (%d/%d)" % (n - bad, n) if not bad else "RESULT FAIL (%d/%d)" % (n - bad, n))
    sys.exit(1 if bad else 0)