"""apply_account2_prewarm.py - idempotent patcher, marker ACCOUNT2_PREWARM_V1.

Adds Brain.enable_prewarm() to tools/jarvis_brain.py and one call to it in tools/stream_asr_test.py.
Every anchor must match exactly once or nothing is written. Backups: <file>.bak_preprewarm.
Usage: python apply_account2_prewarm.py [tools_dir]   (default: the folder of this script)
"""
import py_compile
import shutil
import sys
from pathlib import Path

MARK = "ACCOUNT2_PREWARM_V1"
tools = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
brain_path = tools / "jarvis_brain.py"
app_path = tools / "stream_asr_test.py"


def load(path):
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    return raw.replace("\r\n", "\n"), crlf


def save(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def once(text, anchor, label):
    n = text.count(anchor)
    if n != 1:
        print(f"PATCH POINT NOT UNIQUE/FOUND: {label} (count {n}). Nothing was written.")
        sys.exit(2)


brain, brain_crlf = load(brain_path)
app, app_crlf = load(app_path)
if MARK in brain and MARK in app:
    print("Already patched - nothing to do.")
    sys.exit(0)
if (MARK in brain) != (MARK in app):
    print("PARTIALLY PATCHED: one file has the marker and the other does not. Nothing was written.")
    sys.exit(3)

# ---- anchors in jarvis_brain.py -------------------------------------------------------------
A_IMPORT = "import json\nimport re\nimport threading\nimport time\nfrom pathlib import Path\n"
A_CONST = 'INTERRUPT_TAG = " [interrupted]"\n'
A_INIT = '        self.session_id = time.strftime("%Y%m%d-%H%M%S")\n'
A_COMMIT = ('            if len(self.history) > MAX_HISTORY_TURNS * 2:\n'
            '                self.history[:] = self.history[-RESET_KEEP_TURNS * 2:]\n'
            '            self._append_log({"kind": "turn", "user": user_text, "assistant": reply,\n'
            '                              "interrupted": bool(interrupted)})\n')
A_FORGET = '                self._append_log({"kind": "forget_all", "count": had})\n'
A_REMEMBER = ('                self._save_facts()\n'
              '            self._append_log({"kind": "remember", "fact": fact})\n')
A_FACTS = "    # ---- durable facts"
A_APP = "brain = Brain()  # ACCOUNT2_BRAIN_V1"
for a, lab in ((A_IMPORT, "imports"), (A_CONST, "INTERRUPT_TAG"), (A_INIT, "session_id"),
               (A_COMMIT, "commit_turn reset"), (A_FORGET, "forget_all log"),
               (A_REMEMBER, "remember save"), (A_FACTS, "durable facts section")):
    once(brain, a, lab)
once(app, A_APP, "brain = Brain() in stream_asr_test.py")

METHODS = '''    # ---- prefix-cache pre-warm (ACCOUNT2_PREWARM_V1) ------------------------------------------
    def enable_prewarm(self, url: str) -> None:
        """Measured 2026-10-03: llama-server re-reads everything after the first changed message
        (about 14 prompt tokens/s here), so the first question after a history reset or a start-up
        waited 8-16 s. Once enabled, the current history is sent to the server in the background
        (one generated token) at start-up, after each history reset and after a remembered fact
        changes the system prompt. The next real question then reuses the cache. Off unless this is
        called, so unit tests never touch the network. JARVIS_NO_PREWARM=1 switches it off."""
        if os.environ.get("JARVIS_NO_PREWARM"):
            self._log("[brain] prewarm disabled (JARVIS_NO_PREWARM)")
            return
        self._prewarm_url = url
        self._prewarm()

    def _prewarm(self) -> None:
        url = self._prewarm_url
        if not url:
            return
        with self._lock:
            messages = self.build_messages(".")
        worker = threading.Thread(target=self._prewarm_worker, args=(url, messages),
                                  daemon=True, name="brain-prewarm")
        self._prewarm_thread = worker
        worker.start()

    def _prewarm_worker(self, url: str, messages: list) -> None:
        body = json.dumps({"messages": messages, "max_tokens": 1, "stream": False,
                           "cache_prompt": True}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=PREWARM_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            usage = data.get("usage") or {}
            timings = data.get("timings") or {}
            self._log(f"[brain] prewarm done in {time.monotonic() - t0:.1f} s: "
                      f"prompt_tokens={usage.get('prompt_tokens')}, "
                      f"newly_read={timings.get('prompt_n')}, reused={timings.get('cache_n')}")
        except Exception as exc:  # the server may be down or busy: pre-warming is optional
            self._log(f"[brain] prewarm skipped: {type(exc).__name__}: {exc}")

'''

b = brain
b = b.replace(A_IMPORT, "import json\nimport os\nimport re\nimport threading\nimport time\nimport urllib.request\n"
                         "from pathlib import Path\n")
b = b.replace(A_CONST, A_CONST + "PREWARM_TIMEOUT_S = 120  # ACCOUNT2_PREWARM_V1\n")
b = b.replace(A_INIT, A_INIT + "        self._prewarm_url = None  # ACCOUNT2_PREWARM_V1: set by enable_prewarm()\n"
                              "        self._prewarm_thread = None\n")
b = b.replace(A_COMMIT,
              '            reset = len(self.history) > MAX_HISTORY_TURNS * 2  # ACCOUNT2_PREWARM_V1\n'
              '            if reset:\n'
              '                self.history[:] = self.history[-RESET_KEEP_TURNS * 2:]\n'
              '            self._append_log({"kind": "turn", "user": user_text, "assistant": reply,\n'
              '                              "interrupted": bool(interrupted)})\n'
              '            if reset:  # the start of the prompt just changed: read it now, not at the next question\n'
              '                self._prewarm()\n')
b = b.replace(A_FORGET, A_FORGET + "                if had:  # ACCOUNT2_PREWARM_V1: the system prompt changed\n"
                                   "                    self._prewarm()\n")
b = b.replace(A_REMEMBER, '                self._save_facts()\n'
                          '                self._prewarm()  # ACCOUNT2_PREWARM_V1: the system prompt changed\n'
                          '            self._append_log({"kind": "remember", "fact": fact})\n')
b = b.replace(A_FACTS, METHODS + A_FACTS)

line_end = app.index("\n", app.index(A_APP))
a2 = (app[:line_end] + "\nbrain.enable_prewarm(LLAMA_SERVER_URL)  # ACCOUNT2_PREWARM_V1: read the history now "
      "so the first question after a start-up or a history reset is fast" + app[line_end:])

for p in (brain_path, app_path):
    shutil.copy2(p, str(p) + ".bak_preprewarm")
save(brain_path, b, brain_crlf)
save(app_path, a2, app_crlf)
try:
    py_compile.compile(str(brain_path), doraise=True)
    py_compile.compile(str(app_path), doraise=True)
except py_compile.PyCompileError as exc:
    shutil.copy2(str(brain_path) + ".bak_preprewarm", brain_path)
    shutil.copy2(str(app_path) + ".bak_preprewarm", app_path)
    print("SYNTAX CHECK FAILED, both files restored from backup:", exc)
    sys.exit(4)
print("PATCH RESULT: OK (backups: *.bak_preprewarm)")
for p in (brain_path, app_path):
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if MARK in line:
            print(f"{p.name}:{i}: {line.strip()[:110]}")