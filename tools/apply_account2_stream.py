"""apply_account2_stream.py - Account 2 patcher (ACCOUNT2_EPOCH_V1 in tts_piper.py, ACCOUNT2_STREAM_V1 in stream_asr_test.py).
Idempotent. Every patch point must match exactly once, otherwise NOTHING is written to that file.
A backup (*.bak_prestream) is made first; a failed py_compile restores it. Usage: python apply_account2_stream.py [tools_dir]
"""
import py_compile
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Jarvis_Ted\tools")

TTS_EDITS = [
    ("        self._stop_event = threading.Event()\n",
     "        self._stop_event = threading.Event()\n"
     "        self._epoch = 0                        # ACCOUNT2_EPOCH_V1: bumped by every stop()\n"
     "        self._epoch_lock = threading.Lock()\n"),
    ("    elapsed_seconds: float   # wall time inside speak()\n",
     "    elapsed_seconds: float   # wall time inside speak()\n"
     "    skipped: bool = False    # ACCOUNT2_EPOCH_V1: True when the reply was cancelled before this sentence started\n"),
    ("debug: bool = False) -> \"SpeakResult\":\n",
     "debug: bool = False, epoch: Optional[int] = None) -> \"SpeakResult\":\n"),
    ("        t_entry = time.perf_counter()\n        self._stop_event.clear()\n        self._ensure_stream_active()\n",
     "        t_entry = time.perf_counter()\n"
     "        with self._epoch_lock:  # ACCOUNT2_EPOCH_V1: a sentence of a cancelled reply must not clear the stop flag\n"
     "            if epoch is not None and epoch != self._epoch:\n"
     "                print(\"[tts_piper] speak() skipped: the reply was cancelled before this sentence\")\n"
     "                return SpeakResult(stopped_early=True, bytes_written=0, audio_seconds=0.0,\n"
     "                                   elapsed_seconds=0.0, skipped=True)\n"
     "            self._stop_event.clear()\n"
     "        self._ensure_stream_active()\n"),
    ("    def stop(self) -> None:\n",
     "    def begin_reply(self) -> int:\n"
     "        \"\"\"ACCOUNT2_EPOCH_V1: call once before the first sentence of a reply and pass the value to every\n"
     "        speak(..., epoch=value). After any stop(), the remaining sentences of that reply are skipped.\"\"\"\n"
     "        with self._epoch_lock:\n"
     "            return self._epoch\n"
     "\n"
     "    def stop(self) -> None:\n"),
    ("        self._stop_event.set()\n        self._last_stop_monotonic = time.monotonic()\n",
     "        with self._epoch_lock:  # ACCOUNT2_EPOCH_V1\n"
     "            self._epoch += 1\n"
     "        self._stop_event.set()\n        self._last_stop_monotonic = time.monotonic()\n"),
]

STREAM_FUNCS = '''
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
            print(f"            [seg{segment_index}] Jarvis: \\"{sentence}\\"")
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
            with open(brain.dir / "stream_log.jsonl", "a", encoding="utf-8", newline="\\n") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": brain.session_id, **record}) + "\\n")
        except OSError:
            pass

'''

STREAM_EDITS = [
    ("from jarvis_brain import Brain, finish_reply  # ACCOUNT2_BRAIN_V1\n",
     "from jarvis_brain import Brain, finish_reply  # ACCOUNT2_BRAIN_V1\n"
     "from jarvis_stream import SentenceSplitter, parse_sse_line  # ACCOUNT2_STREAM_V1\n"),
    ("\ndef asr_llm_worker(\n", STREAM_FUNCS + "\ndef asr_llm_worker(\n"),
    ("                llm_time, reply = get_llm_reply(_llm_text, conversation_history)\n",
     "                if STREAM_REPLY:  # ACCOUNT2_STREAM_V1: speak each sentence as soon as it is generated\n"
     "                    _stream_and_speak(text, _llm_text, segment_index, segment_gen)\n"
     "                    continue\n"
     "                llm_time, reply = get_llm_reply(_llm_text, conversation_history)\n"),
]


def patch(name: str, marker: str, edits: list, tag: str) -> bool:
    path = ROOT / name
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if marker in text:
        print(f"{name}: already patched ({marker}) - skipped")
        return True
    for i, (old, _new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            print(f"PATCH POINT NOT UNIQUE/FOUND in {name}: edit {i} matched {n} time(s) - nothing written")
            return False
    backup = path.with_name(path.name + ".bak_" + tag)
    if not backup.exists():
        shutil.copy2(path, backup)
    for old, new in edits:
        text = text.replace(old, new, 1)
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(backup, path)
        print(f"{name}: py_compile FAILED, original restored from {backup.name}: {exc}")
        return False
    print(f"{name}: patched OK ({len(edits)} edits), backup {backup.name}")
    return True


ok_tts = patch("tts_piper.py", "ACCOUNT2_EPOCH_V1", TTS_EDITS, "prestream")
ok_stream = patch("stream_asr_test.py", "ACCOUNT2_STREAM_V1", STREAM_EDITS, "prestream") if ok_tts else False
print("PATCH RESULT:", "OK" if (ok_tts and ok_stream) else "INCOMPLETE - see lines above")
sys.exit(0 if (ok_tts and ok_stream) else 1)