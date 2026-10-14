"""tools/diagnose_tts_gaps.py — checks whether TTS chunk synthesis is
keeping up with real-time playback, to test the hypothesis that the
mid-utterance clicks ("mic bumps") reported during barge-in testing are
caused by the output device running out of buffered audio between
synthesized chunks (a streaming underrun) rather than something else.

No stop()/interrupt logic here -- this only exercises normal playback
with per-chunk timing diagnostics turned on. Listen for the mid-utterance
bumps while each sentence plays, and note roughly how many you hear and
where, so they can be compared against the printed UNDERRUN LIKELY lines.
"""
from tts_piper import CancellableTTS

SENTENCES = [
    "This is the first warm-up sentence.",
    "This is the second sentence, after the model is already loaded.",
    (
        "This is a long test sentence meant to give enough time for an "
        "interruption to happen partway through the utterance, so we can "
        "measure how quickly playback actually stops once the stop signal "
        "is sent from another thread."
    ),
]


def main() -> None:
    tts = CancellableTTS()
    for i, text in enumerate(SENTENCES, start=1):
        print(f"\n--- utterance {i}: \"{text[:50]}...\" ---")
        tts.speak(text, debug=True)


if __name__ == "__main__":
    main()
