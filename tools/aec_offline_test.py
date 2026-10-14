"""aec_offline_test.py - does WebRTC echo cancellation remove Jarvis own voice on the laptop speakers?
(Account 3, 2026-10-02). Plays 4 sentences through CancellableTTS while recording the mic, builds a
reference track from the TTS tap aligned to the measured echo delay, runs pywebrtc_audio offline, and
prints echo level before and after (plus a control run with a silent reference). Saves the WAVs to
C:\\Jarvis_Ted\\aec_test so tuning can continue offline. Earphone must be UNPLUGGED and the room quiet."""
import os
import sys
import time
import wave

import numpy as np
import sounddevice as sd

SR = 16000
FRAME = 160
CH = 512
NAME = "Smart Sound"
API = "Windows WASAPI"
OUT_DIR = r"C:\Jarvis_Ted\aec_test"
SENTENCES = [
    "This is the first sentence for testing the echo canceller on the laptop speakers.",
    "Here is a second, slightly longer sentence so the canceller has time to adapt to the room.",
    "The third sentence keeps going for a little while to measure how much of the echo is removed.",
    "And this is the fourth and last sentence of the test.",
]
SPEECH_REF = "median 452, p95 1526 (own speech, earphone connected, input index 17)"


def find_input_device(name_substring, preferred_hostapi):
    hostapis = sd.query_hostapis()
    pref = next((i for i, a in enumerate(hostapis) if a["name"] == preferred_hostapi), None)
    matches = [(i, d) for i, d in enumerate(sd.query_devices())
               if d["max_input_channels"] > 0 and name_substring.lower() in d["name"].lower()]
    if not matches:
        raise RuntimeError("No input device matched " + name_substring)
    if pref is not None:
        for i, d in matches:
            if d["hostapi"] == pref:
                return i
    return matches[0][0]


def envelope(x):
    n = len(x) // FRAME
    f = x[: n * FRAME].astype(np.float64).reshape(n, FRAME)
    return np.sqrt((f ** 2).mean(axis=1))


def to_16k(pcm_bytes, rate):
    x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
    if rate == SR:
        return x
    idx = np.arange(int(len(x) * SR / rate)) * (rate / SR)
    return np.interp(idx, np.arange(len(x)), x)


def chunk_peaks(x):
    n = len(x) // CH
    if n == 0:
        return np.array([0])
    return np.abs(x[: n * CH].astype(np.int32)).reshape(n, CH).max(axis=1)


def estimate_delay(tap, mic_all, t0, t_start, t_end, t_tap0):
    s0 = max(0, int((t_start - 0.2 - t0) * SR))
    s1 = min(len(mic_all), int((t_end - t0) * SR))
    mic = mic_all[s0:s1]
    t_w0 = t0 + s0 / SR
    a, b = envelope(tap), envelope(mic)
    if len(b) <= len(a):
        return None
    a0, b0 = a - a.mean(), b - b.mean()
    c = np.correlate(b0, a0, mode="valid")
    csum = np.cumsum(np.concatenate(([0.0], b0 ** 2)))
    win = csum[len(a):] - csum[:-len(a)]
    score = c / (np.sqrt((a0 ** 2).sum() * win) + 1e-12)
    k = int(np.argmax(score))
    return (t_w0 + k * 0.01) - t_tap0, float(score[k])


def save_wav(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())


def run_aec(AudioProcessor, near, far):
    ap = AudioProcessor(sample_rate=SR, echo_cancellation=True, noise_suppression=False,
                        auto_gain_control=False, stream_delay_ms=0)
    return np.asarray(ap.process(near.astype(np.int16), far.astype(np.int16))).astype(np.int16)


def db(raw, other):
    return 20.0 * np.log10((np.sqrt(np.mean(raw.astype(np.float64) ** 2)) + 1e-9) /
                           (np.sqrt(np.mean(other.astype(np.float64) ** 2)) + 1e-9))


def pk(x):
    p = chunk_peaks(x)
    return "p50=%d p95=%d max=%d" % (int(np.median(p)), int(np.percentile(p, 95)), int(p.max()))


def main():
    sys.path.insert(0, r"C:\Jarvis_Ted\tools")
    try:
        from pywebrtc_audio import AudioProcessor
    except Exception as exc:
        print("IMPORT FAILED: pywebrtc_audio: %r" % (exc,))
        return 2
    from tts_piper import CancellableTTS

    idx = find_input_device(NAME, API)
    info = sd.query_devices(idx)
    api_name = sd.query_hostapis()[info["hostapi"]]["name"]
    extra = sd.WasapiSettings(auto_convert=True) if api_name == "Windows WASAPI" else None
    print("Using input device [%d] %r" % (idx, info["name"]))
    tts = CancellableTTS()
    tinfo = tts.tap_info()
    out_name = sd.query_devices(tinfo["output_device_index"])["name"]
    print("Output device [%d] %r" % (tinfo["output_device_index"], out_name))
    if "headphone" in out_name.lower() or idx == 17:
        print("WARNING: NOT A VALID SPEAKER TEST - the earphone looks connected (input %d, output %r)." % (idx, out_name))

    blocks, first, trials = [], {}, []

    def cb(indata, frames, time_info, status):
        if "t" not in first:
            first["t"], first["n"] = time.monotonic(), frames
        blocks.append(indata[:, 0].copy())

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CH,
                        device=idx, extra_settings=extra, callback=cb):
        time.sleep(1.0)
        for n, text in enumerate(SENTENCES, 1):
            t_start = time.monotonic()
            tts.speak(text)
            time.sleep(1.2)
            t_end = time.monotonic()
            pieces = tts.tap_read(since=t_start)
            mic_now = np.concatenate(list(blocks))
            t0 = first["t"] - first["n"] / SR
            if not pieces:
                print("trial %d: no tap pieces" % n)
                continue
            tap = to_16k(b"".join(p[2] for p in pieces), tinfo["sample_rate"])
            est = estimate_delay(tap, mic_now, t0, t_start, t_end, pieces[0][0])
            if est is None:
                print("trial %d: delay estimate failed" % n)
                continue
            print("trial %d: delay=%.3fs match=%.2f tap_audio=%.2fs" % (n, est[0], est[1], len(tap) / SR))
            trials.append((pieces[0][0], est[0], tap))
            time.sleep(0.8)
    tts.shutdown()
    if not trials:
        print("NO USABLE TRIALS")
        return 3

    mic_all = np.concatenate(blocks).astype(np.int16)
    t0 = first["t"] - first["n"] / SR
    far = np.zeros(len(mic_all), dtype=np.float64)
    spans = []
    for t_tap0, delay, tap in trials:
        pos = int(round((t_tap0 + delay - t0) * SR))
        end = min(len(far), pos + len(tap))
        if pos < 0 or end <= pos:
            continue
        far[pos:end] += tap[: end - pos]
        spans.append((pos, end))
    far = np.clip(far, -32768, 32767).astype(np.int16)
    try:
        clean = run_aec(AudioProcessor, mic_all, far)
        control = run_aec(AudioProcessor, mic_all, np.zeros_like(far))
    except Exception as exc:
        print("AEC CALL FAILED: %r" % (exc,))
        return 4
    os.makedirs(OUT_DIR, exist_ok=True)
    save_wav(os.path.join(OUT_DIR, "mic_raw.wav"), mic_all)
    save_wav(os.path.join(OUT_DIR, "far_aligned.wav"), far)
    save_wav(os.path.join(OUT_DIR, "mic_clean.wav"), clean)
    print("Peaks per 512-sample chunk during each sentence (raw mic / after AEC / control with silent reference):")
    for n, (a, b) in enumerate(spans, 1):
        print("sentence %d: raw %s | AEC %s (%.1f dB down) | control %s (%.1f dB down)"
              % (n, pk(mic_all[a:b]), pk(clean[a:b]), db(mic_all[a:b], clean[a:b]),
                 pk(control[a:b]), db(mic_all[a:b], control[a:b])))
    print("For reference, your own speech: " + SPEECH_REF)
    print("Saved WAVs in " + OUT_DIR)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())