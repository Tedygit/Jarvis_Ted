"""tts_piper.py - cancellable, resident TTS playback for the barge-in path.

The piper voice model AND the output audio stream are each loaded/opened
ONCE (in CancellableTTS.__init__) and kept resident in-process for the
app's lifetime. Recreating an sd.RawOutputStream per utterance -- opening
and closing the underlying audio device on every speak() call -- produces
an audible open/close transient (a click) independent of chunk count and
independent of whether stop() is ever called; confirmed 2026-09-06 when
diagnose_tts_gaps.py reproduced the click on a fully uninterrupted run
where every utterance synthesized as a single chunk. Keeping one resident
stream open for the app's lifetime, and only start()/abort()-ing it
(never closing until shutdown), removes that device-level transient the
same way the resident voice model removes the ~4.8s load cost from being
paid per utterance.

speak() streams synthesized AudioChunks straight to the output device in
small pieces; stop() aborts playback immediately via PortAudio's abort()
-- that abort() call, not the chunk size, is what produces the sub-50ms
interrupt latency, so it works regardless of how piper internally chunks
a given utterance.

speak(..., debug=True) additionally prints, for each chunk after the
first, the previous chunk's playback duration and the wall-clock gap
before the next chunk was ready from the synthesizer -- flagging
UNDERRUN LIKELY whenever that gap exceeds the previous chunk's duration,
which would starve the output device of audio and produce an audible
click/pop. This is a diagnostic aid for isolating mid-utterance audio
artifacts; it does not change playback behavior when debug=False.

2026-09-07 addition (diagnosing total-silence-in-live-pipeline bug):
speak() now ALWAYS prints a one-line summary when it returns -- chunks
written, bytes written, elapsed time, and whether stop_event ended up
set -- regardless of the debug= flag. This is cheap and unconditional
because the live pipeline (stream_asr_test.py) calls tts.stop() on
EVERY VAD SPEECH START, including short noise/filler-burst false
positives (a separate, already-known-open VAD problem). Hypothesis:
speak()'s _stop_event.clear() happens right at entry, but a spurious
SPEECH START landing anywhere during synthesis (which is not
instantaneous) can set the event again before the first chunk is even
written -- causing speak() to return having written ZERO bytes, with
no exception and no prior visible symptom. This instrumentation
confirms or refutes that directly on the next live run, instead of
guessing further from silence in the log.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass  # ACCOUNT2_SPEAKRESULT_V1
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import sounddevice as sd
from piper import PiperVoice

try:  # ACCOUNT2_SPEAKABLE_V1: strip markdown markers before the text is spoken
    from jarvis_speakable import speakable as _speakable
except ImportError:  # keep speaking even if the helper file is missing
    def _speakable(text):
        return text

DEFAULT_VOICE_NAME = "en_US-lessac-medium"
WRITE_PIECE_SIZE = 4096  # bytes per write() call -- keeps interrupt checks fine-grained


def _resolve_voice_model(name: str = DEFAULT_VOICE_NAME) -> Path:
    """Locate the .onnx voice model file. Checks likely locations; raises
    with an exact search command if none match."""
    candidates = [
        Path(r"C:\Jarvis_Ted") / f"{name}.onnx",          # piper's CWD-default download location
        Path(r"C:\Jarvis_Ted\models") / f"{name}.onnx",
        Path.home() / ".local" / "share" / "piper" / "voices" / f"{name}.onnx",
        Path.home() / "AppData" / "Local" / "piper" / f"{name}.onnx",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"Could not find {name}.onnx in any known location:\n  "
        + "\n  ".join(str(c) for c in candidates)
        + f'\nFind it with: Get-ChildItem -Path C:\\ -Recurse -Filter "{name}*.onnx" -ErrorAction SilentlyContinue'
        + "\nthen pass it explicitly: CancellableTTS(model_path=r'...')"
    )


# ACCOUNT2_TAP_V1 -- output device by name + reference-audio tap (2026-10-01)
OUTPUT_DEVICE_NAME = "Speaker (Realtek(R) Audio)"
OUTPUT_HOSTAPI_NAME = "MME"
FALLBACK_OUTPUT_INDEX = 3
TAP_SECONDS = 10.0  # how much already-written audio the reference tap keeps

# ACCOUNT3_DEFAULT_OUTPUT_V1 -- follow the Windows default output (2026-10-02).
# Evidence: with the earphone plugged in, the MME default output was 'Headphone (Realtek(R) Audio)'
# (index 4) while the by-name lookup below picked 'Speaker (Realtek(R) Audio)' (index 6), so speech
# played on the laptop speaker. The MME default is used when it is one of these two Realtek endpoints
# (the Speaker endpoint is the one confirmed audible at 22050 Hz; the Headphone endpoint is untested
# until Tedy reports). Any other default (e.g. the EPSON projector) is ignored.
OUTPUT_FOLLOW_DEFAULT_NAMES = ("Headphone (Realtek(R) Audio)", "Speaker (Realtek(R) Audio)")


def _resolve_output_device() -> int:
    """Pick the output device index at start-up (indices change when headphones are connected).
    1) the MME host API's default output device, if it is one of OUTPUT_FOLLOW_DEFAULT_NAMES;
    2) else the device named OUTPUT_DEVICE_NAME on OUTPUT_HOSTAPI_NAME;
    3) else FALLBACK_OUTPUT_INDEX. Resolved once at start-up: plug the earphone in BEFORE starting."""
    try:
        apis = sd.query_hostapis()
        devices = sd.query_devices()
        mme = next((i for i, a in enumerate(apis) if a["name"] == OUTPUT_HOSTAPI_NAME), None)
        if mme is not None:
            default_idx = apis[mme]["default_output_device"]
            if default_idx is not None and default_idx >= 0:
                dev = devices[default_idx]
                if dev["max_output_channels"] >= 1 and dev["name"] in OUTPUT_FOLLOW_DEFAULT_NAMES:
                    print(f"[tts_piper] output device = {OUTPUT_HOSTAPI_NAME} default: index {default_idx} "
                          f"({dev['name']!r})")
                    return default_idx
                print(f"[tts_piper] {OUTPUT_HOSTAPI_NAME} default output {dev['name']!r} (index {default_idx}) "
                      "is not a known Realtek endpoint; using the by-name lookup")
        for idx, dev in enumerate(devices):
            if dev["max_output_channels"] < 1:
                continue
            if dev["name"] != OUTPUT_DEVICE_NAME:
                continue
            if apis[dev["hostapi"]]["name"] != OUTPUT_HOSTAPI_NAME:
                continue
            print(f"[tts_piper] output device resolved by name: index {idx} "
                  f"({OUTPUT_DEVICE_NAME!r}, {OUTPUT_HOSTAPI_NAME})")
            return idx
    except Exception as exc:
        print(f"[tts_piper] output device lookup failed: {exc}")
    print(f"[tts_piper] WARNING: no output device named {OUTPUT_DEVICE_NAME!r} on "
          f"{OUTPUT_HOSTAPI_NAME}; falling back to index {FALLBACK_OUTPUT_INDEX}")
    return FALLBACK_OUTPUT_INDEX


@dataclass
class SpeakResult:  # ACCOUNT2_SPEAKRESULT_V1: what speak() returns (it returned None before)
    stopped_early: bool      # True if stop() / a write failure ended playback before the end
    bytes_written: int       # PCM bytes handed to the output stream
    audio_seconds: float     # bytes_written expressed as seconds of audio
    elapsed_seconds: float   # wall time inside speak()
    skipped: bool = False    # ACCOUNT2_EPOCH_V1: True when the reply was cancelled before this sentence started


class CancellableTTS:
    def __init__(self, model_path: Optional[str] = None):
        resolved = Path(model_path) if model_path else _resolve_voice_model()
        t0 = time.perf_counter()
        self._voice = PiperVoice.load(str(resolved))
        print(f"[tts_piper] model loaded from {resolved} in {time.perf_counter() - t0:.3f}s "
              "(one-time cost, paid once per app run -- not per utterance)")
        self.sample_rate = self._voice.config.sample_rate
        self._stop_event = threading.Event()
        self._epoch = 0                        # ACCOUNT2_EPOCH_V1: bumped by every stop()
        self._epoch_lock = threading.Lock()
        # Reference-audio tap (ACCOUNT2_TAP_V1): a copy of every PCM piece handed to the
        # output stream, for an echo canceller. See tap_info() / tap_read().
        self._tap_lock = threading.Lock()
        self._tap = collections.deque()
        self._tap_bytes = 0
        self._tap_total_bytes = 0
        self._last_stop_monotonic = None
        self.restart_retries = 0   # ACCOUNT2_RESTART_V1: stream.start() retries needed after abort()
        self.stream_reopens = 0    # ACCOUNT2_RESTART_V1: times the output stream had to be reopened

        # Resident output stream: opened ONCE and kept alive for the app's
        # lifetime, same principle as the resident voice model above.
        # Recreating this per utterance was the source of the per-utterance
        # click confirmed 2026-09-06 -- see module docstring.
        t1 = time.perf_counter()
        self._output_device = _resolve_output_device()
        self._stream = sd.RawOutputStream(samplerate=self.sample_rate, channels=1, dtype="int16",
                                          device=self._output_device)
        self._stream.start()
        print(f"[tts_piper] output stream opened in {time.perf_counter() - t1:.3f}s "
              "(one-time cost, kept resident -- not reopened per utterance)")

    def _ensure_stream_active(self) -> None:
        """abort() leaves the stream stopped; restart it (without closing
        or reopening the underlying device) before the next utterance."""
        if not self._stream.active:
            self._restart_stream()

    def _restart_stream(self) -> None:
        """Restart the stream after abort() (ACCOUNT2_RESTART_V1). MME can refuse for a short
        time after an abort ("media data is still playing", MME error 33), so retry for up to
        1 s, then reopen the same device as a last resort."""
        last_exc = None
        for attempt in range(20):
            try:
                self._stream.start()
                if attempt:
                    self.restart_retries += attempt
                    print(f"[tts_piper] stream restart needed {attempt} retry(ies), "
                          f"about {attempt * 0.05:.2f}s, after abort")
                return
            except sd.PortAudioError as exc:
                last_exc = exc
                time.sleep(0.05)
        print(f"[tts_piper] WARNING: stream.start() kept failing for 1 s ({last_exc}); "
              "reopening the output stream")
        self.stream_reopens += 1
        try:
            self._stream.close()
        except sd.PortAudioError:
            pass
        self._stream = sd.RawOutputStream(samplerate=self.sample_rate, channels=1, dtype="int16",
                                          device=self._output_device)
        self._stream.start()

    def _write_in_pieces(self, audio: bytes, piece_size: int = WRITE_PIECE_SIZE) -> bool:
        """Write audio in small pieces, checking stop_event between pieces.
        Returns False if interrupted or the stream failed, True if fully written."""
        for i in range(0, len(audio), piece_size):
            if self._stop_event.is_set():
                print(f"[tts_piper] write interrupted by stop_event at byte {i}/{len(audio)}")
                return False
            try:
                piece = audio[i:i + piece_size]
                t_w0 = time.monotonic()
                self._stream.write(piece)
                self._tap_record(t_w0, time.monotonic(), piece)
            except sd.PortAudioError as exc:
                print(f"[tts_piper] WRITE FAILED (PortAudioError) at byte {i}/{len(audio)}: {exc}")
                return False
        return True

    def speak(self, text: str, on_first_chunk: Callable[[], None] | None = None,
              debug: bool = False, epoch: Optional[int] = None) -> "SpeakResult":
        """Blocks the calling (worker) thread until done or stop() fires.

        debug=True prints per-chunk synthesis-vs-playback timing (see
        module docstring) without changing playback behavior.

        Always prints a one-line summary on return (chunks/bytes written,
        elapsed time, final stop_event state) -- see 2026-09-07 module
        docstring note.
        """
        _orig_text = text  # ACCOUNT2_SPEAKABLE_V1
        text = _speakable(text)
        if text != _orig_text:
            print(f"[tts_piper] speakable: {_orig_text!r} -> {text!r}")
        if not text.strip():
            return SpeakResult(stopped_early=False, bytes_written=0, audio_seconds=0.0, elapsed_seconds=0.0)
        t_entry = time.perf_counter()
        with self._epoch_lock:  # ACCOUNT2_EPOCH_V1: a sentence of a cancelled reply must not clear the stop flag
            if epoch is not None and epoch != self._epoch:
                print("[tts_piper] speak() skipped: the reply was cancelled before this sentence")
                return SpeakResult(stopped_early=True, bytes_written=0, audio_seconds=0.0,
                                   elapsed_seconds=0.0, skipped=True)
            self._stop_event.clear()
        self._ensure_stream_active()
        first_chunk = True
        t_after_prev_write: Optional[float] = None
        prev_chunk_duration: Optional[float] = None
        chunk_index = 0
        bytes_written = 0
        stopped_early = False
        for chunk in self._voice.synthesize(text):
            if self._stop_event.is_set():
                print(f"[tts_piper] speak() stopped before chunk {chunk_index} "
                      f"({time.perf_counter() - t_entry:.3f}s after entry, "
                      f"{bytes_written} bytes written so far)")
                stopped_early = True
                break
            t_received = time.perf_counter()
            if debug and t_after_prev_write is not None:
                synth_gap = t_received - t_after_prev_write
                flag = "UNDERRUN LIKELY" if synth_gap > prev_chunk_duration else "ok"
                print(f"[debug] chunk {chunk_index}: prev_chunk_duration={prev_chunk_duration:.3f}s, "
                      f"gap_before_this_chunk={synth_gap:.3f}s -> {flag}")
            if first_chunk:
                if on_first_chunk is not None:
                    on_first_chunk()
                first_chunk = False
            if not self._write_in_pieces(chunk.audio_int16_bytes):
                stopped_early = True
                break
            bytes_written += len(chunk.audio_int16_bytes)
            t_after_prev_write = time.perf_counter()
            prev_chunk_duration = len(chunk.audio_int16_bytes) / 2 / self.sample_rate
            chunk_index += 1
        else:
            if debug:
                print(f"[debug] utterance complete: {chunk_index} chunks total")

        elapsed = time.perf_counter() - t_entry
        print(f"[tts_piper] speak() done: {chunk_index} chunk(s), {bytes_written} bytes written, "
              f"{elapsed:.3f}s elapsed, stopped_early={stopped_early}, "
              f"stop_event_set={self._stop_event.is_set()}")
        return SpeakResult(stopped_early=stopped_early, bytes_written=bytes_written,
                           audio_seconds=bytes_written / 2 / self.sample_rate,
                           elapsed_seconds=elapsed)

    def tap_info(self) -> dict:
        """Facts about the reference signal that an echo canceller needs."""
        try:
            latency = float(self._stream.latency)
        except Exception:
            latency = None
        return {
            "sample_rate": self.sample_rate,
            "channels": 1,
            "dtype": "int16",
            "output_device_index": self._output_device,
            "output_latency_s": latency,
            "clock": "time.monotonic",
        }

    def _tap_record(self, t_start: float, t_end: float, pcm: bytes) -> None:
        limit = int(TAP_SECONDS * self.sample_rate * 2)
        with self._tap_lock:
            self._tap.append((t_start, t_end, pcm))
            self._tap_bytes += len(pcm)
            self._tap_total_bytes += len(pcm)
            while self._tap_bytes > limit and self._tap:
                self._tap_bytes -= len(self._tap.popleft()[2])

    def tap_read(self, since: Optional[float] = None) -> list:
        """Return [(t_write_start, t_write_end, pcm_int16_bytes), ...] in write order.
        Times are time.monotonic() seconds taken around the blocking stream.write().
        t_write_end is when PortAudio accepted the piece, NOT when it was heard: playback
        lags by the queued audio plus the output latency. since= keeps pieces whose
        t_write_end is later than that monotonic time."""
        with self._tap_lock:
            if since is None:
                return list(self._tap)
            return [e for e in self._tap if e[1] > since]

    def tap_stats(self) -> dict:
        with self._tap_lock:
            return {
                "total_bytes": self._tap_total_bytes,
                "buffered_bytes": self._tap_bytes,
                "last_stop_monotonic": self._last_stop_monotonic,
            }

    def begin_reply(self) -> int:
        """ACCOUNT2_EPOCH_V1: call once before the first sentence of a reply and pass the value to every
        speak(..., epoch=value). After any stop(), the remaining sentences of that reply are skipped."""
        with self._epoch_lock:
            return self._epoch

    def stop(self) -> None:
        """Call from the VAD/turn-manager thread the instant speech is detected."""
        with self._epoch_lock:  # ACCOUNT2_EPOCH_V1
            self._epoch += 1
        self._stop_event.set()
        self._last_stop_monotonic = time.monotonic()
        try:
            self._stream.abort()  # discard buffered audio immediately, don't drain it -- leaves the stream stopped, restarted by _ensure_stream_active() on the next speak()
        except sd.PortAudioError:
            pass

    def shutdown(self) -> None:
        """Call once, at actual app exit, to release the audio device."""
        try:
            self._stream.close()
        except sd.PortAudioError:
            pass