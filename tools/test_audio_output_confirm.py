import sounddevice as sd
import numpy as np

duration = 1.2

def make_tone(freq, sr):
    t = np.linspace(0, duration, int(sr * duration), False)
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

devices = sd.query_devices()
candidates = [i for i, d in enumerate(devices) if d["max_output_channels"] > 0]

results = {}

print("=== Per-device confirmed output test ===")
print("For each device: ENTER plays a 1.2s tone, then type y/n for whether you heard it.")
print("Type 's' instead of ENTER to skip a device.\n")

for i in candidates:
    d = devices[i]
    name = d["name"]
    sr = int(d["default_samplerate"]) if d["default_samplerate"] else 44100
    cmd = input(f"[{i}] {name} (sr={sr}Hz) -- ENTER to play, 's' to skip: ").strip().lower()
    if cmd == "s":
        results[i] = "skipped"
        continue
    try:
        sd.play(make_tone(440, sr), samplerate=sr, device=i)
        sd.wait()
    except Exception as e:
        print(f"    FAILED to open: {e}")
        results[i] = f"FAILED: {e}"
        continue
    ans = input(f"    Heard it on [{i}]? (y/n): ").strip().lower()
    results[i] = "HEARD" if ans.startswith("y") else "silent"
    print()

print("\n=== Summary ===")
for i in candidates:
    print(f"[{i}] {devices[i]['name']}: {results.get(i, 'not tested')}")

heard = [i for i in candidates if results.get(i) == "HEARD"]
print(f"\nAudible device index(es): {heard}")
