r"""
C:\Jarvis_Ted\tools\asr_segment_latency_test.py

Measures REAL per-utterance ASR latency: slices known speech segments out of
an existing capture WAV, feeds each one to whisper-cli.exe individually, and
times each call end-to-end. This is the missing number for M2 -- how long a
single utterance actually takes from "VAD says the turn ended" to "text is
available" -- as opposed to inferring it from whole-file batch runs.

Segment boundaries below are Tedy's own manually-reviewed timestamps for
stream_capture_20260830_225417.wav, not raw Silero VAD output. Treat this as
a first-pass measurement on trusted data. The real next step after this is
wiring a whisper-cli call directly into stream_vad_test.py's own
segment-end event, using its actual detected boundaries.

Requires: Python 3.12 venv at C:\Jarvis_Ted\.venv (stdlib only).
"""

from __future__ import annotations

import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path

WHISPER_CLI: Path = Path(r"C:\tools\whisper.cpp\build\bin\Release\whisper-cli.exe")
WHISPER_MODEL: Path = Path(r"C:\tools\whisper.cpp\models\ggml-base.en.bin")
INPUT_WAV: Path = Path(r"C:\Jarvis_Ted\stream_capture_20260830_225417.wav")
TEMP_DIR: Path = Path(r"C:\Jarvis_Ted\tools\_asr_latency_tmp")
AUDIO_CTX: int | None = 512  # near the top, with the other constants

# Padding around each segment so whisper isn't fed an abruptly-cut edge.
PAD_SECONDS: float = 0.2

# (start_sec, end_sec, label) -- Tedy's own reviewed boundaries.
SEGMENTS: list[tuple[float, float, str]] = [
    (1.60, 2.64, "run the service"),
    (3.91, 4.95, "I mean the repository"),
    (6.00, 12.00, "stutter + are you following me + ...we-wel"),
    (46.27, 48.17, "what? a a are you waiting for me?"),
    (51.15, 51.99, "a a at when?"),
    (52.70, 56.23, "r you showing me the service? no I mean the repository"),
    (57.56, 58.70, "we...where is it?"),
]


@dataclass
class SegmentResult:
    label: str
    audio_duration_s: float
    asr_wall_time_s: float
    realtime_factor: float
    transcript: str


def slice_wav(src: Path, dst: Path, start_s: float, end_s: float, pad_s: float) -> float:
    """Extract [start_s - pad, end_s + pad] from src into dst. Returns clip duration in seconds."""
    with wave.open(str(src), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames_total = wf.getnframes()

        start_frame = max(0, int((start_s - pad_s) * rate))
        end_frame = min(n_frames_total, int((end_s + pad_s) * rate))
        if end_frame <= start_frame:
            raise ValueError(f"Empty or invalid range for segment [{start_s}, {end_s}]")

        wf.setpos(start_frame)
        frames = wf.readframes(end_frame - start_frame)

    with wave.open(str(dst), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sampwidth)
        out.setframerate(rate)
        out.writeframes(frames)

    return (end_frame - start_frame) / rate


def run_whisper(wav_path: Path) -> tuple[float, str]:
    """Run whisper-cli.exe on wav_path. Returns (wall_time_s, transcript_text)."""
    cmd = [
        str(WHISPER_CLI), "-m", str(WHISPER_MODEL), "-f", str(wav_path),
        "-bs", "1", "-bo", "1", "-t", "8", "-nt",  # -nt: plain text, no timestamps
    ]
    if AUDIO_CTX is not None:
        cmd += ["-ac", str(AUDIO_CTX)]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    wall_time = time.perf_counter() - t0

    if proc.returncode != 0:
        raise RuntimeError(f"whisper-cli failed (exit {proc.returncode}):\n{proc.stderr}")

    transcript = " ".join(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return wall_time, transcript


def main() -> None:
    for p in (WHISPER_CLI, WHISPER_MODEL, INPUT_WAV):
        if not p.exists():
            raise FileNotFoundError(f"Required path not found: {p}")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    results: list[SegmentResult] = []

    for i, (start_s, end_s, label) in enumerate(SEGMENTS, start=1):
        seg_path = TEMP_DIR / f"segment_{i:02d}.wav"
        duration = slice_wav(INPUT_WAV, seg_path, start_s, end_s, PAD_SECONDS)
        wall_time, transcript = run_whisper(seg_path)
        rtf = duration / wall_time if wall_time > 0 else float("inf")
        results.append(SegmentResult(label, duration, wall_time, rtf, transcript))
        print(f"[{i}/{len(SEGMENTS)}] {duration:.2f}s audio -> {wall_time:.3f}s ASR "
              f"(rtf={rtf:.2f}x) :: {transcript!r}")

    avg_wall = sum(r.asr_wall_time_s for r in results) / len(results)
    print("\n--- Summary ---")
    print(f"Segments tested: {len(results)}")
    print(f"Mean per-utterance ASR wall time: {avg_wall:.3f}s")
    print(f"Min / Max: {min(r.asr_wall_time_s for r in results):.3f}s / "
          f"{max(r.asr_wall_time_s for r in results):.3f}s")


if __name__ == "__main__":
    main()