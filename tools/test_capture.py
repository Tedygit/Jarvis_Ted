# C:\Jarvis_Ted\tools\test_capture.py
import sounddevice as sd
import wave

DURATION_SEC = 5
SAMPLE_RATE = 16000   # target rate for the eventual ASR stage
CHANNELS = 1
OUT_PATH = "capture_test.wav"

def main() -> None:
    print("Available input devices:")
    print(sd.query_devices())

    default_in = sd.query_devices(kind="input")
    print(f"\nDefault input device: {default_in['name']}")
    print(f"PortAudio-reported default samplerate: {default_in['default_samplerate']} Hz")

    print(f"\nRecording {DURATION_SEC}s at {SAMPLE_RATE} Hz, {CHANNELS} channel(s) — speak now.")
    audio = sd.rec(int(DURATION_SEC * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()

    with wave.open(OUT_PATH, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    print(f"\nSaved {OUT_PATH} — {audio.shape[0]} samples captured, dtype={audio.dtype}")

if __name__ == "__main__":
    main()