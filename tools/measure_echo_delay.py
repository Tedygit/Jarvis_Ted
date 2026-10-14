import sys
import time

import numpy as np
import sounddevice as sd

SR = 16000
FRAME = 160  # 10 ms at 16 kHz
CH = 512
NAME = "Smart Sound"
API = "Windows WASAPI"
SENTENCES = [
    "This is the first sentence for measuring the echo delay.",
    "Here is a second, slightly longer sentence to measure the same path again.",
    "And this is the third and last measurement sentence.",
]
SPEECH_REF = "median 452, p95 1526 (measured earlier with the earphone connected)"


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


def analyze(pieces, rate, mic_all, t0, t_start, t_end):
    if not pieces:
        return None
    t_tap0 = pieces[0][0]
    tap = to_16k(b"".join(p[2] for p in pieces), rate)
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
    echo = mic[k * FRAME: (k + len(a)) * FRAME]
    base = mic[: int(0.2 * SR)]
    return {
        "delay": (t_w0 + k * 0.01) - t_tap0,
        "conf": float(score[k]),
        "tap_s": len(tap) / SR,
        "echo_peaks": chunk_peaks(echo),
        "base_max": int(np.abs(base.astype(np.int32)).max()) if len(base) else 0,
    }


def main():
    sys.path.insert(0, r"C:\Jarvis_Ted\tools")
    from tts_piper import CancellableTTS

    idx = find_input_device(NAME, API)
    info = sd.query_devices(idx)
    api_name = sd.query_hostapis()[info["hostapi"]]["name"]
    extra = sd.WasapiSettings(auto_convert=True) if api_name == "Windows WASAPI" else None
    print("Using input device [%d] %r" % (idx, info["name"]))

    tts = CancellableTTS()
    tinfo = tts.tap_info()
    print("tap_info:", tinfo)

    blocks, first = [], {}

    def cb(indata, frames, time_info, status):
        if "t" not in first:
            first["t"], first["n"] = time.monotonic(), frames
        blocks.append(indata[:, 0].copy())

    results = []
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CH,
                        device=idx, extra_settings=extra, callback=cb):
        time.sleep(1.0)
        for n, text in enumerate(SENTENCES, 1):
            t_start = time.monotonic()
            tts.speak(text)
            time.sleep(1.2)
            t_end = time.monotonic()
            pieces = tts.tap_read(since=t_start)
            mic_all = np.concatenate(list(blocks))
            t0 = first["t"] - first["n"] / SR
            r = analyze(pieces, tinfo["sample_rate"], mic_all, t0, t_start, t_end)
            if r is None:
                print("trial %d: NO RESULT (no tap pieces or mic window too short)" % n)
            else:
                pk = r["echo_peaks"]
                print("trial %d: delay=%.3fs  match=%.2f  tap_audio=%.2fs  mic_echo_peak p50=%d p95=%d max=%d  quiet_before_max=%d"
                      % (n, r["delay"], r["conf"], r["tap_s"], int(np.median(pk)),
                         int(np.percentile(pk, 95)), int(pk.max()), r["base_max"]))
                results.append(r)
            time.sleep(1.0)
    tts.shutdown()

    if results:
        d = [r["delay"] for r in results]
        print("\nSUMMARY: delays=%s  spread=%.3fs  lowest match=%.2f"
              % ([round(x, 3) for x in d], max(d) - min(d), min(r["conf"] for r in results)))
        print("For reference, your own speech: " + SPEECH_REF)
    print("Done.")


if __name__ == "__main__":
    main()