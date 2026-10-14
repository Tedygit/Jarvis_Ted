"""
Jarvis_Ted - M1: continuous audio capture + streaming VAD loop.

Replaces the earlier fixed-duration record-then-analyze tests
(capture_test.wav, batch VAD analysis) with a live, indefinitely-running
capture loop that reports speech START/END events as they happen. This is
the concrete test for M1's continuous-listening acceptance criteria
(continuous listening, tolerance for pauses/hesitation/self-correction, no
forced push-to-talk) - the one-shot tests proved the two components work in
isolation but not their live behavior.

2026-08-30 fix #1 (RESOLVED, evidenced by a real run): the first run failed
immediately with
    sounddevice.PortAudioError: Error opening InputStream: Invalid sample rate [PaErrorCode -9997]
Root cause: WASAPI *shared-mode* streams must match the audio engine's
configured mix format (this device's WASAPI mix format is 48000 Hz per the
M0 audit) unless PortAudio's WASAPI auto-convert flag is enabled. Fixed via
extra_settings=sd.WasapiSettings(auto_convert=True) whenever the resolved
device's host API is WASAPI. Confirmed working on a real run: stream opened,
ran continuously for 34.8s wall time / 34.5s of audio (1077 chunks - the
close match between wall time and audio duration is itself evidence against
dropped/starved chunks), 10 START/END event pairs printed, no crash.

2026-08-30 fix #2 (NOT YET VERIFIED): that same run printed START/END
timestamps but saved no audio anywhere, so there was no way to independently
check whether the printed boundaries actually matched what was said and when
- only live, real-time subjective impression, which is weaker evidence than
being able to play the segment back afterward. This script now also writes
everything captured to a WAV file (stdlib `wave` module, matching the
project's existing WAV-handling decision) time-aligned with the printed
timestamps, specifically so the next run can be checked against a real
recording rather than memory alone.

Run (from the project's Python 3.12 venv):
    C:\\Jarvis_Ted\\.venv\\Scripts\\python.exe stream_vad_test.py

If the stream still fails to open, fall back to the host API already proven
(2026-08-30, capture_test.wav) to accept 16 kHz mono directly on this exact
device:
    C:\\Jarvis_Ted\\.venv\\Scripts\\python.exe stream_vad_test.py --hostapi MME

Stop:
    Ctrl+C

While it runs:
    Speak naturally. Include short pauses, filler/hesitation ("uh", "um"),
    and at least one self-correction ("open the service - actually, the
    repository"). After stopping, play back the saved WAV file and check the
    printed START/END lines against what you actually said and where you
    actually paused - report both the console output and that comparison
    back, not just "it worked."
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
import wave
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
import torch
from silero_vad import VADIterator, load_silero_vad

SAMPLE_RATE = 16_000        # matches the already-verified capture_test.wav and Silero's native rate
CHUNK_SAMPLES = 512         # Silero's required chunk size at 16 kHz (32 ms/chunk) - see references/architecture.md
SILENCE_WARNING_SEC = 3.0   # watchdog: warn if input stays ~silent this long (the 2026-08-30 permission bug produced exactly this symptom)
NEAR_ZERO_PEAK = 50         # int16 peak below this counts as "silent" for the watchdog only, not for VAD itself


def find_input_device(name_substring: str, preferred_hostapi: str) -> int:
    """
    Resolve the mic by name substring + preferred host API rather than a
    raw device index. The known-problems log shows indices shift whenever
    device enumeration changes (observed mid-troubleshooting on 2026-08-30) -
    a long-running continuous process is exactly where that bites hardest.
    """
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
            f"  [{i}] {d['name']}" for i, d in enumerate(all_devices) if d["max_input_channels"] > 0
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
    """
    WASAPI shared-mode streams must match the audio engine's mix format
    unless the WASAPI-specific auto-convert flag is set - confirmed 2026-08-30
    to turn the 'Invalid sample rate [PaErrorCode -9997]' failure into a
    working 16 kHz mono stream on this device. Only apply this on WASAPI:
    other host APIs (MME, DirectSound) don't take a WasapiSettings object and
    already do their own resampling - MME already proved this directly on
    this device on 2026-08-30 with capture_test.wav.
    """
    device_info = sd.query_devices(device_index)
    hostapi_name = sd.query_hostapis()[device_info["hostapi"]]["name"]
    if hostapi_name == "Windows WASAPI" and auto_convert:
        return sd.WasapiSettings(auto_convert=True), hostapi_name
    return None, hostapi_name


@dataclass
class StreamStats:
    chunks_seen: int = 0
    silent_chunks_run: int = 0
    warned_silence: bool = False
    start_time: float = 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis_Ted M1 - continuous capture + streaming VAD")
    parser.add_argument("--device-name", default="Smart Sound", help="Substring to match the input device name")
    parser.add_argument("--hostapi", default="Windows WASAPI", help="Preferred host API name")
    parser.add_argument(
        "--wasapi-auto-convert", action=argparse.BooleanOptionalAction, default=True,
        help="On WASAPI, ask PortAudio to insert Windows' own sample-rate/channel converter "
             "(fixes 'Invalid sample rate' when the device's mix format isn't 16 kHz mono). "
             "Confirmed working 2026-08-30. Use --no-wasapi-auto-convert for a later latency comparison.",
    )
    parser.add_argument(
        "--record", action=argparse.BooleanOptionalAction, default=True,
        help="Save everything captured to a WAV file, time-aligned with the printed START/END "
             "timestamps, so the run can be checked against a real recording afterward instead of "
             "only live impression. Use --no-record to disable.",
    )
    parser.add_argument(
        "--record-path", default=None,
        help="WAV output path. Defaults to stream_capture_<timestamp>.wav in the current directory.",
    )
    parser.add_argument("--max-seconds", type=float, default=None, help="Optional run limit; omit to run until Ctrl+C")
    parser.add_argument(
        "--min-silence-ms", type=int, default=400,
        help="Silence duration before an END event fires. Silero's library default is 100ms, which is "
             "likely too aggressive for natural thinking pauses; 400ms is a starting point to tune from "
             "what you actually observe.",
    )
    parser.add_argument("--threshold", type=float, default=0.5, help="Speech-probability threshold (Silero default 0.5)")
    args = parser.parse_args()

    device_index = find_input_device(args.device_name, args.hostapi)
    device_info = sd.query_devices(device_index)
    extra_settings, hostapi_name = build_extra_settings(device_index, args.wasapi_auto_convert)
    print(f"Using input device [{device_index}] {device_info['name']!r} (hostapi={hostapi_name})")
    if hostapi_name == "Windows WASAPI":
        print(f"WASAPI auto-convert: {'ON (rate/channel conversion delegated to Windows)' if extra_settings else 'OFF (raw device format required)'}")

    record_path = None
    wav_writer = None
    if args.record:
        record_path = args.record_path or f"stream_capture_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        wav_writer = wave.open(record_path, "wb")
        wav_writer.setnchannels(1)
        wav_writer.setsampwidth(2)  # int16
        wav_writer.setframerate(SAMPLE_RATE)
        print(f"Recording everything captured to {record_path} - play this back afterward and compare "
              f"it against the printed START/END timestamps below.")

    print("Loading Silero VAD model (ONNX backend)...")
    torch.set_num_threads(1)  # VAD inference is cheap; don't compete with the LLM's threads later
    model = load_silero_vad(onnx=True)
    vad_iterator = VADIterator(
        model, sampling_rate=SAMPLE_RATE, threshold=args.threshold, min_silence_duration_ms=args.min_silence_ms
    )

    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def audio_callback(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        # Keep this callback cheap - no VAD inference or file I/O here. Just copy and hand off.
        if status:
            print(f"[stream status] {status}", file=sys.stderr)
        audio_q.put(indata.copy())

    stats = StreamStats(start_time=time.monotonic())
    print(f"Listening continuously at {SAMPLE_RATE} Hz mono, {CHUNK_SAMPLES}-sample chunks. "
          f"Speak naturally. Ctrl+C to stop.\n")

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            device=device_index,
            extra_settings=extra_settings,
            callback=audio_callback,
        )
    except sd.PortAudioError as e:
        print(f"\nFailed to open the input stream: {e}", file=sys.stderr)
        if hostapi_name == "Windows WASAPI":
            print(
                "If --wasapi-auto-convert is already on (it is by default) and this still fails, fall "
                "back to the host API already proven to accept 16 kHz mono directly on this device: "
                "rerun with --hostapi MME (see capture_test.wav in the status file's benchmark table).",
                file=sys.stderr,
            )
        if wav_writer is not None:
            wav_writer.close()
        raise

    with stream:
        try:
            while args.max_seconds is None or (time.monotonic() - stats.start_time) < args.max_seconds:
                chunk = audio_q.get()
                stats.chunks_seen += 1

                if wav_writer is not None:
                    wav_writer.writeframes(chunk.tobytes())

                peak = int(np.max(np.abs(chunk)))
                if peak < NEAR_ZERO_PEAK:
                    stats.silent_chunks_run += 1
                else:
                    stats.silent_chunks_run = 0
                    stats.warned_silence = False

                silent_seconds = stats.silent_chunks_run * (CHUNK_SAMPLES / SAMPLE_RATE)
                if silent_seconds >= SILENCE_WARNING_SEC and not stats.warned_silence:
                    print(
                        f"[WARNING] No audible input for {silent_seconds:.1f}s - check the mic's "
                        f"'Allow apps and Windows to use this device for audio' setting "
                        f"(this exact symptom was the 2026-08-30 root cause).",
                        file=sys.stderr,
                    )
                    stats.warned_silence = True

                chunk_float = torch.from_numpy(chunk.flatten().astype(np.float32) / 32768.0)
                event = vad_iterator(chunk_float, return_seconds=True)
                if event:
                    elapsed = time.monotonic() - stats.start_time
                    if "start" in event:
                        print(f"[{elapsed:7.2f}s] SPEECH START  (vad_t={event['start']}s)")
                    if "end" in event:
                        print(f"[{elapsed:7.2f}s] SPEECH END    (vad_t={event['end']}s)")
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            vad_iterator.reset_states()
            if wav_writer is not None:
                wav_writer.close()

    elapsed_total = time.monotonic() - stats.start_time
    audio_seconds = stats.chunks_seen * CHUNK_SAMPLES / SAMPLE_RATE
    print(f"\nSession summary: {stats.chunks_seen} chunks processed over {elapsed_total:.1f}s wall time "
          f"({audio_seconds:.1f}s of audio).")
    if record_path is not None:
        print(f"Saved recording: {record_path} ({audio_seconds:.1f}s) - play it back and compare against "
              f"the START/END timestamps above.")


if __name__ == "__main__":
    main()
