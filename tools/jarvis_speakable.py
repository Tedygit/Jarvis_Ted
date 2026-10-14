"""jarvis_speakable.py - ACCOUNT2_SPEAKABLE_V1

Turns an LLM reply sentence into text that is fit to be spoken by Piper.
The model writes markdown (**bold**, `code`, "1. **Item**: ...", "- bullet",
"## Heading", [text](url)). Spoken aloud, those symbols are noise at best.
Only the text sent to the speaker is changed: history, logs and the console
keep the original sentence. Pure Python, no third-party imports.
"""
from __future__ import annotations

import re

_FENCE = re.compile(r"```[A-Za-z0-9_+-]*")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_BOLD = re.compile(r"(\*\*)(?=\S)(.+?)(?<=\S)\1")  # only ** (a __dunder__ name is code, not emphasis)
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_(?=\S)([^_]+?)(?<=\S)_(?![\w_])")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET = re.compile(r"^\s*[-*+\u2022]\s+")
_NUMBERED = re.compile(r"^\s*\d{1,2}[.)]\s+")
_RULE = re.compile(r"^\s*([-*_=])\1{2,}\s*$")
_SPACES = re.compile(r"[ \t]+")


def speakable(text: str) -> str:
    """Return text with markdown markers removed. May return '' (nothing to say)."""
    if not text:
        return ""
    t = _FENCE.sub(" ", text)
    out_lines = []
    for line in t.splitlines() or [t]:
        if _RULE.match(line):
            continue
        line = _HEADING.sub("", line)
        line = _BULLET.sub("", line)
        line = _NUMBERED.sub("", line)
        out_lines.append(line)
    t = " ".join(out_lines)
    t = _LINK.sub(r"\1", t)
    t = _BOLD.sub(r"\2", t)
    t = _ITALIC_STAR.sub(r"\1", t)
    t = _ITALIC_UNDER.sub(r"\1", t)
    t = t.replace("`", "")
    t = _SPACES.sub(" ", t).strip()
    if not re.search(r"[A-Za-z0-9]", t):  # only punctuation left: nothing worth speaking
        return ""
    return t