import queue, sys, threading, time
import numpy as np
import sounddevice as sd
import torch
from silero_vad import load_silero_vad
import stream_asr_test as s

SENTENCE = ("I am ready to assist you. Please tell me what you would like to do, "
            "and I will help you step by step with your project today.")

dev = s.find_input_device("Smart Sound", "Windows WASAPI")
extra, _ = s.build_extra_settings(dev, True)
model = load_silero_vad(onnx=True)
q = queue.Queue()

def cb(indata, frames, ti, status):
    q.put(indata.copy())

def run_phase(label, speak):
    model.reset_states()
    while not q.empty():
        q.get_nowait()
    done = threading.Event()
    def talk():
        try:
            s.tts.speak(SENTENCE)
        finally:
            done.set()
    if speak:
        threading.Thread(target=talk, daemon=True).start()
    else:
        threading.Timer(4.0, done.set).start()
    peaks, probs = [], []
    while not (done.is_set() and q.empty()):
        try:
            c = q.get(timeout=0.2)
        except queue.Empty:
            continue
        peaks.append(int(np.max(np.abs(c))))
        f = torch.from_numpy(c.flatten().astype(np.float32) / 32768.0)
        probs.append(float(model(f, 16000)))
    p = np.array(probs)
    k = np.array(peaks)
    print(f"{label}: chunks={len(p)} | VAD prob max={p.max():.3f} p95={np.percentile(p,95):.3f} "
          f"frac>0.5={np.mean(p>0.5):.2f} | peak max={k.max()} p95={int(np.percentile(k,95))}")

with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=512,
                    device=dev, extra_settings=extra, callback=cb):
    print("PHASE A: silence, stay quiet for 4 seconds...")
    run_phase("A silence      ", False)
    time.sleep(2)
    print("PHASE B: Jarvis speaks. STAY COMPLETELY SILENT.")
    run_phase("B echo only    ", True)
    time.sleep(3)
    print("PHASE C: Jarvis speaks. START COUNTING ALOUD NOW (one, two, three...) until it stops.")
    time.sleep(0.5)
    run_phase("C talk over TTS", True)

s.tts.shutdown()
print("DONE")