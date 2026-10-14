import sounddevice as sd
import numpy as np

print("=== Available output devices ===")
for i, dev in enumerate(sd.query_devices()):
    if dev["max_output_channels"] > 0:
        marker = " <-- DEFAULT" if i == sd.default.device[1] else ""
        print(f"[{i}] {dev['name']}  (max_output_channels={dev['max_output_channels']}){marker}")

print()
print("Playing a 2-second test tone on the DEFAULT output device now...")
sr = 22050
t = np.linspace(0, 2, int(sr * 2), False)
tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
sd.play(tone, samplerate=sr)
sd.wait()
print("Done. Did you hear a 2-second tone?")
