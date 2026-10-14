"""
tools/test_tts_with_mic.py -- isolates whether having the mic InputStream
open is what breaks CancellableTTS.speak()'s audio in the live pipeline.

Part 1: speak() with no mic stream open (should match test_tts_e2e.py).
Part 2: speak() with the mic InputStream open, same config as
        stream_asr_test.py, running concurrently.

No whisper-server / llama-server needed.

Run:
    cd C:\Jarvis_Ted
    .\.venv\Scripts\python.exe tools\test_tts_with_mic.py
"""
from __future__ import annotations

import numpy as np
import sounddevice as sd

from tts_piper import CancellableTTS

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 512

TEST_SENTENCE = (
    "This is a longer test sentence so there is enough time to clearly "
    "notice whether audio is actually playing out loud right now."
)


def find_input_device(name_substring: str, preferred_hostapi: str) -> int:
    """Copied unchanged from stream_asr_test.py (verified 2026-08-30/31)."""
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
        )
    if preferred_hostapi_index is not None:
        for idx, dev in matches:
            if dev["hostapi"] == preferred_hostapi_index:
                return idx
    return matches[0][0]


def build_extra_settings(device_index: int, auto_convert: bool):
    """Copied unchanged from stream_asr_test.py (verified 2026-08-30)."""
    device_info = sd.query_devices(device_index)
    hostapi_name = sd.query_hostapis()[device_info["hostapi"]]["name"]
    if hostapi_name == "Windows WASAPI" and auto_convert:
        return sd.WasapiSettings(auto_convert=True), hostapi_name
    return None, hostapi_name


def silent_callback(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
    # Deliberately does nothing -- this test only cares whether an ACTIVE
    # input stream, by itself, breaks TTS output.
    pass


def main() -> None:
    print("Loading TTS (also opens the resident output stream on device=3)...")
    tts = CancellableTTS()

    print("\n" + "=" * 60)
    print("PART 1 of 2: speak() with NO microphone stream open.")
    print("Should sound identical to test_tts_e2e.py (known-good).")
    input("Press Enter when ready to listen...")
    tts.speak(TEST_SENTENCE)
    heard_part1 = input("Did you hear that sentence spoken out loud? (y/n): ").strip().lower()

    print("\n" + "=" * 60)
    print("PART 2 of 2: speak() with the mic InputStream open and running,")
    print("using the exact same device/config as stream_asr_test.py.")
    device_index = find_input_device("Smart Sound", "Windows WASAPI")
    device_info = sd.query_devices(device_index)
    extra_settings, hostapi_name = build_extra_settings(device_index, auto_convert=True)
    print(f"Opening input device [{device_index}] {device_info['name']!r} (hostapi={hostapi_name})...")

    input_stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK_SAMPLES,
        device=device_index, extra_settings=extra_settings, callback=silent_callback,
    )
    with input_stream:
        print("Mic stream is now open and running (same as a live session).")
        input("Press Enter when ready to listen...")
        tts.speak(TEST_SENTENCE)
    heard_part2 = input("Did you hear that sentence spoken out loud? (y/n): ").strip().lower()

    print("\n" + "=" * 60)
    print(f"RESULT -- Part 1 (no mic open):  heard = {heard_part1}")
    print(f"RESULT -- Part 2 (mic open):     heard = {heard_part2}")
    if heard_part1 == "y" and heard_part2 == "n":
        print("Confirms: an active mic InputStream is what breaks TTS output.")
    elif heard_part1 == "n" and heard_part2 == "n":
        print("Silent even in isolation here -- points away from the mic-stream")
        print("theory and back toward device=3 / environment state.")
    elif heard_part1 == "y" and heard_part2 == "y":
        print("Both worked -- the mic stream alone isn't the cause; the")
        print("stop()/abort() threading race becomes the next suspect.")
    else:
        print("Unexpected pattern -- worth a re-run before drawing a conclusion.")

    tts.shutdown()


if __name__ == "__main__":
    main()
