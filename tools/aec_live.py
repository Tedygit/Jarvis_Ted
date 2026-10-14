"""aec_live.py - live echo cancellation for the speakers (Account 1, 2026-10-02, UNVERIFIED live).

Feeds each 512-sample microphone chunk, plus a reference chunk built from CancellableTTS.tap_read(),
to the WebRTC canceller (pywebrtc-audio) and returns the cleaned chunk.

Reference timing (from offline sweeps on C:\\Jarvis_Ted\\aec_test, 4 sentences, one run): the reference
may LEAD the echo by about 50-400 ms (36/31/29-39 dB down) but must not LAG it (lag of +20 ms already
collapses it). So the tap is placed on the clock by WRITE time, which is earlier than playback, and
pieces written ahead of playback stay contiguous. After stop() the written-but-unplayed audio is cut
from the reference at stop time + output latency (abort() discards it).
Optional JARVIS_AEC_MIC_DELAY_MS delays the microphone path (multiples of 32 ms) to add lead if needed.
"""
from __future__ import annotations

import collections
import time

import numpy as np

SR = 16000


def _to_16k(pcm: bytes, rate: int) -> np.ndarray:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    if rate == SR or len(x) < 4:
        return x.astype(np.int16)
    xs = np.convolve(x, np.array([0.25, 0.5, 0.25]), mode="same")
    idx = np.arange(int(len(x) * SR / rate)) * (rate / SR)
    y = np.interp(idx, np.arange(len(x)), xs)
    return np.clip(y, -32768, 32767).astype(np.int16)


class LiveAEC:
    def __init__(self, tts, mic_delay_ms: int = 0, clock=time.monotonic) -> None:
        from pywebrtc_audio import AudioProcessor  # raises ImportError if not installed
        self._tts = tts
        self._clock = clock
        info = tts.tap_info()
        self._rate = int(info["sample_rate"])
        self._latency = float(info.get("output_latency_s") or 0.18)
        self._ap = AudioProcessor(sample_rate=SR, echo_cancellation=True, noise_suppression=False,
                                  auto_gain_control=False, stream_delay_ms=0)
        self._since = clock()
        self._segs: list = []        # [start_idx, int16 array at 16 kHz, t_write_end]
        self._end_idx = 0
        self._last_stop = None
        n_delay = int(round(mic_delay_ms / 32.0))
        self._delay = collections.deque(np.zeros(512, np.int16) for _ in range(n_delay)) if n_delay > 0 else None
        self._active = 0
        self._quiet = 0
        self._e_raw = 0.0
        self._e_clean = 0.0
        self._since_ref = 10 ** 9   # chunks since the reference last held audio
        self._play_start = None     # clock time the current reply's reference began
        self._closed = False        # ACCOUNT1_AEC_CLOSED_V1: set by end_session(); cleared when the reference has been silent for 1 s
        self._now = clock()
        self.ref_active = False     # True while a reply plays and for ~1 s after its reference ends
        # ACCOUNT1_AEC_GATE_V1: double-talk gate for the speech detector. While a reply plays, the
        # microphone chunk is hidden from the speech detector unless the canceller's output keeps at
        # least gate_theta of the microphone level over the last 5 chunks (echo only = output far below input).
        import os as _os
        self.gate_theta = float(_os.environ.get("JARVIS_AEC_GATE_THETA", "0.6"))   # 0 disables the gate
        self._hist = collections.deque(maxlen=5)
        self.vad_pass = True
        self._gate_n = 0
        self._gate_closed = 0
        # ACCOUNT1_AEC_BLIND_V1: the canceller has not converged at the start of a reply (live: 1-5 dB in the
        # first seconds at 60% volume), so its own first words looked like speech. For blind_s seconds from the
        # first reference audio of a reply the speech detector sees silence (half-duplex for the start of a reply).
        self.blind_s = float(_os.environ.get("JARVIS_AEC_BLIND_S", "3.0"))   # 0 disables
        self._dump = None           # JARVIS_AEC_DUMP=1: keep mic/reference/output for offline analysis
        import os
        if os.environ.get("JARVIS_AEC_DUMP"):
            import atexit
            self._dump = {"mic": [], "ref": [], "out": [], "t": []}
            atexit.register(self._write_dump)

    def _ingest(self) -> None:
        for t_ws, t_we, pcm in self._tts.tap_read(since=self._since):
            self._since = max(self._since, t_we)
            x = _to_16k(pcm, self._rate)
            start = max(self._end_idx, int(round(t_ws * SR)))
            self._segs.append([start, x, t_we])
            self._end_idx = start + len(x)
        stop = self._tts.tap_stats().get("last_stop_monotonic")
        if stop is not None and stop != self._last_stop:
            self._last_stop = stop
            cut = int(round((stop + self._latency) * SR))
            for seg in self._segs:
                if seg[2] <= stop and seg[0] + len(seg[1]) > cut:
                    seg[1] = seg[1][: max(0, cut - seg[0])]
            self._end_idx = max((g[0] + len(g[1]) for g in self._segs), default=0)

    def _reference(self, c0: int, n: int) -> np.ndarray:
        ref = np.zeros(n, dtype=np.int16)
        for s, x, _ in self._segs:
            a, b = max(c0, s), min(c0 + n, s + len(x))
            if a < b:
                ref[a - c0: b - c0] = x[a - s: b - s]
        self._segs = [g for g in self._segs if g[0] + len(g[1]) >= c0 - SR]
        return ref

    def process(self, chunk: np.ndarray):
        """chunk: int16 array, 512 samples (any shape). Returns (cleaned chunk, note or None)."""
        near = chunk.reshape(-1).astype(np.int16)
        n = len(near)
        now = self._clock()
        self._ingest()
        ref = self._reference(int(round(now * SR)) - n, n)
        if self._delay is not None:
            self._delay.append(near)
            near_in = self._delay.popleft()
        else:
            near_in = near
        out = np.asarray(self._ap.process(near_in, ref), dtype=np.int16).reshape(-1)
        note = None
        self._now = now
        if np.any(ref):
            if self._play_start is None and not self._closed:   # ACCOUNT1_AEC_CLOSED_V1: first reference audio of a new reply (not the tail of a stopped one)
                self._play_start = now
            self._since_ref = 0
        else:
            self._since_ref += 1
        self.ref_active = self._since_ref < 32
        if not self.ref_active:
            self._closed = False  # ACCOUNT1_AEC_CLOSED_V1
        if not self.ref_active:
            out = near_in.copy()  # idle: pass the (delayed) microphone through unchanged
        # ACCOUNT1_AEC_SESSION_V1: a reply (all its streamed sentences) is one session. It ends when the user's
        # speech is confirmed (end_session) or after about 30 s without reference audio, not after a 1 s gap
        # between sentences.
        if self._since_ref >= 938:
            self._play_start = None
        if self.ref_active and self.gate_theta > 0:
            self._hist.append((float(np.sum(near_in.astype(np.float64) ** 2)),
                               float(np.sum(out.astype(np.float64) ** 2))))
            e_in = sum(h[0] for h in self._hist)
            e_out = sum(h[1] for h in self._hist)
            self.vad_pass = (e_out / (e_in + 1e-9)) ** 0.5 >= self.gate_theta
            self._gate_n += 1
            if not self.vad_pass:
                self._gate_closed += 1
        else:
            self._hist.clear()
            self.vad_pass = True
        if self.blind_s > 0 and self.ref_active and self._play_start is not None \
                and (self._now - self._play_start) < self.blind_s:
            self.vad_pass = False
        if np.any(ref):
            self._active += 1
            self._quiet = 0
            self._e_raw += float(np.sum(near_in.astype(np.float64) ** 2))
            self._e_clean += float(np.sum(out.astype(np.float64) ** 2))
        elif self._active:
            self._quiet += 1
            if self._quiet >= 15:
                if self._active >= 15:
                    r = (self._e_raw / (self._active * n)) ** 0.5
                    c = (self._e_clean / (self._active * n)) ** 0.5
                    db = 20 * np.log10((r + 1e-9) / (c + 1e-9))
                    note = (f"[aec] reply: mic rms {r:.0f} -> {c:.0f} after cancellation "
                            f"({db:.1f} dB down) over {self._active * n / SR:.1f}s of playback; "
                            f"speech-detector gate closed on {100.0 * self._gate_closed / max(1, self._gate_n):.0f}% of chunks")
                self._active = 0
                self._e_raw = self._e_clean = 0.0
                self._gate_n = self._gate_closed = 0
        if self._dump is not None:
            self._dump["mic"].append(near.copy())
            self._dump["ref"].append(ref.copy())
            self._dump["out"].append(out.copy())
            self._dump["t"].append(now)
        return out.reshape(chunk.shape), note

    def _write_dump(self) -> None:
        import os
        import wave
        d = self._dump
        if not d or not d["mic"]:
            return
        folder = os.environ.get("JARVIS_AEC_DUMP_DIR", r"C:\Jarvis_Ted\aec_test\live_dump")
        os.makedirs(folder, exist_ok=True)
        for key, name in (("mic", "mic_live_raw.wav"), ("ref", "ref_live.wav"), ("out", "mic_live_out.wav")):
            with wave.open(os.path.join(folder, name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(np.concatenate(d[key]).astype(np.int16).tobytes())
        with open(os.path.join(folder, "chunk_times.txt"), "w") as f:
            f.write("\n".join("%.4f" % t for t in d["t"]))
        print(f"[aec] dump written to {folder} ({len(d['mic'])} chunks)")

    def end_session(self) -> None:
        """ACCOUNT1_AEC_SESSION_V1: the user spoke; the current reply session is over, so the next reply starts a new one."""
        self._play_start = None
        self._closed = True  # ACCOUNT1_AEC_CLOSED_V1: audio still draining after stop() must not open a new session

    def playback_age(self):
        """Seconds since the current reply's reference audio began (None when no reply is playing)."""
        if self._play_start is None:  # ACCOUNT1_AEC_SESSION_V1: also valid in the gap between two sentences
            return None
        return self._now - self._play_start