"""Check of the tts_piper reference tap and stream restart (Account 2, 2026-10-01)."""
import sys
import threading
import time

sys.path.insert(0, r"C:\Jarvis_Ted\tools")
from tts_piper import CancellableTTS

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def speak_checked(text):
    """speak() and report any exception instead of a traceback."""
    before = tts.tap_stats()["total_bytes"]
    try:
        tts.speak(text)
    except Exception as exc:
        print(f"        speak() raised {type(exc).__name__}: {exc}")
        return False, 0
    return True, tts.tap_stats()["total_bytes"] - before


tts = CancellableTTS()
info = tts.tap_info()
print("tap_info:", info)
sr = info["sample_rate"]

print("\n--- Test 1: one full sentence (listen: it must play in full) ---")
t0 = time.monotonic()
tts.speak("This is a test of the reference audio tap. This sentence should be heard in full.")
t1 = time.monotonic()
entries = tts.tap_read()
stats = tts.tap_stats()
tap_bytes = sum(len(e[2]) for e in entries)
print(f"speak() took {t1 - t0:.2f}s; tap holds {len(entries)} pieces = {tap_bytes / 2 / sr:.2f}s of audio")
check("tap holds every byte that was written", tap_bytes == stats["total_bytes"] and tap_bytes > 0,
      f"(tap {tap_bytes} bytes, counter {stats['total_bytes']})")
check("timestamps are ordered and inside the speak() call",
      all(entries[i][1] <= entries[i + 1][0] + 1e-6 for i in range(len(entries) - 1))
      and entries[0][0] >= t0 and entries[-1][1] <= t1,
      f"(first start +{entries[0][0] - t0:.3f}s, last end {t1 - entries[-1][1]:.3f}s before return)")


def interrupted_speak(text, stop_after):
    th = threading.Thread(target=tts.speak, args=(text,))
    th.start()
    time.sleep(stop_after)
    t_stop = time.monotonic()
    tts.stop()
    th.join(timeout=8)
    return th, t_stop


print("\n--- Test 2: second sentence, interrupted after 1.2 s (listen: it must be cut) ---")
before = tts.tap_stats()["total_bytes"]
th, t_stop = interrupted_speak(
    "This second sentence is deliberately long so that it can be interrupted in the middle "
    "by the stop call, and the rest of it should never be heard at all.", 1.2)
after = tts.tap_stats()
written = after["total_bytes"] - before
check("speak() returned after stop()", not th.is_alive())
check("stop time was recorded", after["last_stop_monotonic"] is not None
      and abs(after["last_stop_monotonic"] - t_stop) < 0.05)
late = tts.tap_read(since=t_stop + 0.25)
check("no audio recorded after the stop", len(late) == 0, f"({len(late)} piece(s) later than stop + 0.25 s)")
check("interrupted sentence recorded less than 6 s of audio (loose check)", written / 2 / sr < 6.0,
      f"({written / 2 / sr:.2f}s recorded)")

print("\n--- Test 3: speak 3 s after the stop, like the live pipeline (listen: full sentence) ---")
r0, o0 = tts.restart_retries, tts.stream_reopens
time.sleep(3.0)
ok, grew = speak_checked("This sentence follows three seconds after the stop.")
check("speak() after a 3 s pause worked", ok and grew > 0, f"({grew / 2 / sr:.2f}s recorded)")
print(f"        restart retries used: {tts.restart_retries - r0}, stream reopens: {tts.stream_reopens - o0}"
      "  (information: 0 and 0 means the stream restarted on the first attempt)")

print("\n--- Test 4: speak immediately after a stop, the case that failed before (listen: cut, then full sentence) ---")
r0, o0 = tts.restart_retries, tts.stream_reopens
th, t_stop = interrupted_speak(
    "This fourth sentence is also long and it will be interrupted shortly after it starts, "
    "so the rest of it must not be heard.", 1.0)
ok, grew = speak_checked("Immediate restart after a stop works.")
check("speak() immediately after a stop worked", ok and grew > 0, f"({grew / 2 / sr:.2f}s recorded)")
print(f"        restart retries used: {tts.restart_retries - r0}, stream reopens: {tts.stream_reopens - o0}")

tts.shutdown()
print("\nRESULT:", "ALL PASS" if all(results) else "SOME CHECKS FAILED", f"({sum(results)}/{len(results)})")