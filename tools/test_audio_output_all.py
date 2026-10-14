import sounddevice as sd
import numpy as np
import time

sr = 22050
duration = 1.5

def make_tone(freq):
    t = np.linspace(0, duration, int(sr * duration), False)
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

devices = sd.query_devices()
candidates = [i for i, d in enumerate(devices) if d["max_output_channels"] > 0]

print("=== Testing each output device individually ===")
print("A tone will play on each device below, one at a time.")
print("Listen carefully and note EVERY index number where you hear it.\n")

for i in candidates:
    name = devices[i]["name"]
    print(f"--- [{i}] {name} --- playing now...")
    try:
        sd.play(make_tone(440), samplerate=sr, device=i)
        sd.wait()
    except Exception as e:
        print(f"    FAILED to play on device {i}: {e}")
    time.sleep(0.4)

print("\nDone. Report back every index number that produced an audible tone (or confirm none did).")
