import sys
sys.path.insert(0, r"C:\Jarvis_Ted\tools")
from jarvis_brain import Brain
b = Brain(log=print)
mode = sys.argv[1]
if mode == "write":
    print("SPOKEN:", b.try_memory_command("Remember that I prefer Flutter."))
    print("FACTS:", [f["text"] for f in b.facts])
    sys.exit(0 if [f["text"] for f in b.facts] == ["I prefer Flutter"] else 1)
loaded = [f["text"] for f in b.facts]
in_prompt = "- I prefer Flutter" in b.build_messages("What do I prefer?")[0]["content"]
print("FACTS AFTER RESTART:", loaded)
print("FACT IN SYSTEM PROMPT:", in_prompt)
print("SPOKEN:", b.try_memory_command("Forget everything you remember."))
print("FACTS AFTER FORGET:", [f["text"] for f in b.facts])
ok = loaded == ["I prefer Flutter"] and in_prompt and b.facts == []
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)