"""Unit tests for Brain.wait_for_prewarm (ACCOUNT2_PREWARM_V2). Fake local server; no llama-server."""
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
from jarvis_brain import Brain  # noqa: E402


class SlowServer:
    def __init__(self, delay):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                time.sleep(delay)
                body = json.dumps({"usage": {"prompt_tokens": 5}, "timings": {"prompt_n": 5, "cache_n": 0}}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/x" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class T(unittest.TestCase):
    def setUp(self):
        os.environ.pop("JARVIS_NO_PREWARM", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def brain(self):
        return Brain(memory_dir=self.tmp.name, resume=False, log=self.logs.append)

    def test_returns_at_once_when_prewarm_was_never_enabled(self):
        b = self.brain()
        t0 = time.monotonic()
        b.wait_for_prewarm()
        self.assertLess(time.monotonic() - t0, 0.2)
        self.assertFalse(any("warming up" in str(m) for m in self.logs))

    def test_waits_for_the_prewarm_and_says_so(self):
        srv = SlowServer(0.6)
        try:
            b = self.brain()
            b.enable_prewarm(srv.url)
            t0 = time.monotonic()
            b.wait_for_prewarm()
            self.assertGreaterEqual(time.monotonic() - t0, 0.4)
            self.assertFalse(b._prewarm_thread.is_alive())
            self.assertTrue(any("warming up" in str(m) for m in self.logs))
            self.assertTrue(any("prewarm done" in str(m) for m in self.logs))
        finally:
            srv.stop()

    def test_gives_up_after_the_timeout(self):
        srv = SlowServer(3.0)
        try:
            b = self.brain()
            b.enable_prewarm(srv.url)
            t0 = time.monotonic()
            b.wait_for_prewarm(timeout=0.5)
            self.assertLess(time.monotonic() - t0, 1.5)
        finally:
            srv.stop()

    def test_server_down_does_not_hang(self):
        b = self.brain()
        b.enable_prewarm("http://127.0.0.1:9/x")
        t0 = time.monotonic()
        b.wait_for_prewarm()
        self.assertLess(time.monotonic() - t0, 5.0)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1)
    n = r.result.testsRun
    bad = len(r.result.failures) + len(r.result.errors)
    print("RESULT ALL PASS (%d/%d)" % (n - bad, n) if not bad else "RESULT FAIL (%d/%d)" % (n - bad, n))
    sys.exit(1 if bad else 0)