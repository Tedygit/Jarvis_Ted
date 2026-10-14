"""apply_account2_prewarm_wait.py - idempotent patcher, marker ACCOUNT2_PREWARM_V2.

Start-up only: the app waits (with a clear message) until the first pre-warm has finished, before it
starts listening. Needs ACCOUNT2_PREWARM_V1 to be applied first. Backups: <file>.bak_prewait.
Usage: python apply_account2_prewarm_wait.py [tools_dir]
"""
import py_compile
import shutil
import sys
from pathlib import Path

MARK1, MARK2 = "ACCOUNT2_PREWARM_V1", "ACCOUNT2_PREWARM_V2"
tools = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
brain_path = tools / "jarvis_brain.py"
app_path = tools / "stream_asr_test.py"


def load(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def save(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


brain, brain_crlf = load(brain_path)
app, app_crlf = load(app_path)
if MARK2 in brain and MARK2 in app:
    print("Already patched - nothing to do.")
    sys.exit(0)
if MARK2 in brain or MARK2 in app:
    print("PARTIALLY PATCHED: one file has the V2 marker and the other does not. Nothing was written.")
    sys.exit(3)
if MARK1 not in brain or MARK1 not in app:
    print("ACCOUNT2_PREWARM_V1 is not applied in both files. Nothing was written.")
    sys.exit(3)

A_BRAIN = "    def _prewarm(self) -> None:\n"
A_APP = "brain.enable_prewarm(LLAMA_SERVER_URL)  # ACCOUNT2_PREWARM_V1"
for text, anchor, label in ((brain, A_BRAIN, "def _prewarm"), (app, A_APP, "enable_prewarm line")):
    n = text.count(anchor)
    if n != 1:
        print(f"PATCH POINT NOT UNIQUE/FOUND: {label} (count {n}). Nothing was written.")
        sys.exit(2)

WAIT = '''    def wait_for_prewarm(self, timeout: float = 90.0) -> None:
        """ACCOUNT2_PREWARM_V2: start-up only. Measured 2026-10-03: while a pre-warm runs, speech
        recognition is about 3.6 times slower (8.4 s instead of 2.3 s), so a question asked in the
        first seconds looked ignored. Wait here, with a message, until the pre-warm has finished."""
        worker = self._prewarm_thread
        if worker is None or not worker.is_alive():
            return
        self._log("[brain] warming up the language model (about 20 s) - please wait for "
                  "'Listening continuously' before you speak")
        worker.join(timeout)

'''
b = brain.replace(A_BRAIN, WAIT + A_BRAIN)
line_end = app.index("\n", app.index(A_APP))
a2 = (app[:line_end] + "\nbrain.wait_for_prewarm()  # ACCOUNT2_PREWARM_V2: do not start listening until the model is warm"
      + app[line_end:])

for p in (brain_path, app_path):
    shutil.copy2(p, str(p) + ".bak_prewait")
save(brain_path, b, brain_crlf)
save(app_path, a2, app_crlf)
try:
    py_compile.compile(str(brain_path), doraise=True)
    py_compile.compile(str(app_path), doraise=True)
except py_compile.PyCompileError as exc:
    shutil.copy2(str(brain_path) + ".bak_prewait", brain_path)
    shutil.copy2(str(app_path) + ".bak_prewait", app_path)
    print("SYNTAX CHECK FAILED, both files restored from backup:", exc)
    sys.exit(4)
print("PATCH RESULT: OK (backups: *.bak_prewait)")
for p in (brain_path, app_path):
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if MARK2 in line:
            print(f"{p.name}:{i}: {line.strip()[:110]}")