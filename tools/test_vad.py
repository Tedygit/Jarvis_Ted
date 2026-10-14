# C:\Jarvis_Ted\tools\test_vad.py
import time
import wave

import numpy as np
import torch
from silero_vad import load_silero_vad, get_speech_timestamps

WAV_PATH = "capture_test.wav"
SAMPLE_RATE = 16000

def load_wav_as_tensor(path: str) -> torch.Tensor:
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE, f"expected {SAMPLE_RATE} Hz, got {wf.getframerate()}"
        assert wf.getnchannels() == 1, "expected mono audio"
        assert wf.getsampwidth() == 2, "expected 16-bit PCM"
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return torch.from_numpy(samples)

def main() -> None:
    print("Loading Silero VAD model (ONNX backend)...")
    load_start = time.perf_counter()
    model = load_silero_vad(onnx=True)
    load_elapsed = time.perf_counter() - load_start
    print(f"Model load took {load_elapsed * 1000:.1f} ms")

    print(f"Reading {WAV_PATH} (via wave module, bypassing torchaudio's file I/O)...")
    wav = load_wav_as_tensor(WAV_PATH)

    start = time.perf_counter()
    timestamps = get_speech_timestamps(wav, model, sampling_rate=SAMPLE_RATE, return_seconds=True)
    elapsed = time.perf_counter() - start

    print(f"\nVAD inference took {elapsed * 1000:.1f} ms for {len(wav) / SAMPLE_RATE:.1f}s of audio")
    if not timestamps:
        print("No speech detected — unexpected, since this file is known to contain speech.")
    else:
        print(f"Detected {len(timestamps)} speech segment(s):")
        for seg in timestamps:
            print(f"  {seg['start']:.2f}s -> {seg['end']:.2f}s")

if __name__ == "__main__":
    main()