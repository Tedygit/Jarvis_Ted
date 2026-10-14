"""jarvis_brain.py - Account 2: prompt/context assembly, conversation memory, persistence.

Marker: ACCOUNT2_BRAIN_V1 (2026-10-02). Pure Python, standard library only: no audio, no HTTP,
no threads of its own. The HTTP call to llama-server stays in stream_asr_test.py (get_llm_reply);
this module decides WHAT is sent, what is remembered, and how a reply is cleaned.

Memory stages implemented (see references/architecture.md):
  1. current turn            - the user text passed to build_messages()
  2. recent history          - last MAX_HISTORY_TURNS pairs, batch reset to RESET_KEEP_TURNS
                               (same policy as the verified 2026-09-03 fix: cache-friendly prefix)
  3. durable facts           - only when the user says "remember (that) ...", kept in facts.json,
                               placed in the system prompt, cleared by "forget everything"
  4. persistent log          - every committed turn appended to conversation.jsonl; the last
                               RESUME_TURNS turns are reloaded at start-up
NOT implemented (no evidence it is needed yet): summaries, searchable history.

Files live in MEMORY_DIR (C:\\Jarvis_Ted\\memory): facts.json, conversation.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

MEMORY_DIR = Path(r"C:\Jarvis_Ted\memory")

BASE_SYSTEM_PROMPT = (
    "You are Jarvis_Ted, a local voice coding assistant. Reply in 1-3 short "
    "sentences unless the user explicitly asks for code or a detailed "
    "explanation. Never say 'As an AI language model' or describe what you "
    "are unless directly asked who you are."
)

MAX_HISTORY_TURNS = 6   # pairs kept before a reset fires (12 messages)
RESET_KEEP_TURNS = 2    # pairs that survive a reset (4 messages)
RESUME_TURNS = 2        # pairs reloaded from the log at start-up
MAX_FACTS = 6           # kept small: every fact costs prompt tokens on a CPU-only 3B model
MAX_FACT_CHARS = 150
INTERRUPT_TAG = " [interrupted]"
PREWARM_TIMEOUT_S = 120  # ACCOUNT2_PREWARM_V1

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")
_REMEMBER_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?jarvis[\s,]+)?(?:please\s+)?remember(?:\s+that)?[\s,:]+(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
# ACCOUNT2_REMEMBER_V1: a misheard sentence ("Please remember or skip the functions instead.") saved a wrong fact.
REMEMBER_HINT = "I did not save that. To make me remember something, say: remember that, and then the fact."
_FACT_BAD_START = re.compile(r"^(?:or|and|but|then|instead|skip)\b", re.IGNORECASE)
_FORGET_RE = re.compile(
    r"^\s*(?:(?:hey\s+)?jarvis[\s,]+)?(?:please\s+)?forget\s+(?:everything|all)"
    r"(?:\s+(?:that\s+)?(?:you\s+)?(?:remember(?:ed)?|know|were\s+told))?\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def finish_reply(reply: str, finish_reason: str | None) -> tuple[str, bool]:
    """Clean an LLM reply before it is spoken or stored. Returns (text, trimmed).

    * Removes any copy of INTERRUPT_TAG the model may have imitated from history.
    * If the model stopped on the token cap (finish_reason == "length"), the reply ends mid-sentence;
      cut it back to the last complete sentence. If there is no complete sentence, keep it as is.
    """
    text = reply.replace(INTERRUPT_TAG.strip(), "").strip()
    if finish_reason != "length":
        return text, False
    last_end = None
    for m in _SENTENCE_END.finditer(text):
        last_end = m.end()
    if last_end is None or last_end >= len(text.rstrip()):
        return text, False
    trimmed = text[:last_end].strip()
    if not trimmed:
        return text, False
    return trimmed, True


class Brain:
    """Owns the conversation context. Call from the ASR/LLM worker thread only (a lock guards it anyway)."""

    def __init__(self, memory_dir: Path | str = MEMORY_DIR, resume: bool = True, log=print) -> None:
        self._lock = threading.RLock()
        self._log = log
        self.dir = Path(memory_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.facts_path = self.dir / "facts.json"
        self.log_path = self.dir / "conversation.jsonl"
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self._prewarm_url = None  # ACCOUNT2_PREWARM_V1: set by enable_prewarm()
        self._prewarm_thread = None
        self.history: list[dict[str, str]] = []
        self.facts: list[dict] = self._load_facts()
        resumed = self._resume() if resume else 0
        self._log(f"[brain] memory dir {self.dir}; {len(self.facts)} fact(s); "
                  f"{resumed} earlier turn(s) resumed; session {self.session_id}")

    # ---- context assembly -------------------------------------------------------------------
    def system_prompt(self) -> str:
        with self._lock:
            if not self.facts:
                return BASE_SYSTEM_PROMPT
            lines = "\n".join(f"- {f['text']}" for f in self.facts)
            return (BASE_SYSTEM_PROMPT
                    + "\nFacts the user asked you to remember (in the user's own words):\n" + lines)

    def build_messages(self, user_text: str) -> list[dict[str, str]]:
        """The exact messages list sent to llama-server for this turn."""
        with self._lock:
            return [{"role": "system", "content": self.system_prompt()},
                    *[dict(m) for m in self.history],
                    {"role": "user", "content": user_text}]

    # ---- history ----------------------------------------------------------------------------
    def commit_turn(self, user_text: str, reply: str, interrupted: bool = False) -> None:
        """Record a finished (or interrupted) turn. Call AFTER speaking so `interrupted` is known."""
        with self._lock:
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant",
                                 "content": reply + (INTERRUPT_TAG if interrupted else "")})
            reset = len(self.history) > MAX_HISTORY_TURNS * 2  # ACCOUNT2_PREWARM_V1
            if reset:
                self.history[:] = self.history[-RESET_KEEP_TURNS * 2:]
            self._append_log({"kind": "turn", "user": user_text, "assistant": reply,
                              "interrupted": bool(interrupted)})
            if reset:  # the start of the prompt just changed: read it now, not at the next question
                self._prewarm()

    # ---- prefix-cache pre-warm (ACCOUNT2_PREWARM_V1) ------------------------------------------
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

    def _is_windows(self) -> bool:
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

    def wait_for_prewarm(self, timeout: float = 90.0) -> None:
        """ACCOUNT2_PREWARM_V2: start-up only. Measured 2026-10-03: while a pre-warm runs, speech
        recognition is about 3.6 times slower (8.4 s instead of 2.3 s), so a question asked in the
        first seconds looked ignored. Wait here, with a message, until the pre-warm has finished."""
        worker = self._prewarm_thread
        if worker is None or not worker.is_alive():
            return
        self._log("[brain] warming up the language model (about 20 s) - please wait for "
                  "'Listening continuously' before you speak")
        worker.join(timeout)

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

    # ---- durable facts ----------------------------------------------------------------------
    def try_memory_command(self, text: str) -> str | None:
        """If `text` is "remember (that) X" or "forget everything", apply it and return the sentence
        to speak (no LLM call). Otherwise return None."""
        with self._lock:
            if _FORGET_RE.match(text):
                had = len(self.facts)
                self.facts = []
                self._save_facts()
                self._append_log({"kind": "forget_all", "count": had})
                if had:  # ACCOUNT2_PREWARM_V1: the system prompt changed
                    self._prewarm()
                return ("Done. I have forgotten everything you asked me to remember."
                        if had else "There was nothing to forget.")
            m = _REMEMBER_RE.match(text)
            if not m:
                return None
            fact = m.group(1).strip(" .!?,;:\"'")
            if len(re.sub(r"\W", "", fact)) < 3 or fact.lower() in {"that", "this", "it"}:
                return None
            if _FACT_BAD_START.match(fact):  # ACCOUNT2_REMEMBER_V1
                self._append_log({"kind": "remember_rejected", "text": text[:150]})
                return REMEMBER_HINT
            fact = fact[:MAX_FACT_CHARS]
            if not any(f["text"].lower() == fact.lower() for f in self.facts):
                self.facts.append({"text": fact, "t": time.strftime("%Y-%m-%dT%H:%M:%S")})
                if len(self.facts) > MAX_FACTS:
                    self.facts = self.facts[-MAX_FACTS:]
                self._save_facts()
                self._prewarm()  # ACCOUNT2_PREWARM_V1: the system prompt changed
            self._append_log({"kind": "remember", "fact": fact})
            return "Noted. I will remember that."

    # ---- persistence ------------------------------------------------------------------------
    def _load_facts(self) -> list[dict]:
        try:
            data = json.loads(self.facts_path.read_text(encoding="utf-8"))
            return [f for f in data if isinstance(f, dict) and isinstance(f.get("text"), str)][-MAX_FACTS:]
        except FileNotFoundError:
            return []
        except Exception as exc:  # corrupt file: keep running with no facts, do not overwrite it silently
            self._log(f"[brain] WARNING: could not read {self.facts_path}: {exc}")
            return []

    def _save_facts(self) -> None:
        tmp = self.facts_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.facts, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.facts_path)

    def _append_log(self, record: dict) -> None:
        record = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": self.session_id, **record}
        try:
            with open(self.log_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._log(f"[brain] WARNING: could not append to {self.log_path}: {exc}")

    def _resume(self) -> int:
        try:
            size = self.log_path.stat().st_size
        except OSError:
            return 0
        try:
            with open(self.log_path, "rb") as fh:
                if size > 65536:
                    fh.seek(size - 65536)
                    data = fh.read().split(b"\n", 1)[-1]
                else:
                    data = fh.read()
        except OSError:
            return 0
        turns = []
        for line in data.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("kind") == "turn" and isinstance(rec.get("user"), str) \
                    and isinstance(rec.get("assistant"), str):
                turns.append(rec)
        turns = turns[-RESUME_TURNS:]
        for rec in turns:
            self.history.append({"role": "user", "content": rec["user"]})
            self.history.append({"role": "assistant",
                                 "content": rec["assistant"] + (INTERRUPT_TAG if rec.get("interrupted") else "")})
        return len(turns)