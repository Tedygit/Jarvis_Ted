import sys
sys.path.insert(0, r"C:\Jarvis_Ted\tools")
from tts_piper import CancellableTTS

tts = CancellableTTS()
print("Speaking now -- listen for the voice...")
tts.speak("This is a live end to end test of the patched output device.")
print("Done. Did you hear it? If yes, the fix is confirmed in production code.")
tts.shutdown()
