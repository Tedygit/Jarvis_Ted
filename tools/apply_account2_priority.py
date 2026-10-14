"""apply_account2_priority.py - idempotent patcher, marker ACCOUNT2_PRIORITY_V1.

Adds Brain.lower_llama_priority() to tools/jarvis_brain.py and one call to it in tools/stream_asr_test.py
(after the start-up wait). Needs ACCOUNT2_PREWARM_V1 and ACCOUNT2_PREWARM_V2 first. Backups: <file>.bak_prepriority.
Usage: python apply_account2_priority.py [tools_dir]
"""
import py_compile
import shutil
import sys
from pathlib import Path

NEED = ("ACCOUNT2_PREWARM_V1", "ACCOUNT2_PREWARM_V2")
MARK = "ACCOUNT2_PRIORITY_V1"
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
if MARK in brain and MARK in app:
    print("Already patched - nothing to do.")
    sys.exit(0)
if MARK in brain or MARK in app:
    print("PARTIALLY PATCHED: one file has the marker and the other does not. Nothing was written.")
    sys.exit(3)
for need in NEED:
    if need not in brain or need not in app:
        print(f"{need} is not applied in both files. Nothing was written.")
        sys.exit(3)

A_IMPORT = "import json\nimport os\nimport re\nimport threading\nimport time\nimport urllib.request\n"
A_BRAIN = "    def wait_for_prewarm(self, timeout: float = 90.0) -> None:\n"
A_APP = "brain.wait_for_prewarm()  # ACCOUNT2_PREWARM_V2"
for text, anchor, label in ((brain, A_IMPORT, "imports"), (brain, A_BRAIN, "def wait_for_prewarm"),
                            (app, A_APP, "wait_for_prewarm line")):
    n = text.count(anchor)
    if n != 1:
        print(f"PATCH POINT NOT UNIQUE/FOUND: {label} (count {n}). Nothing was written.")
        sys.exit(2)

METHOD = '''    def _is_windows(self) -> bool:
        return os.name == "nt"

    def lower_llama_priority(self) -> None:
        """ACCOUNT2_PRIORITY_V1. Measured 2026-10-03 (replayed recording, llama-server reading a new
        167-token prompt): speech recognition took 13.2 s at Normal priority and 2.2 s (baseline 2.1 s)
        with llama-server at BelowNormal; the prompt read became about 11% slower. Called once at
        start-up by the app. JARVIS_NO_LLAMA_PRIORITY=1 leaves the priority unchanged. Windows only;
        a failure is logged, never raised. A llama-server restarted later is back at Normal."""
        if os.environ.get("JARVIS_NO_LLAMA_PRIORITY"):
            self._log("[brain] llama-server priority left unchanged (JARVIS_NO_LLAMA_PRIORITY)")
            return
        if not self._is_windows():
            return
        script = ("$n = 0; Get-Process llama-server -ErrorAction SilentlyContinue | "
                  "ForEach-Object { $_.PriorityClass = 'BelowNormal'; $n++ }; Write-Output $n")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=20)
            count = (out.stdout or "").strip() or "0"
            self._log(f"[brain] llama-server priority set to BelowNormal ({count} process(es))")
        except Exception as exc:
            self._log(f"[brain] could not change llama-server priority: {type(exc).__name__}: {exc}")

'''
b = brain.replace(A_IMPORT, "import json\nimport os\nimport re\nimport subprocess\nimport threading\nimport time\n"
                            "import urllib.request\n")
b = b.replace(A_BRAIN, METHOD + A_BRAIN)
line_end = app.index("\n", app.index(A_APP))
a2 = (app[:line_end] + "\nbrain.lower_llama_priority()  # ACCOUNT2_PRIORITY_V1: speech recognition must not be starved "
      "by the language model" + app[line_end:])

for p in (brain_path, app_path):
    shutil.copy2(p, str(p) + ".bak_prepriority")
save(brain_path, b, brain_crlf)
save(app_path, a2, app_crlf)
try:
    py_compile.compile(str(brain_path), doraise=True)
    py_compile.compile(str(app_path), doraise=True)
except py_compile.PyCompileError as exc:
    shutil.copy2(str(brain_path) + ".bak_prepriority", brain_path)
    shutil.copy2(str(app_path) + ".bak_prepriority", app_path)
    print("SYNTAX CHECK FAILED, both files restored from backup:", exc)
    sys.exit(4)
print("PATCH RESULT: OK (backups: *.bak_prepriority)")
for p in (brain_path, app_path):
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if MARK in line:
            print(f"{p.name}:{i}: {line.strip()[:110]}")