"""tools/diagnose_abort_click.py — isolates whether the audible click/bump
comes from stop()'s abort() cutting the waveform mid-sample.

diagnose_tts_gaps.py already showed (a) each test sentence synthesizes as
exactly 1 chunk, ruling out a between-chunk underrun, and (b) a full,
uninterrupted playback produced no click at all. The one condition
neither test exercised is an actual stop() call mid-utterance. This
script does exactly that, timed from the first audible audio (not from
thread launch, since time-to-first-audio varies with whisper-server/
llama-server also running) so the cut lands at a known point.

Run once per cut point, e.g.:
    python tools\diagnose_abort_click.py 1.0
    python tools\diagnose_abort_click.py 2.5
    python tools\diagnose_abort_click.py 4.0

After each run, report: did you hear a click, and roughly how many
seconds after speech started -- compare that to the printed
"stop() called at ...s after first audio" line.
"""
from __future__ import annotations

import sys
import threading
import time

from tts_piper import CancellableTTS

LONG_TEXT = (
    "This is a long test sentence meant to give enough time for an "
    "interruption to happen partway through the utterance, so we can "
    "measure how quickly playback actually stops once the stop signal "
    "is sent from another thread."
)

DEFAULT_CUT_AFTER_SECONDS = 2.0


def main() -> None:
    cut_after = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CUT_AFTER_SECONDS
    tts = CancellableTTS()

    first_audio_time: list[float] = []

    def on_first_chunk() -> None:
        first_audio_time.append(time.perf_counter())

    print(f"\n--- cutting {cut_after:.1f}s after first audio ---")
    t_launch = time.perf_counter()
    speak_thread = threading.Thread(
        target=tts.speak,
        args=(LONG_TEXT,),
        kwargs={"on_first_chunk": on_first_chunk, "debug": True},
    )
    speak_thread.start()

    while not first_audio_time:
        time.sleep(0.005)
    time.sleep(cut_after)

    t_stop = time.perf_counter()
    tts.stop()
    speak_thread.join()
    t_joined = time.perf_counter()

    print(f"[diagnose] first audio at {first_audio_time[0] - t_launch:.3f}s after launch")
    print(f"[diagnose] stop() called at {t_stop - first_audio_time[0]:.3f}s after first audio")
    print(f"[diagnose] thread joined {t_joined - t_stop:.3f}s after stop() called")
    print("[diagnose] Report: did you hear a click? If so, when relative to the cutoff?")


if __name__ == "__main__":
    main()
