import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper import PiperVoice

DEFAULT_VOICE_NAME = "en_US-lessac-medium"


def _resolve_voice_model(name: str = DEFAULT_VOICE_NAME) -> Path:
    candidates = [
        Path(r"C:\Jarvis_Ted") / f"{name}.onnx",
        Path(r"C:\Jarvis_Ted\models") / f"{name}.onnx",
        Path.home() / ".local" / "share" / "piper" / "voices" / f"{name}.onnx",
        Path.home() / "AppData" / "Local" / "piper" / f"{name}.onnx",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"Could not find {name}.onnx in any known location")


def make_tone_int16(freq: float, sr: int, duration: float = 1.2) -> bytes:
    t = np.linspace(0, duration, int(sr * duration), False)
    tone = (0.4 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    return tone.tobytes()


print("Loading Piper voice (one-time, ~5s)...")
voice = PiperVoice.load(str(_resolve_voice_model()))
sr = voice.config.sample_rate
print(f"Piper voice sample_rate = {sr}Hz\n")

candidates = [3, 8, 11, 13, 14]
tone = make_tone_int16(440, sr)
results = {}

print("=== RawOutputStream confirmation (matches tts_piper.py exactly) ===")
for i in candidates:
    name = sd.query_devices(i)["name"]
    input(f"[{i}] {name} -- ENTER to play via RawOutputStream at {sr}Hz: ")
    try:
        stream = sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16", device=i)
        stream.start()
        stream.write(tone)
        time.sleep(len(tone) / 2 / sr)
        stream.stop()
        stream.close()
    except Exception as e:
        print(f"    FAILED: {e}")
        results[i] = f"FAILED: {e}"
        continue
    ans = input(f"    Heard it on [{i}]? (y/n): ").strip().lower()
    results[i] = "HEARD" if ans.startswith("y") else "silent"
    print()

print("\n=== Summary ===")
for i in candidates:
    print(f"[{i}]: {results.get(i)}")

heard = [i for i in candidates if results.get(i) == "HEARD"]
print(f"\nRawOutputStream-confirmed audible index(es): {heard}")
