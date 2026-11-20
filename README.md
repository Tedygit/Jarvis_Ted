# Jarvis_Ted

A fully local, offline voice coding assistant: continuous listening, streaming
speech recognition, a self-hosted LLM, and streaming text-to-speech, with
conversational memory and optional read-only project tools — no cloud APIs
involved at any point in the pipeline.

Built for and verified on Windows 11. The project root is fixed at
`C:\Jarvis_Ted` throughout the code and launch scripts.

## Status (as of October 2026)

Current milestone: **M4 — streaming TTS + true barge-in/cancellation**. The
voice loop runs end-to-end in two supported modes:

| Mode | Voice interruption ("barge-in") | Status |
|---|---|---|
| Earphone, full duplex (`JARVIS_FULL_DUPLEX=1`) | Works | Verified live (two runs: 2 interruptions / 170 s, 3 interruptions / 427 s) |
| Laptop speakers, default half-duplex | Not possible by design | Verified — every reply plays in full, Jarvis never answers itself |
| Laptop speakers, experimental echo cancellation (`-Aec`) | Intended, not reliable | Mixed live results (1.6–8.9 dB cancellation vs. ~25 dB needed); not supported above ~60% speaker volume; see Known limitations |

The full, evidence-by-evidence history (every live run, benchmark, and
decision that led here) is in [`JARVIS_TED_STATUS.md`](JARVIS_TED_STATUS.md).
This README summarizes the current state; that file is the source of truth.

## Pipeline

```
Microphone
  -> Silero VAD (continuous listening, turn detection)
  -> whisper-server (streaming ASR, self-built whisper.cpp, ggml-small.en.bin)
  -> llama-server  (local LLM reply, self-built llama.cpp, Qwen2.5-Coder-3B-Instruct)
  -> Piper TTS (streaming, sentence-by-sentence)
  -> Speaker / earphone
```

A generation-counter turn-cancellation mechanism tracks the active turn, and
drops any in-flight ASR/LLM/TTS work for a turn the moment newer speech
supersedes it, instead of letting stale work finish.

## Features

- Continuous listening (no push-to-talk) with Silero VAD
- Streaming ASR via a self-built whisper.cpp server
- Local LLM replies via a self-built llama.cpp server, streamed sentence by
  sentence so TTS can start speaking before the full reply is generated
- Turn-cancellation: a new utterance cancels stale ASR/LLM/TTS work for the
  previous turn
- True voice barge-in — verified reliable with earphones; **not** reliably
  solved on laptop speakers (see Known limitations)
- Conversational memory (`tools/jarvis_brain.py`): recent history with
  cache-friendly batch eviction, durable facts via spoken "remember that …" /
  "forget everything", and a persistent conversation log resumed on restart
- Optional read-only coding tools (`tools/jarvis_tools.py`): list files, read
  a file range, search text, `git status` / `diff` / `log` — spoken commands,
  confined to the project root, off by default (`JARVIS_TOOLS=1` to enable)

## Milestones

| # | Milestone | State |
|---|---|---|
| M0 | Hardware/environment feasibility | Done |
| M1 | Audio capture + VAD | Done |
| M2 | Streaming ASR + turn detection | Done |
| M3 | Local LLM conversation | Done |
| M4 | Streaming TTS + barge-in | Done on earphones; laptop-speaker echo cancellation unsolved |
| M5 | Persistence | Done (verified live: spoken remember/forget, restart resume) |
| M6 | Coding context + local tools | Implemented, wired behind a flag, not yet exercised by voice |
| M7 | Reliability and memory refinement | Ongoing — see Known limitations |
| M8 | Final acceptance + offline verification + packaging | Not started |

## Requirements

Everything below is required to actually run the assistant; none of it is
vendored in this repository (see `.gitignore`).

- **Windows 11.** Paths, launch scripts, and device-selection logic are
  Windows-specific. A Debian 12 dual-boot exists on the development machine,
  but the pipeline has not been ported to it.
- **Python 3.12**, in a virtual environment at `.venv` inside the project
  root.
- **Python packages** (no `requirements.txt` is committed yet — install into
  the venv): `numpy`, `sounddevice`, `requests`, `torch` (CPU build is
  enough), `piper-tts` (provides the `piper` module), `silero-vad`, and
  optionally `pywebrtc-audio==0.2.0` for the experimental speaker
  echo-cancellation path (`tools/aec_live.py`).
- **whisper.cpp**, self-built (the verified build uses AVX-512) —
  <https://github.com/ggml-org/whisper.cpp>. Expected at
  `C:\tools\whisper.cpp\build\bin\Release\whisper-server.exe`.
- **llama.cpp**, self-built — <https://github.com/ggml-org/llama.cpp>.
  Expected at `C:\tools\llama.cpp\build\bin\Release\llama-server.exe`.
- **Model files** (download separately, not committed):
  - Whisper: `ggml-small.en.bin`, placed next to the whisper.cpp build.
  - LLM: `Qwen2.5-Coder-3B-Instruct-Q5_K_M.gguf`, placed in
    `C:\Jarvis_Ted\models\`.
  - TTS voice: Piper `en_US-lessac-medium`. This repo includes the voice's
    `en_US-lessac-medium.onnx.json` config, but not the matching `.onnx`
    weights file (binary model files are gitignored) — download it from
    <https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium>
    and place it at `C:\Jarvis_Ted\en_US-lessac-medium.onnx` (see
    `tools/tts_piper.py` for the full list of locations it checks).

Verified hardware baseline (not a hard minimum, just what this was built and
tested against): Intel i7-1195G7 (4C/8T, AVX-512), 16 GB RAM, Iris Xe
integrated graphics, ~40 GB free disk for models and logs.

## Running it

Two double-click launchers wrap the single PowerShell launcher:

- **`Jarvis_earphone.bat`** — plug in the earphone fully *before* starting;
  voice interruption works.
- **`Jarvis_speakers.bat`** — unplug the earphone before starting; replies
  play in full and cannot be interrupted by voice.

Both start `whisper-server` and `llama-server` if they are not already
running, then launch the assistant. Equivalent manual invocation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Jarvis_Ted\tools\start_jarvis.ps1 -Mode speakers
```

`start_jarvis.ps1` flags:

| Flag | Effect |
|---|---|
| `-Mode earphone\|speakers` | Required. Sets `JARVIS_FULL_DUPLEX` (earphone) or default half-duplex (speakers). |
| `-Aec` | Speakers only — enables the experimental echo canceller (`JARVIS_AEC=1`). Not supported above ~60% speaker volume; see Known limitations. |
| `-AecDump` | With `-Aec` — dumps the live reference/microphone signals for offline analysis. |
| `-AecMicDelayMs` | With `-Aec` — overrides the microphone-delay compensation (default 96 ms). |
| `-Tools` | Enables the read-only coding tools (`JARVIS_TOOLS=1`). |

Every `JARVIS_*` environment variable is cleared at the start of each launch,
so a value left over from a previous window cannot silently affect a run.

### Environment variables

Set directly if you run `tools/stream_asr_test.py` without the launcher:

| Variable | Purpose |
|---|---|
| `JARVIS_FULL_DUPLEX` | Enable full-duplex voice barge-in (for earphone use). |
| `JARVIS_NO_BARGE_IN` | Disable barge-in entirely. |
| `JARVIS_AEC`, `JARVIS_AEC_DUMP`, `JARVIS_AEC_DUMP_DIR`, `JARVIS_AEC_GATE_THETA`, `JARVIS_AEC_BLIND_S`, `JARVIS_AEC_MIC_DELAY_MS` | Experimental speaker echo cancellation (see `tools/aec_live.py`). |
| `JARVIS_ASR_PROMPT` | Override or disable (`off`) the Whisper initial prompt used to steer transcription. |
| `JARVIS_NO_ASR_GUARD` | Disable the transcript-length sanity guard. |
| `JARVIS_PREROLL_MS`, `JARVIS_STOP_MIN_SPEECH_MS` | Turn-assembly timing (pre-roll audio, minimum speech burst to count as a stop). |
| `JARVIS_NO_STREAM` | Disable sentence-by-sentence streamed replies. |
| `JARVIS_NO_PREWARM`, `JARVIS_NO_LLAMA_PRIORITY` | Disable LLM pre-warming / process-priority adjustment. |
| `JARVIS_TOOLS` | Enable the read-only coding tools. |
| `JARVIS_PROJECT_ROOT` | Override the project root used by the coding tools. |

## Known limitations

- **Voice barge-in through laptop speakers is not solved.** The experimental
  echo canceller (`JARVIS_AEC=1`) achieves 20–36 dB cancellation offline on
  recordings, but only 1.6–8.9 dB live, and at speaker volumes above ~60% it
  reliably fails (Jarvis answers its own voice). Until this is fixed, use
  earphones for barge-in, or accept half-duplex (no interruption) on
  speakers.
- Whisper occasionally mis-decodes a live segment that replays cleanly
  offline through the same server; the root cause is undetermined.
- `TurnAssembler` (merges pause-fragmented speech into one turn) has no
  regression test on the real machine; its timing defaults are untested
  starting values.
- The coding tools (M6) are implemented and wired in behind a flag, but have
  not yet been exercised through the live voice pipeline.
- Input device index is unstable depending on whether the earphone is
  connected; output device selection is more robust (by name).

The complete, dated list — with the live evidence behind each item — is in
the "Known Problems" section of `JARVIS_TED_STATUS.md`.

## Repository layout

```
Jarvis_Ted/
├── Jarvis_earphone.bat / Jarvis_speakers.bat   double-click launchers
├── JARVIS_TED_STATUS.md                        full project history and evidence log
├── jarvis_ted_m0_*.ps1                          one-time hardware/environment audit scripts
├── en_US-lessac-medium.onnx.json                Piper voice config (weights downloaded separately)
└── tools/
    ├── start_jarvis.ps1                         single launcher (both modes, env setup)
    ├── stream_asr_test.py                       main pipeline: capture, VAD, ASR, LLM, TTS, turn-cancellation
    ├── tts_piper.py                             resident, cancellable Piper TTS
    ├── aec_live.py                               experimental live echo cancellation
    ├── jarvis_brain.py                          conversation memory and prompt assembly
    ├── jarvis_stream.py / jarvis_speakable.py    streamed-reply sentence splitting / speech cleanup
    ├── jarvis_tools.py                          read-only coding tools (list/read/search/git)
    └── test_*.py, measure_*.py, diagnose_*.py    verification and diagnostic scripts used throughout development
```

## Development notes

This project was built iteratively through voice-pipeline changes proposed in
chat and verified live on the real machine before being recorded as working —
nothing in `JARVIS_TED_STATUS.md` is marked done without a live run or
offline test behind it. Three separated areas of responsibility (audio
capture/VAD/ASR; local LLM, TTS and memory; integration, tools and packaging)
were developed in parallel, with the status file as the single shared record
between them.

## License

No license file is included. All rights are reserved by default; add a
`LICENSE` file here if you want to state explicit reuse terms.
