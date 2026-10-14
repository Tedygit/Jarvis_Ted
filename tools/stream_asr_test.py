"""
Jarvis_Ted - M2/M3 async pipeline + M4 turn-cancellation.

2026-09-06 finding (this session - turn-cancellation + TTS re-point):
Two changes, scoped exactly to the two items JARVIS_TED_STATUS.md listed
as blocked on this file's source:

1. Turn-cancellation ("dropping stale queued segments once new speech
   arrives", the confirmed-structural M4 blocker - two live runs showed
   fully-processed replies to segments the user had already spoken past:
   300.8s/10 of 32 segments left unprocessed, 54.1s/3 of 9 left
   unprocessed). Root cause: work_q was strict FIFO with no way to know a
   queued-or-in-flight segment had been superseded by newer speech, and
   both curl.exe calls used subprocess.run(), which blocks to completion
   with no way to interrupt an in-flight request.

   Fix: a TurnState object tracks a monotonic "generation" counter,
   bumped once per SPEECH START. Every segment is tagged with the
   generation active when its SPEECH START fired. The worker checks its
   segment's generation against the current generation at three points -
   before ASR, after ASR/before LLM, after LLM/before TTS - and drops the
   segment the instant it's stale, at whichever point that becomes true.
   SPEECH START also terminates whatever curl.exe call is currently in
   flight (both transcribe_segment and get_llm_reply now run curl via
   Popen instead of subprocess.run, registered with TurnState so it can
   be killed from the main thread), so a blocking ASR/LLM call unblocks
   in roughly the time curl.exe takes to die instead of running to
   completion. The three checkpoints are what actually guarantee
   correctness (a segment is dropped regardless of what happens to its
   subprocess); Popen-based cancellation only makes that drop happen
   quickly instead of after the full remaining request time.

   NOT touched by this fix, still open per the status file: the
   turn-manager gap (merging a pause-fragmented sentence back into one
   turn instead of treating each VAD segment as its own turn), and VAD
   firing on short noise/filler bursts. Both are separate, unrelated
   problems.

2. TTS re-point: the embedded subprocess-based CancellableTTS
   (`python -m piper --output-raw`) is replaced with
   `from tts_piper import CancellableTTS` - the 2026-09-06 resident-voice
   /resident-stream implementation already verified via
   diagnose_tts_gaps.py (TTS click, E3) and test_barge_in.py (stop()/
   abort(), E4). No changes to tts_piper.py itself; it's used as-is.

UNVERIFIED as of this edit - implemented, not yet run on the real
machine. Test plan and what to report back are in the accompanying chat
message; do not treat this as resolved in JARVIS_TED_STATUS.md until
that's actually run.

2026-09-03 finding (post-history-patch live run, 196.3s wall / 195.9s
audio, 28 segments detected, only 10 dequeued before Ctrl+C, 17 left
unprocessed): adding conversation_history (see get_llm_reply below) fixed
cross-turn coherence but reintroduced a severe backlog, worse than the
original single-thread bug. Two replies (seg8, seg9) took 45.3s and 49.1s.

Root cause CONFIRMED directly from llama-server's own terminal (Terminal
B) timing log, not inferred: for the first seven LLM calls, each request
shared a long cached prefix with the previous one (llama-server's own
reported `sim_best` 0.85-0.95, meaning 85-95% of the prompt was reused
from KV cache, so each turn only evaluated ~20-30 new tokens - cheap).
The turn immediately after conversation_history first exceeded
MAX_HISTORY_TURNS and evicted its oldest user/assistant pair, `sim_best`
collapsed to 0.122, and stayed collapsed (0.112) on the very next turn
too - two turns in a row, not one. Mechanism: llama-server matches by
*longest common prefix* against the previous request. The old eviction
(`del conversation_history[:excess]`, dropping exactly the oldest pair
every turn once over the cap) removes tokens from the FRONT of the
message list every single turn from that point on, so every future
request's prefix diverges from the previous one right after the system
prompt - forcing a near-total prompt recompute, every turn, for the rest
of the session, not just the turn that evicted. At this machine's
measured ~11 tok/s prompt-eval speed, recomputing a ~450-500 token
prompt costs ~40-45s, which is exactly what seg8/seg9 show. The trailing
`[PIPELINE FAILED] curl failed (exit 3221225786)` on seg10 lines up with
Terminal B's own log for that call too: `launch_slot_` fires but there is
no `print_timing` line after it, meaning Ctrl+C hit while it was still
mid-recompute - curl.exe getting killed mid-request, not an independent
bug, now confirmed from Terminal B's log instead of inferred from
wall-clock math.

Fix applied below (isolating this one change - the turn-manager gap and
the "No audible input" false-positive noted earlier are both still open,
untouched): eviction now happens in one batch instead of one pair per
turn. Once history exceeds MAX_HISTORY_TURNS pairs, it drops straight
down to the last RESET_KEEP_TURNS pairs instead of trimming by one pair
at a time. This still pays one full-recompute turn at the moment of
reset (unavoidable - the prompt prefix genuinely changes), but then
grows cleanly again with a stable, cache-friendly prefix until the next
reset, instead of paying that cost on every turn from the first eviction
onward. Still a Layer-2-style stopgap window (see architecture.md's
layered-memory model), not the Layer 3+ summary/durable-facts memory
that's real M5/M7 scope.

2026-08-31 finding (second full live run, 257.2s, 24 turns): after fixing
the crash/persona/latency-cap issues, the pipeline still appeared to "go
deaf" mid-utterance. Confirmed by timestamp evidence: SPEECH START/END
pairs kept firing far faster than real time immediately after a long LLM
reply finished (e.g. a reply ending at ~81.14s was followed by
`[81.16s] SPEECH START` for a segment reported as 3.71s of *audio* but
only 0.09s of *elapsed* time since the previous event - the signature of
a backlog of already-recorded audio being drained in a burst). Root
cause: everything ran on one thread, so while llama-server was generating
a reply, incoming audio kept queuing but was not looked at.

Fix: audio capture + VAD now run continuously on the main thread with NO
blocking calls in that loop. On SPEECH END, the segment is handed off to
a queue and a separate worker thread does ASR + LLM fully decoupled from
listening. SPEECH START/END timestamps should now reflect true real-time
detection. Console output may interleave (a new SPEECH START can print
while an older segment's ASR/LLM result is still being printed) - that is
expected with two threads running concurrently, not a bug. Each worker
print is tagged [segN] so it's clear which turn it belongs to.

Segments used to queue up and be answered strictly in order even past
the point where the user had moved on - the 2026-09-06 fix above is what
actually resolves that. The turn-manager gap (VAD segments wired
directly to LLM turns, with nothing merging a pause-fragmented sentence
back together) is a separate, still-open item.

2026-08-31 fixes carried over from the previous round (still in place):
  - encoding="utf-8", errors="replace" on both curl calls - fixes a real
    UnicodeDecodeError crash (Windows' cp1252 default vs. UTF-8 JSON
    responses).
  - SYSTEM_PROMPT for a Jarvis_Ted persona (partially effective - still
    sometimes reverts to generic "large language model" self-description
    with no conversation memory to reinforce it turn to turn).
  - LLM_MAX_TOKENS cut 200 -> 80 (bounds worst-case reply latency to
    roughly 10.7s at the benchmarked ~7.5 tok/s generation speed, vs.
    ~26.7s+ before; confirmed second run's replies landed 2.4-9.4s).
  - Empty ASR transcripts skip the LLM call instead of wasting a request.

audio_ctx policy (evidenced 2026-08-31, capture_test.wav, 5.0s clip):
    full context: ~2.05s. audio_ctx=512 (2x headroom over 5.0s): 0.755s,
    transcript IDENTICAL. audio_ctx=256 (~1x duration, no headroom):
    0.482s, transcript CHANGED ("good" -> "okay"). 512 is the only
    confirmed-safe floor so far.

PREREQUISITES - start both first, each in its own terminal, and leave both running:
    Terminal A (ASR):
        C:\\tools\\whisper.cpp\\build\\bin\\Release\\whisper-server.exe ^
            -m C:\\tools\\whisper.cpp\\models\\ggml-small.en.bin -t 8 ^
            --host 127.0.0.1 --port 8080
    Terminal B (LLM):
        C:\\tools\\llama.cpp\\build\\bin\\Release\\llama-server.exe ^
            -m C:\\Jarvis_Ted\\models\\Qwen2.5-Coder-3B-Instruct-Q5_K_M.gguf ^
            -c 4096 -t 8 -np 1 --host 127.0.0.1 --port 8081

Run (Terminal C, from the project's venv, both servers above already running):
    cd C:\\Jarvis_Ted
    .\\.venv\\Scripts\\python.exe tools\\stream_asr_test.py

Stop:
    Ctrl+C
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from silero_vad import VADIterator, load_silero_vad

from tts_piper import CancellableTTS
from jarvis_brain import Brain, finish_reply  # ACCOUNT2_BRAIN_V1
from jarvis_stream import SentenceSplitter, parse_sse_line  # ACCOUNT2_STREAM_V1
from jarvis_tools import CodeTools, try_tool_command  # ACCOUNT3_TOOLS_V1

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 512
SILENCE_WARNING_SEC = 3.0
NEAR_ZERO_PEAK = 1  # peak < 1 means every sample in the chunk is exactly 0 (digital silence). Measured quiet-room floor, earphone connected (2026-10-01): median peak 1, max 36, 0 exact-zero chunks in 250, so the old value 50 fired on any quiet room.

WHISPER_SERVER_URL = "http://127.0.0.1:8080/inference"
LLAMA_SERVER_URL = "http://127.0.0.1:8081/v1/chat/completions"
CURL_EXE = "curl.exe"

SYSTEM_PROMPT = (
    "You are Jarvis_Ted, a local voice coding assistant. Reply in 1-3 short "
    "sentences unless the user explicitly asks for code or a detailed "
    "explanation. Never say 'As an AI language model' or describe what you "
    "are unless directly asked who you are."
)
LLM_MAX_TOKENS = 80

# History policy - see the 2026-09-03 finding at the top of this file.
MAX_HISTORY_TURNS = 6   # cap before a reset fires (12 messages)
RESET_KEEP_TURNS = 2    # how much survives a reset (4 messages)
conversation_history: list[dict[str, str]] = []

AUDIO_CTX_MIN = 512
AUDIO_CTX_MARGIN = 2.0
AUDIO_CTX_UNITS_PER_SEC = 50
AUDIO_CTX_MAX = 1500
# ACCOUNT1_ASR_PROMPT_V1: initial prompt for whisper (vocabulary bias). Replay of the live run: "sets" was heard as "6"/"states",
# "Python" as "I-D"; with this prompt they were correct, silence and noise controls stayed blank. JARVIS_ASR_PROMPT=off disables it,
# any other value replaces it (no quotes or semicolons).
# ACCOUNT1_ASR_PROMPT_P4: every example prefixed with Python (replay of 33 segments: "pattern" -> "Python", controls stay tags)
ASR_PROMPT_DEFAULT = ("The user asks a programming assistant about Python and Java. "
                      "Tell me about Python sets, Python tuples, Python dictionaries and Python functions instead of Python lists.")
_asr_prompt_env = __import__("os").environ.get("JARVIS_ASR_PROMPT")
ASR_PROMPT = ASR_PROMPT_DEFAULT if _asr_prompt_env is None else (
    "" if _asr_prompt_env.strip().lower() in ("", "off", "0", "none") else _asr_prompt_env)
print(f"[asr] initial prompt: {ASR_PROMPT!r}" if ASR_PROMPT else "[asr] initial prompt: off")


class TurnState:
    """Thread-safe turn-generation tracker + in-flight external-process
    canceller - see the 2026-09-06 docstring entry above for the full
    rationale.

    'Generation' increments once per SPEECH START. A segment captured
    between one SPEECH START and its matching SPEECH END belongs to the
    generation that was active at that START. Any segment whose
    generation is lower than the CURRENT generation has been superseded
    by newer speech and must be dropped rather than answered.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._active_proc: subprocess.Popen | None = None

    def new_turn(self) -> int:
        """Call exactly once per SPEECH START. Bumps the generation and
        immediately terminates whatever curl.exe call is in flight for
        the now-superseded previous turn, so a blocking ASR/LLM call
        unblocks right away instead of running to completion. Returns
        the new generation number - tag the segment this SPEECH START
        opens with this value."""
        with self._lock:
            self._generation += 1
            gen = self._generation
            proc = self._active_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return gen

    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def register_proc(self, proc: "subprocess.Popen | None") -> None:
        """Called by _run_curl before and after each curl.exe call so
        new_turn() always has a live handle to terminate, or None once
        the call finishes normally."""
        with self._lock:
            self._active_proc = proc


turn_state = TurnState()

HALF_DUPLEX = not __import__('os').environ.get('JARVIS_FULL_DUPLEX')


class _TtsGate:
    TAIL_SEC = 0.7
    def __init__(self):
        self._lock = threading.Lock()
        self._busy = 0
        self._until = 0.0
    def begin(self):
        with self._lock:
            self._busy += 1
    def end(self):
        with self._lock:
            self._busy -= 1
            self._until = time.monotonic() + self.TAIL_SEC
    def blocked(self):
        with self._lock:
            return self._busy > 0 or time.monotonic() < self._until


_tts_gate = _TtsGate()


@dataclass
class TurnResult:
    chunks: list
    speech_ms: float
    pieces: int
    end_elapsed: float
    accepted: bool


class TurnAssembler:
    """Account 1 turn assembly (2026-10-01, UNVERIFIED until run live).

    A VAD segment is a candidate piece, not a finished turn. After a piece
    ends, the assembler HOLDS it for merge_gap_ms. If speech starts again
    inside that window, the new piece is merged into the same turn (the
    real pause audio is kept). Only when the window elapses is the turn
    released. A released turn whose total estimated speech is shorter than
    min_speech_ms is rejected as a noise/filler burst and never reaches
    ASR, LLM or TTS.

    Speech time is estimated as piece audio length minus the VAD hangover
    (--min-silence-ms). Pure bookkeeping: no I/O, no threads.
    """

    def __init__(self, merge_gap_ms: int, min_speech_ms: int, hangover_ms: int) -> None:
        self._chunk_ms = CHUNK_SAMPLES * 1000.0 / SAMPLE_RATE
        self._gap_limit = max(0, int(round(merge_gap_ms / self._chunk_ms)))
        self._min_speech_ms = float(min_speech_ms)
        self._hangover_ms = float(hangover_ms)
        self.chunks: list = []
        self._state = "idle"  # idle | piece | hold
        self._speech_ms = 0.0
        self._pieces = 0
        self._piece_start = 0
        self._gap = 0
        self._end_elapsed = 0.0

    @property
    def collecting(self) -> bool:
        """True while the caller must append every incoming chunk to .chunks."""
        return self._state != "idle"

    def start_piece(self, chunk: np.ndarray, preroll=None) -> bool:  # ACCOUNT1_PREROLL_V1
        """VAD start. Returns True if this continues a held turn (the chunk was
        already appended as gap audio), False if it opens a new turn."""
        if self._state == "hold":
            continued = True
            self._pieces += 1
            self._piece_start = len(self.chunks) - 1
        else:
            continued = False
            pre = [c.copy() for c in preroll] if preroll else []  # ACCOUNT1_PREROLL_V1: audio just before the VAD start (the VAD fires late)
            self.chunks = pre + [chunk.copy()]
            self._speech_ms = 0.0
            self._pieces = 1
            self._piece_start = len(pre)
        self._state = "piece"
        self._gap = 0
        return continued

    def end_piece(self, elapsed: float) -> tuple:
        """VAD end. Returns (piece_audio_seconds, piece_speech_ms)."""
        if self._state != "piece":
            return 0.0, 0.0
        n = len(self.chunks) - self._piece_start
        piece_ms = max(0.0, n * self._chunk_ms - self._hangover_ms)
        self._speech_ms += piece_ms
        self._end_elapsed = elapsed
        self._state = "hold"
        self._gap = 0
        return n * self._chunk_ms / 1000.0, piece_ms

    def speech_ms_so_far(self) -> float:
        """Conservative running estimate: while a piece is open, the VAD hangover
        is assumed to be trailing silence, so a short burst is never over-counted."""
        if self._state == "piece":
            n = len(self.chunks) - self._piece_start
            return self._speech_ms + max(0.0, n * self._chunk_ms - self._hangover_ms)
        return self._speech_ms

    def piece_speech_ms_so_far(self) -> float:  # ACCOUNT1_STOPBURST_V1
        """Speech estimate of the open piece only (earlier held pieces are not added)."""
        if self._state == "piece":
            n = len(self.chunks) - self._piece_start
            return max(0.0, n * self._chunk_ms - self._hangover_ms)
        return 0.0

    def tick(self) -> "TurnResult | None":
        """Call once per chunk, after that chunk was appended (if collecting)
        and before VAD events are handled. Releases the held turn once the
        merge window has elapsed."""
        if self._state != "hold":
            return None
        self._gap += 1
        if self._gap < self._gap_limit:
            return None
        chunks = self.chunks[: len(self.chunks) - self._gap]  # drop the waiting-window silence
        result = TurnResult(
            chunks=chunks, speech_ms=self._speech_ms, pieces=self._pieces,
            end_elapsed=self._end_elapsed, accepted=self._speech_ms >= self._min_speech_ms,
        )
        self.chunks = []
        self._state = "idle"
        self._speech_ms = 0.0
        self._pieces = 0
        self._piece_start = 0
        self._gap = 0
        return result

# Resident Piper voice + resident output stream - loaded once here, kept
# alive for the process lifetime (see tools/tts_piper.py's module
# docstring). Loading happens at import time, same as this file's
# previous embedded CancellableTTS - if the model file can't be found,
# fail loudly here rather than partway through a live session.
tts = CancellableTTS()
brain = Brain()  # ACCOUNT2_BRAIN_V1: prompt assembly, history, persistence (tools\jarvis_brain.py)
brain.enable_prewarm(LLAMA_SERVER_URL)  # ACCOUNT2_PREWARM_V1: read the history now so the first question after a start-up or a history reset is fast
brain.wait_for_prewarm()  # ACCOUNT2_PREWARM_V2: do not start listening until the model is warm
brain.lower_llama_priority()  # ACCOUNT2_PRIORITY_V1: speech recognition must not be starved by the language model
code_tools = None  # ACCOUNT3_TOOLS_V1: read-only coding tools, OFF unless JARVIS_TOOLS=1
if __import__("os").environ.get("JARVIS_TOOLS"):
    code_tools = CodeTools(__import__("os").environ.get("JARVIS_PROJECT_ROOT") or r"C:\Jarvis_Ted")
    print(f"[tools] read-only coding tools ON, project root {code_tools.root}")


def compute_audio_ctx(duration_seconds: float) -> int:
    scaled = int(duration_seconds * AUDIO_CTX_UNITS_PER_SEC * AUDIO_CTX_MARGIN)
    ctx = min(max(AUDIO_CTX_MIN, scaled), AUDIO_CTX_MAX)
    return min(((ctx + 255) // 256) * 256, AUDIO_CTX_MAX)  # ACCOUNT1_CTX256_V1: multiple of 256 (kept words that the tight margin lost; status update 20)


def find_input_device(name_substring: str, preferred_hostapi: str) -> int:
    """Unchanged from stream_vad_test.py (verified 2026-08-30/31)."""
    hostapis = sd.query_hostapis()
    preferred_hostapi_index = next(
        (i for i, api in enumerate(hostapis) if api["name"] == preferred_hostapi), None
    )
    all_devices = sd.query_devices()
    matches = [
        (idx, dev)
        for idx, dev in enumerate(all_devices)
        if dev["max_input_channels"] > 0 and name_substring.lower() in dev["name"].lower()
    ]
    if not matches:
        available = "\n".join(
            f"   [{i}] {d['name']}" for i, d in enumerate(all_devices) if d["max_input_channels"] > 0
        )
        raise RuntimeError(
            f"No input device matched '{name_substring}'.\nAvailable input devices:\n{available}\n"
            f"Re-run with --device-name set to a substring from one of the names above."
        )
    if preferred_hostapi_index is not None:
        for idx, dev in matches:
            if dev["hostapi"] == preferred_hostapi_index:
                return idx
    return matches[0][0]


def build_extra_settings(device_index: int, auto_convert: bool):
    """Unchanged from stream_vad_test.py (verified 2026-08-30)."""
    device_info = sd.query_devices(device_index)
    hostapi_name = sd.query_hostapis()[device_info["hostapi"]]["name"]
    if hostapi_name == "Windows WASAPI" and auto_convert:
        return sd.WasapiSettings(auto_convert=True), hostapi_name
    return None, hostapi_name


def write_segment_wav(path: Path, chunks: list[np.ndarray]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for chunk in chunks:
            w.writeframes(chunk.tobytes())


def _run_curl(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Runs curl.exe via Popen (not subprocess.run) and registers the
    live process with turn_state so a new SPEECH START can terminate it
    mid-flight. This is what makes an in-progress ASR/LLM request
    actually cancellable instead of always running to completion once
    started."""
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )
    turn_state.register_proc(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    finally:
        turn_state.register_proc(None)
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


# ACCOUNT3_ASR_GUARD_V1 (2026-10-02): guard thresholds are untested guesses; set JARVIS_NO_ASR_GUARD=1 to disable
ASR_GUARD_MIN_SECONDS = 4.0
ASR_GUARD_MIN_WPS = 0.8
ASR_GUARD_MAX_REPEAT = 5


def _asr_text_suspicious(text: str, duration_seconds: float):
    """Reason string if the transcript looks truncated or looped, else None."""
    words = text.split()
    run, maxrep, prev = 0, 0, None
    for w in words:
        run = run + 1 if w == prev else 1
        maxrep = max(maxrep, run)
        prev = w
    if maxrep >= ASR_GUARD_MAX_REPEAT:
        return f"repeat loop (same word {maxrep}x in a row)"
    if duration_seconds >= ASR_GUARD_MIN_SECONDS and len(words) < ASR_GUARD_MIN_WPS * duration_seconds:
        return f"{len(words)} word(s) for {duration_seconds:.1f}s of audio"
    return None


def _asr_guard(args, wav_path, duration_seconds, wall_time, text):
    if __import__("os").environ.get("JARVIS_NO_ASR_GUARD"):
        return wall_time, text
    try:
        size = Path(wav_path).stat().st_size
        expected = 44 + int(duration_seconds * 16000 * 2)
        if abs(size - expected) > max(2048, expected // 50):
            print(f"[ASR GUARD] wav size {size} B vs ~{expected} B expected for {duration_seconds:.2f}s")
    except OSError:
        pass
    reason = _asr_text_suspicious(text, duration_seconds)
    if reason is None:
        return wall_time, text
    print(f"[ASR GUARD] suspicious transcript ({reason}): {text!r} - retrying once")
    start = time.monotonic()
    proc = _run_curl(args, timeout=30)
    wall2 = time.monotonic() - start
    if proc.returncode != 0:
        print(f"[ASR GUARD] retry failed (curl exit {proc.returncode}); keeping the first transcript")
        return wall_time, text
    try:
        text2 = json.loads(proc.stdout)["text"].strip()
    except (json.JSONDecodeError, KeyError):
        print("[ASR GUARD] retry response unreadable; keeping the first transcript")
        return wall_time, text
    reason2 = _asr_text_suspicious(text2, duration_seconds)
    better = len(text2.split()) > len(text.split()) or (reason2 is None and "repeat loop" in reason)
    print(f"[ASR GUARD] retry ({wall2:.2f}s): {len(text2.split())} word(s), still suspicious={reason2 is not None}, "
          f"{'USING retry' if better else 'keeping first'}: {text2!r}")
    return wall_time + wall2, (text2 if better else text)


def transcribe_segment(wav_path: Path, duration_seconds: float, retries: int = 2, retry_delay: float = 0.15) -> tuple[float, str]:
    """POST one segment to the running whisper-server. Returns (wall_time_s, text).

    Retries on failure: a freshly-written WAV can briefly fail here on Windows
    (antivirus scan / file-cache settle) even though the file itself is fine -
    resending the identical bytes a moment later has succeeded every time this
    was tested. ACCOUNT3_ASR_GUARD_V1 adds a one-shot retry for transcripts that
    look truncated or looped, and logs the WAV size sent (see _asr_guard).
    """
    audio_ctx = compute_audio_ctx(duration_seconds)
    args = [
        CURL_EXE, "-s", "-X", "POST", WHISPER_SERVER_URL,
        "-F", f"file=@{wav_path}",
        "-F", "response_format=json",
        "-F", f"audio_ctx={audio_ctx}",
    ]
    if ASR_PROMPT:  # ACCOUNT1_ASR_PROMPT_V1 (--form-string: no curl interpretation of the value; the guard retry reuses args)
        args += ["--form-string", "prompt=" + ASR_PROMPT]
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        start = time.monotonic()
        proc = _run_curl(args, timeout=30)
        wall_time = time.monotonic() - start
        if proc.returncode == 0:
            try:
                text = json.loads(proc.stdout)["text"].strip()
            except (json.JSONDecodeError, KeyError):
                last_exc = RuntimeError(f"Unexpected whisper-server response: {proc.stdout!r}")
            else:
                return _asr_guard(args, wav_path, duration_seconds, wall_time, text)
        else:
            last_exc = RuntimeError(f"curl failed (exit {proc.returncode}): {proc.stderr}")
        if attempt < retries:
            time.sleep(retry_delay)
    raise last_exc


def get_llm_reply(user_text: str, history: list[dict[str, str]]) -> tuple[float, str]:
    """POST the transcribed turn to llama-server, including recent history
    so replies are coherent across segments. Returns (wall_time_s, reply_text)."""
    messages = brain.build_messages(user_text)  # ACCOUNT2_BRAIN_V1 (history now lives in Brain)
    payload = json.dumps({
        "messages": messages,
        "max_tokens": LLM_MAX_TOKENS,
    })
    args = [
        CURL_EXE, "-s", "-X", "POST", LLAMA_SERVER_URL,
        "-H", "Content-Type: application/json", "-d", payload,
    ]
    start = time.monotonic()
    proc = _run_curl(args, timeout=60)
    wall_time = time.monotonic() - start
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed (exit {proc.returncode}): {proc.stderr}")
    try:
        _choice = json.loads(proc.stdout)["choices"][0]
        reply = _choice["message"]["content"].strip()
        _finish = _choice.get("finish_reason")
    except (json.JSONDecodeError, KeyError, IndexError):
        raise RuntimeError(f"Unexpected llama-server response: {proc.stdout!r}")
    reply, _trimmed = finish_reply(reply, _finish)  # ACCOUNT2_BRAIN_V1
    print(f"            [brain] llm finish_reason={_finish!r}, trimmed_to_last_sentence={_trimmed}, "
          f"history_msgs={len(messages) - 2}")
    return wall_time, reply


STREAM_REPLY = not __import__("os").environ.get("JARVIS_NO_STREAM")  # ACCOUNT2_STREAM_V1: JARVIS_NO_STREAM=1 restores the old path


def stream_llm_sentences(user_text: str, info: dict):
    """ACCOUNT2_STREAM_V1: ask llama-server for a streamed reply and yield it one whole sentence at a time.
    The curl.exe process is registered with turn_state, so a new turn terminates it like the old path."""
    messages = brain.build_messages(user_text)
    payload = json.dumps({"messages": messages, "max_tokens": LLM_MAX_TOKENS, "stream": True})
    args = [
        CURL_EXE, "-s", "-N", "-X", "POST", LLAMA_SERVER_URL,
        "-H", "Content-Type: application/json", "-d", payload,
    ]
    info.update(history_msgs=len(messages) - 2, first_token_s=None, finish_reason=None, dropped="", total_s=None)
    t0 = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    turn_state.register_proc(proc)
    watchdog = threading.Timer(90.0, proc.kill)
    watchdog.daemon = True
    watchdog.start()
    splitter = SentenceSplitter()
    pieces = 0
    other: list[str] = []
    try:
        for line in proc.stdout:
            delta, finish, done = parse_sse_line(line)
            if line.strip() and not line.lstrip().startswith("data:"):
                other.append(line.strip()[:200])
            if delta:
                pieces += 1
                if info["first_token_s"] is None:
                    info["first_token_s"] = time.monotonic() - t0
                for sentence in splitter.feed(delta):
                    yield sentence
            if finish:
                info["finish_reason"] = finish
            if done:
                break
        if pieces == 0:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise RuntimeError(f"llama-server stream gave no text (curl exit {proc.returncode}): "
                               f"{' | '.join(other)[:300]} {proc.stderr.read()[:200]}")
        for sentence in splitter.finish(info["finish_reason"]):
            yield sentence
        info["dropped"] = splitter.dropped
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.terminate()
        turn_state.register_proc(None)
        for stream_ in (proc.stdout, proc.stderr):
            try:
                stream_.close()
            except Exception:
                pass
        info["total_s"] = time.monotonic() - t0


def _stream_and_speak(user_text: str, llm_text: str, segment_index: int, segment_gen: int) -> None:
    """ACCOUNT2_STREAM_V1: speak each sentence as soon as it exists; stop at once when the turn is superseded.
    History records only what was actually spoken (plus the interrupted marker when the reply did not finish)."""
    info: dict = {}
    spoken: list[str] = []
    completed = False
    t_req = time.monotonic()
    t_first = None
    gen = stream_llm_sentences(llm_text, info)
    epoch = tts.begin_reply()
    try:
        for sentence in gen:
            if segment_gen < turn_state.current_generation():
                print(f"            [seg{segment_index}, gen{segment_gen}] reply stopped: superseded by "
                      f"gen{turn_state.current_generation()}")
                break
            if t_first is None:
                t_first = time.monotonic() - t_req
            print(f"            [seg{segment_index}] Jarvis: \"{sentence}\"")
            _tts_gate.begin()
            try:
                result = tts.speak(sentence, epoch=epoch)
            finally:
                _tts_gate.end()
            if result is not None and getattr(result, "skipped", False):
                print(f"            [seg{segment_index}] reply cancelled by the user; remaining sentences dropped")
                break
            spoken.append(sentence)
            if result is None or result.stopped_early:
                break
        else:
            completed = info.get("finish_reason") in ("stop", "length")  # a stream cut short is not a finished reply
    finally:
        gen.close()
        if spoken:
            brain.commit_turn(user_text, " ".join(spoken), interrupted=not completed)
        record = {
            "kind": "stream", "history_msgs": info.get("history_msgs"), "finish_reason": info.get("finish_reason"),
            "first_token_s": info.get("first_token_s"), "first_sentence_audio_s": t_first, "total_s": info.get("total_s"),
            "spoken_sentences": len(spoken), "completed": completed, "dropped_fragment": bool(info.get("dropped")),
        }
        print("            [brain] llm stream " + ", ".join(
            f"{k}={(round(v, 2) if isinstance(v, float) else v)!r}" for k, v in record.items() if k != "kind"))
        try:
            with open(brain.dir / "stream_log.jsonl", "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": brain.session_id, **record}) + "\n")
        except OSError:
            pass


def asr_llm_worker(
    work_q: "queue.Queue",
    segments_dir: Path,
    keep_segments: bool,
) -> None:
    """Runs on its own thread. Pulls completed segments and does ASR + LLM,
    fully decoupled from the live capture/VAD loop so listening never stops
    while a reply is being generated. Drops a segment the instant it's
    superseded by a newer SPEECH START - see TurnState above."""
    while True:
        item = work_q.get()
        if item is None:
            break
        segment_index, chunks, elapsed_end, segment_gen = item

        if segment_gen < turn_state.current_generation():
            print(f"            [seg{segment_index}, gen{segment_gen}] dropped before ASR "
                  f"(superseded by gen{turn_state.current_generation()})")
            work_q.task_done()
            continue

        duration = len(chunks) * CHUNK_SAMPLES / SAMPLE_RATE
        seg_path = (segments_dir if keep_segments else Path(".")) / \
            f"segment_{segment_index:04d}_{time.strftime('%H%M%S')}.wav"
        write_segment_wav(seg_path, chunks)
        try:
            wall_time, text = transcribe_segment(seg_path, duration)

            if segment_gen < turn_state.current_generation():
                print(f"            [seg{segment_index}, gen{segment_gen}] ASR done but discarded "
                      f"(superseded by gen{turn_state.current_generation()}): \"{text}\"")
                continue

            ctx = compute_audio_ctx(duration)
            print(f"            [{elapsed_end:7.2f}s seg{segment_index}] -> \"{text}\"  "
                  f"({wall_time:.3f}s, audio_ctx={ctx})")
            if not text.strip() or re.fullmatch(r'[\[\(][^\]\)]*[\]\)]', text.strip()):
                print(f"            [seg{segment_index}] (empty transcript - skipping LLM call)")
            else:
                _mem_reply = brain.try_memory_command(text)  # ACCOUNT2_BRAIN_V1: remember / forget
                if _mem_reply is not None:
                    print(f"            [seg{segment_index}] memory command -> \"{_mem_reply}\"")
                    _tts_gate.begin()
                    try:
                        tts.speak(_mem_reply)
                    finally:
                        _tts_gate.end()
                    continue
                _llm_text = text  # ACCOUNT3_TOOLS_V1: read-only coding tools (JARVIS_TOOLS=1)
                if code_tools is not None:
                    _tool_out = try_tool_command(code_tools, text)
                    if _tool_out is not None:
                        print(f"            [seg{segment_index}] tool -> {_tool_out[:200]!r}")
                        _llm_text = (text + "\n\n[Output of a read-only project tool. Answer briefly in one or two spoken sentences; "
                                     "do not read it out line by line.]\n" + _tool_out)
                if STREAM_REPLY:  # ACCOUNT2_STREAM_V1: speak each sentence as soon as it is generated
                    _stream_and_speak(text, _llm_text, segment_index, segment_gen)
                    continue
                llm_time, reply = get_llm_reply(_llm_text, conversation_history)

                if segment_gen < turn_state.current_generation():
                    print(f"            [seg{segment_index}, gen{segment_gen}] LLM replied but discarded "
                          f"(superseded by gen{turn_state.current_generation()}): \"{reply}\"")
                    continue

                print(f"            [seg{segment_index}] Jarvis: \"{reply}\"  ({llm_time:.3f}s)")
                # ACCOUNT2_BRAIN_V1: history is committed AFTER speaking so an interrupted reply is
                # recorded as interrupted (Brain.commit_turn), not as fully heard.
                # Speak output via the resident Piper TTS (cancellable by
                # the main thread's tts.stop() on the next SPEECH START).
                _tts_gate.begin()
                _spoke = None
                try:
                    _spoke = tts.speak(reply)
                finally:
                    _tts_gate.end()
                    brain.commit_turn(text, reply, interrupted=(_spoke is None or _spoke.stopped_early))
        except Exception as exc:
            if segment_gen < turn_state.current_generation():
                print(f"            [seg{segment_index}, gen{segment_gen}] cancelled "
                      f"(superseded by gen{turn_state.current_generation()}): {exc}")
            else:
                print(f"            [seg{segment_index}] -> [PIPELINE FAILED] {exc}", file=sys.stderr)
        finally:
            if not keep_segments:
                seg_path.unlink(missing_ok=True)
            work_q.task_done()


@dataclass
class StreamStats:
    chunks_seen: int = 0
    silent_chunks_run: int = 0
    warned_silence: bool = False
    start_time: float = 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis_Ted M2/M3/M4 - async streaming ASR + LLM reply + turn-cancellation")
    parser.add_argument("--device-name", default="Smart Sound")
    parser.add_argument("--hostapi", default="Windows WASAPI")
    parser.add_argument("--wasapi-auto-convert", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--keep-segments", action=argparse.BooleanOptionalAction, default=True,
        help="Save each transcribed segment's WAV under segments/ so transcripts can be checked "
             "against the real audio afterward - same reasoning as stream_vad_test.py's --record.",
    )
    parser.add_argument("--segments-dir", default="segments")
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--min-silence-ms", type=int, default=400)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument(
        "--merge-gap-ms", type=int, default=1400,  # ACCOUNT1_MERGE_GAP_V1 (was 800): Tedy pauses of 1.05-1.25 s split sentences
        help="After a VAD segment ends, hold it this long; speech resuming inside the window is "
             "merged into the same turn. 0 disables merging. Default 800 is an UNTESTED starting value.",
    )
    parser.add_argument(
        "--min-speech-ms", type=int, default=250,
        help="Turns with less estimated speech than this are dropped as noise/filler bursts "
             "(saved under segments/rejected/ for review). 0 disables. Default 250 is an UNTESTED starting value.",
    )
    args = parser.parse_args()

    device_index = find_input_device(args.device_name, args.hostapi)
    device_info = sd.query_devices(device_index)
    extra_settings, hostapi_name = build_extra_settings(device_index, args.wasapi_auto_convert)
    print(f"Using input device [{device_index}] {device_info['name']!r} (hostapi={hostapi_name})")

    segments_dir = Path(args.segments_dir)
    if args.keep_segments:
        segments_dir.mkdir(exist_ok=True)

    print("Loading Silero VAD model (ONNX backend)...")
    torch.set_num_threads(1)
    model = load_silero_vad(onnx=True)
    vad_iterator = VADIterator(
        model, sampling_rate=SAMPLE_RATE, threshold=args.threshold, min_silence_duration_ms=args.min_silence_ms
    )

    audio_q: "queue.Queue" = queue.Queue()
    work_q: "queue.Queue" = queue.Queue()

    worker = threading.Thread(
        target=asr_llm_worker, args=(work_q, segments_dir, args.keep_segments), daemon=True
    )
    worker.start()

    def audio_callback(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[stream status] {status}", file=sys.stderr)
        audio_q.put(indata.copy())

    stats = StreamStats(start_time=time.monotonic())
    print(f"Listening continuously at {SAMPLE_RATE} Hz mono, {CHUNK_SAMPLES}-sample chunks. "
          f"ASR/LLM/TTS run asynchronously with turn-cancellation on new speech. "
          f"Confirm whisper-server (8080) and llama-server (8081) are both already running. Ctrl+C to stop.\n")

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK_SAMPLES,
        device=device_index, extra_settings=extra_settings, callback=audio_callback,
    )

    segment_chunks: list[np.ndarray] = []
    # ACCOUNT1_AEC_V1 (2026-10-02): JARVIS_AEC=1 cleans each mic chunk with the TTS reference before
    # the speech detector (see tools\aec_live.py). Default off. If it cannot start: half-duplex gate.
    aec = None
    use_gate = HALF_DUPLEX
    pending_stop = False  # ACCOUNT1_AEC_GUARD_V1
    ONSET_GUARD_S = 3600.0  # ACCOUNT1_AEC_STOPDEFER_V1: every stop during a reply waits until the speech is confirmed (was 2.5 s)   # a burst this early in a reply is usually leftover echo
    if __import__("os").environ.get("JARVIS_AEC"):
        try:
            from aec_live import LiveAEC
            aec = LiveAEC(tts, mic_delay_ms=int(__import__("os").environ.get("JARVIS_AEC_MIC_DELAY_MS", "96")))
            use_gate = False
            print("[aec] echo cancellation ON (JARVIS_AEC=1): half-duplex gate off, microphone cleaned before the speech detector.")
        except Exception as exc:
            print(f"[aec] WARNING: could not start echo cancellation ({exc!r}); falling back to half-duplex.")
            aec = None
            use_gate = True
    in_segment = False
    segment_index = 0
    current_capture_generation = 0
    assembler = TurnAssembler(
        merge_gap_ms=args.merge_gap_ms, min_speech_ms=args.min_speech_ms,
        hangover_ms=args.min_silence_ms,
    )
    turn_confirmed = False
    # ACCOUNT1_STOPBURST_V1: while a reply plays, a stop needs ONE piece with this much speech (separate echo bursts
    # of 80-240 ms, up to 1.4 s apart, used to add up through the held turn and cut the reply). JARVIS_STOP_MIN_SPEECH_MS.
    STOP_MIN_SPEECH_MS = float(__import__("os").environ.get("JARVIS_STOP_MIN_SPEECH_MS", "350"))
    import collections as _collections  # ACCOUNT1_PREROLL_V1: JARVIS_PREROLL_MS (default 400, 0 disables)
    _preroll_ms = int(__import__("os").environ.get("JARVIS_PREROLL_MS", "400"))
    _preroll = _collections.deque(maxlen=max(0, int(round(_preroll_ms / (CHUNK_SAMPLES * 1000.0 / SAMPLE_RATE)))))
    rejected_count = 0
    rejected_dir = segments_dir / "rejected"
    print(f"Turn assembly: merge gap {args.merge_gap_ms} ms, min speech {args.min_speech_ms} ms "
          f"(0 disables either).")

    with stream:
        try:
            while args.max_seconds is None or (time.monotonic() - stats.start_time) < args.max_seconds:
                chunk = audio_q.get()
                stats.chunks_seen += 1
                raw_chunk = chunk
                if aec is not None:
                    chunk, aec_note = aec.process(chunk)
                    if aec_note:
                        print(aec_note)
                    # ACCOUNT1_AEC_LEAD_V1: with no reply playing, aec.process() returns the (delayed) microphone unchanged

                peak = int(np.max(np.abs(chunk)))
                if peak < NEAR_ZERO_PEAK:
                    stats.silent_chunks_run += 1
                else:
                    stats.silent_chunks_run = 0
                    stats.warned_silence = False
                silent_seconds = stats.silent_chunks_run * (CHUNK_SAMPLES / SAMPLE_RATE)
                if silent_seconds >= SILENCE_WARNING_SEC and not stats.warned_silence:
                    print(f"[WARNING] Input is digital silence (all samples zero) for {silent_seconds:.1f}s - microphone may be blocked or muted", file=sys.stderr)
                    stats.warned_silence = True

                if use_gate and not in_segment and _tts_gate.blocked():
                    vad_iterator.reset_states()
                    continue
                if assembler.collecting:
                    assembler.chunks.append(chunk.copy())

                turn = assembler.tick()
                if turn is not None:
                    elapsed = time.monotonic() - stats.start_time
                    turn_was_confirmed = turn_confirmed
                    turn_confirmed = False
                    if not turn.accepted:
                        rejected_count += 1
                        print(f"[{elapsed:7.2f}s] DROPPED short burst "
                              f"(~{turn.speech_ms:.0f}ms speech < --min-speech-ms {args.min_speech_ms})")
                        if args.keep_segments:
                            rejected_dir.mkdir(parents=True, exist_ok=True)
                            write_segment_wav(
                                rejected_dir / f"rejected_{rejected_count:04d}_{time.strftime('%H%M%S')}.wav",
                                turn.chunks,
                            )
                    else:
                        if not turn_was_confirmed:
                            current_capture_generation = turn_state.new_turn()
                        segment_index += 1
                        turn_audio = len(turn.chunks) * CHUNK_SAMPLES / SAMPLE_RATE
                        print(f"[{elapsed:7.2f}s] TURN READY    seg{segment_index} "
                              f"(gen{current_capture_generation}, {turn.pieces} piece(s), "
                              f"{turn_audio:.2f}s audio, ~{turn.speech_ms:.0f}ms speech)")
                        work_q.put((segment_index, turn.chunks, turn.end_elapsed, current_capture_generation))

                _vad_in = chunk if (aec is None or getattr(aec, "vad_pass", True)) else np.zeros_like(chunk)  # ACCOUNT1_AEC_GATE_V1
                chunk_float = torch.from_numpy(_vad_in.flatten().astype(np.float32) / 32768.0)
                event = vad_iterator(chunk_float, return_seconds=True)
                if event:
                    elapsed = time.monotonic() - stats.start_time
                    if "start" in event:
                        # Provisional start: cut TTS at once (barge-in path unchanged), but
                        # defer the turn-generation bump until the speech is confirmed to be
                        # at least --min-speech-ms long, so a noise burst cannot cancel a
                        # valid in-flight turn (see TurnAssembler and the confirm step below).
                        pending_stop = False
                        if not __import__("os").environ.get("JARVIS_NO_BARGE_IN"):
                            reply_age = aec.playback_age() if aec is not None else None
                            if reply_age is not None and reply_age < ONSET_GUARD_S:
                                pending_stop = True  # stop only once the speech is confirmed (not a short burst)
                                print(f"            (reply began {reply_age:.1f}s ago: stop deferred until speech is confirmed)")
                            else:
                                tts.stop()
                        print(f"[{elapsed:7.2f}s] SPEECH START")
                        in_segment = True
                        if assembler.start_piece(chunk, preroll=list(_preroll)):  # ACCOUNT1_PREROLL_V1
                            print(f"            (continues the held turn: pause shorter than "
                                  f"--merge-gap-ms {args.merge_gap_ms})")
                    if "end" in event:
                        piece_audio, piece_ms = assembler.end_piece(elapsed)
                        print(f"[{elapsed:7.2f}s] SPEECH END    ({piece_audio:.2f}s audio, "
                              f"~{piece_ms:.0f}ms speech) - holding for continuation")
                        in_segment = False
                        pending_stop = False

                if in_segment and not turn_confirmed and (  # ACCOUNT1_STOPBURST_V1
                        assembler.piece_speech_ms_so_far() >= STOP_MIN_SPEECH_MS if pending_stop
                        else assembler.speech_ms_so_far() >= args.min_speech_ms):
                    turn_confirmed = True
                    if pending_stop:
                        pending_stop = False
                        tts.stop()
                    if aec is not None:  # ACCOUNT1_AEC_SESSION_V1: the user has spoken, the reply session is over
                        aec.end_session()
                    current_capture_generation = turn_state.new_turn()
                    elapsed = time.monotonic() - stats.start_time
                    print(f"[{elapsed:7.2f}s] speech confirmed (gen{current_capture_generation})")
                _preroll.append(chunk)  # ACCOUNT1_PREROLL_V1: last chunks before the next VAD start
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            vad_iterator.reset_states()

    elapsed_total = time.monotonic() - stats.start_time
    audio_seconds = stats.chunks_seen * CHUNK_SAMPLES / SAMPLE_RATE
    pending = work_q.qsize()
    print(f"\nSession summary: {stats.chunks_seen} chunks over {elapsed_total:.1f}s wall time "
          f"({audio_seconds:.1f}s of audio), {segment_index} segments detected"
          + (f", {pending} still processing" if pending else "") + ".")

    tts.shutdown()


if __name__ == "__main__":
    main()