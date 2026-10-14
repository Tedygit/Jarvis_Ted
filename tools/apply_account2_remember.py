import py_compile
import shutil
import sys
from pathlib import Path

MARK = "ACCOUNT2_REMEMBER_V1"
brain_path = Path(sys.argv[1]) / "jarvis_brain.py"
raw = brain_path.read_bytes().decode("utf-8")
crlf = "\r\n" in raw
brain = raw.replace("\r\n", "\n")
if MARK in brain:
    print("Already patched - nothing to do.")
    sys.exit(0)

A_RE = ('_REMEMBER_RE = re.compile(\n'
        '    r"^\\s*(?:(?:hey\\s+)?jarvis[\\s,]+)?(?:please\\s+)?remember(?:\\s+that)?[\\s,:]+(.+?)\\s*$",\n'
        '    re.IGNORECASE | re.DOTALL,\n'
        ')\n')
A_USE = ('            fact = m.group(1).strip(" .!?,;:\\"\'")\n'
         '            if len(re.sub(r"\\W", "", fact)) < 3 or fact.lower() in {"that", "this", "it"}:\n'
         '                return None\n')
for anchor, label in ((A_RE, "_REMEMBER_RE definition"), (A_USE, "fact extraction")):
    n = brain.count(anchor)
    if n != 1:
        print(f"PATCH POINT NOT UNIQUE/FOUND: {label} (count {n}). Nothing was written.")
        sys.exit(2)

NEW_RE = (A_RE +
          '# ACCOUNT2_REMEMBER_V1: a misheard sentence ("Please remember or skip the functions instead.") saved a wrong fact.\n'
          'REMEMBER_HINT = "I did not save that. To make me remember something, say: remember that, and then the fact."\n'
          '_FACT_BAD_START = re.compile(r"^(?:or|and|but|then|instead|skip)\\b", re.IGNORECASE)\n')
NEW_USE = (A_USE +
           '            if _FACT_BAD_START.match(fact):  # ACCOUNT2_REMEMBER_V1\n'
           '                self._append_log({"kind": "remember_rejected", "text": text[:150]})\n'
           '                return REMEMBER_HINT\n')
new = brain.replace(A_RE, NEW_RE).replace(A_USE, NEW_USE)

shutil.copy2(brain_path, str(brain_path) + ".bak_preremember")
brain_path.write_bytes((new.replace("\n", "\r\n") if crlf else new).encode("utf-8"))
try:
    py_compile.compile(str(brain_path), doraise=True)
except py_compile.PyCompileError as exc:
    shutil.copy2(str(brain_path) + ".bak_preremember", brain_path)
    print("SYNTAX CHECK FAILED, file restored from backup:", exc)
    sys.exit(4)
print("PATCH RESULT: OK (backup: jarvis_brain.py.bak_preremember)")