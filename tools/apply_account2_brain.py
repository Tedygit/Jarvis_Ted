"""apply_account2_brain.py - Account 2 patcher (ACCOUNT2_BRAIN_V1 / ACCOUNT2_SPEAKRESULT_V1).
Idempotent: skips a file that already carries its marker. Every patch point must match exactly once,
otherwise nothing is written to that file. A backup is made first; a failed py_compile restores it.
Usage: python apply_account2_brain.py [tools_dir]
"""
import py_compile
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Jarvis_Ted\tools")

TTS_EDITS = [
    ("\nimport collections\n",
     "\nimport collections\nfrom dataclasses import dataclass  # ACCOUNT2_SPEAKRESULT_V1\n"),
    ("\nclass CancellableTTS:\n",
     "\n@dataclass\n"
     "class SpeakResult:  # ACCOUNT2_SPEAKRESULT_V1: what speak() returns (it returned None before)\n"
     "    stopped_early: bool      # True if stop() / a write failure ended playback before the end\n"
     "    bytes_written: int       # PCM bytes handed to the output stream\n"
     "    audio_seconds: float     # bytes_written expressed as seconds of audio\n"
     "    elapsed_seconds: float   # wall time inside speak()\n"
     "\n\nclass CancellableTTS:\n"),
    ("debug: bool = False) -> None:\n",
     "debug: bool = False) -> \"SpeakResult\":\n"),
    ("              f\"stop_event_set={self._stop_event.is_set()}\")\n",
     "              f\"stop_event_set={self._stop_event.is_set()}\")\n"
     "        return SpeakResult(stopped_early=stopped_early, bytes_written=bytes_written,\n"
     "                           audio_seconds=bytes_written / 2 / self.sample_rate,\n"
     "                           elapsed_seconds=elapsed)\n"),
]

STREAM_EDITS = [
    ("\nfrom tts_piper import CancellableTTS\n",
     "\nfrom tts_piper import CancellableTTS\n"
     "from jarvis_brain import Brain, finish_reply  # ACCOUNT2_BRAIN_V1\n"),
    ("\ntts = CancellableTTS()\n",
     "\ntts = CancellableTTS()\n"
     "brain = Brain()  # ACCOUNT2_BRAIN_V1: prompt assembly, history, persistence (tools\\jarvis_brain.py)\n"),
    ("    messages = [\n"
     "        {\"role\": \"system\", \"content\": SYSTEM_PROMPT},\n"
     "        *history,\n"
     "        {\"role\": \"user\", \"content\": user_text},\n"
     "    ]\n",
     "    messages = brain.build_messages(user_text)  # ACCOUNT2_BRAIN_V1 (history now lives in Brain)\n"),
    ("    try:\n"
     "        reply = json.loads(proc.stdout)[\"choices\"][0][\"message\"][\"content\"].strip()\n"
     "    except (json.JSONDecodeError, KeyError, IndexError):\n"
     "        raise RuntimeError(f\"Unexpected llama-server response: {proc.stdout!r}\")\n"
     "    return wall_time, reply\n",
     "    try:\n"
     "        _choice = json.loads(proc.stdout)[\"choices\"][0]\n"
     "        reply = _choice[\"message\"][\"content\"].strip()\n"
     "        _finish = _choice.get(\"finish_reason\")\n"
     "    except (json.JSONDecodeError, KeyError, IndexError):\n"
     "        raise RuntimeError(f\"Unexpected llama-server response: {proc.stdout!r}\")\n"
     "    reply, _trimmed = finish_reply(reply, _finish)  # ACCOUNT2_BRAIN_V1\n"
     "    print(f\"            [brain] llm finish_reason={_finish!r}, trimmed_to_last_sentence={_trimmed}, \"\n"
     "          f\"history_msgs={len(messages) - 2}\")\n"
     "    return wall_time, reply\n"),
    ("            else:\n"
     "                llm_time, reply = get_llm_reply(text, conversation_history)\n",
     "            else:\n"
     "                _mem_reply = brain.try_memory_command(text)  # ACCOUNT2_BRAIN_V1: remember / forget\n"
     "                if _mem_reply is not None:\n"
     "                    print(f\"            [seg{segment_index}] memory command -> \\\"{_mem_reply}\\\"\")\n"
     "                    _tts_gate.begin()\n"
     "                    try:\n"
     "                        tts.speak(_mem_reply)\n"
     "                    finally:\n"
     "                        _tts_gate.end()\n"
     "                    continue\n"
     "                llm_time, reply = get_llm_reply(text, conversation_history)\n"),
    ("                conversation_history.append({\"role\": \"user\", \"content\": text})\n"
     "                conversation_history.append({\"role\": \"assistant\", \"content\": reply})\n"
     "                if len(conversation_history) > MAX_HISTORY_TURNS * 2:\n"
     "                    conversation_history[:] = conversation_history[-RESET_KEEP_TURNS * 2:]\n"
     "\n"
     "                # Speak output via the resident Piper TTS (cancellable by\n"
     "                # the main thread's tts.stop() on the next SPEECH START).\n"
     "                _tts_gate.begin()\n"
     "                try:\n"
     "                    tts.speak(reply)\n"
     "                finally:\n"
     "                    _tts_gate.end()\n",
     "                # ACCOUNT2_BRAIN_V1: history is committed AFTER speaking so an interrupted reply is\n"
     "                # recorded as interrupted (Brain.commit_turn), not as fully heard.\n"
     "                # Speak output via the resident Piper TTS (cancellable by\n"
     "                # the main thread's tts.stop() on the next SPEECH START).\n"
     "                _tts_gate.begin()\n"
     "                _spoke = None\n"
     "                try:\n"
     "                    _spoke = tts.speak(reply)\n"
     "                finally:\n"
     "                    _tts_gate.end()\n"
     "                    brain.commit_turn(text, reply, interrupted=(_spoke is None or _spoke.stopped_early))\n"),
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


ok_tts = patch("tts_piper.py", "ACCOUNT2_SPEAKRESULT_V1", TTS_EDITS, "prebrain")
ok_stream = patch("stream_asr_test.py", "ACCOUNT2_BRAIN_V1", STREAM_EDITS, "prebrain")
print("PATCH RESULT:", "OK" if (ok_tts and ok_stream) else "INCOMPLETE - see lines above")
sys.exit(0 if (ok_tts and ok_stream) else 1)