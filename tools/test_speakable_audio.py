import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tts_piper

tts = tts_piper.CancellableTTS()
try:
    for s in ["1. **List**: an ordered collection of items.",
              "Run `git status` and then **commit** your work.",
              "## Summary"]:
        r = tts.speak(s)
        print("RESULT stopped_early=%s bytes_written=%s" % (r.stopped_early, r.bytes_written))
finally:
    tts.shutdown()