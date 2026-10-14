"""tools/test_barge_in.py — resident-model test: one-time load cost,
per-call latency on the warm model, and a reconfirmed mid-utterance interrupt.

This retests the interrupt mechanism because the implementation changed
(subprocess -> resident in-process model) -- the earlier PASS was for a
different code path and doesn't automatically carry over.
"""
import threading
import time

from tts_piper import CancellableTTS

SHORT_TEXT_1 = "This is the first warm-up sentence."
SHORT_TEXT_2 = "This is the second sentence, after the model is already loaded."
LONG_TEXT = (
    "This is a long test sentence meant to give enough time for an "
    "interruption to happen partway through the utterance, so we can "
    "measure how quickly playback actually stops once the stop signal "
    "is sent from another thread."
)
INTERRUPT_SECONDS_INTO_PLAYBACK = 2.0


def timed_speak(tts: CancellableTTS, text: str, label: str) -> None:
    t_start = time.perf_counter()
    first_chunk_at = {}

    def on_first_chunk() -> None:
        first_chunk_at["t"] = time.perf_counter() - t_start

    tts.speak(text, on_first_chunk=on_first_chunk)
    print(f"[{label}] first audio chunk after {first_chunk_at.get('t', -1):.3f}s (model already resident)")


def main() -> None:
    tts = CancellableTTS()  # model load happens once, here

    # Two short utterances back-to-back: both should show similar, fast
    # first-chunk latency -- proving the model isn't reloaded per call.
    timed_speak(tts, SHORT_TEXT_1, "utterance 1")
    timed_speak(tts, SHORT_TEXT_2, "utterance 2")

    # Reconfirm the interrupt on this new implementation.
    playback_started = threading.Event()
    t_start = time.perf_counter()

    def on_first_chunk() -> None:
        print(f"[{time.perf_counter() - t_start:.3f}s] first audio chunk written -- playback started")
        playback_started.set()

    speak_thread = threading.Thread(
        target=tts.speak,
        args=(LONG_TEXT,),
        kwargs={"on_first_chunk": on_first_chunk},
        daemon=True,
    )
    speak_thread.start()

    if not playback_started.wait(timeout=20):
        print("WARNING: no audio started within 20s -- investigate. Aborting.")
        tts.stop()
        speak_thread.join(timeout=5)
        return

    time.sleep(INTERRUPT_SECONDS_INTO_PLAYBACK)
    t_stop_called = time.perf_counter()
    print(f"[{t_stop_called - t_start:.3f}s] Calling stop() now -- listen for the cutoff...")
    tts.stop()
    speak_thread.join(timeout=5)
    t_joined = time.perf_counter()
    print(f"[{t_joined - t_start:.3f}s] speak() thread joined "
          f"({t_joined - t_stop_called:.3f}s after stop() was called)")

    if speak_thread.is_alive():
        print("WARNING: speak() thread did not exit within timeout -- investigate.")
    else:
        print("Thread exited cleanly. Confirm by ear: did audio stop "
              f"within ~{t_joined - t_stop_called:.3f}s of the printed stop() line, "
              "with no stale audio resuming after?")


if __name__ == "__main__":
    main()
