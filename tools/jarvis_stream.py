"""jarvis_stream.py - Account 2 (ACCOUNT2_STREAM_V1): pure helpers for streamed LLM replies. No I/O, no threads.

parse_sse_line(line)  -> (delta_text, finish_reason, done)   one line of llama-server's stream
SentenceSplitter      -> turns streamed text pieces into whole sentences to speak, one at a time
"""
from __future__ import annotations

import json
import re

_END = re.compile(r"(?:[.!?][\"')\]]*\s+|\n+)")   # sentence end followed by whitespace, or a line break


def parse_sse_line(line: str) -> tuple[str, str | None, bool]:
    """Parse one line of an OpenAI-style stream ("data: {...}"). Returns (delta, finish_reason, done).
    Lines that are not data lines, or are not valid JSON, give ("", None, False)."""
    s = line.strip()
    if not s.startswith("data:"):
        return "", None, False
    body = s[5:].strip()
    if body == "[DONE]":
        return "", None, True
    try:
        choice = json.loads(body)["choices"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return "", None, False
    delta = (choice.get("delta") or {}).get("content") or ""
    return delta, choice.get("finish_reason"), False


class SentenceSplitter:
    """feed(piece) returns the sentences completed by that piece. finish(reason) returns what is left.

    * A sentence shorter than min_chars is held and joined to the next one (avoids a choppy "Sure.").
    * finish("stop") speaks the remainder. For any other reason (for example "length", the token cap)
      an unfinished fragment is dropped, as finish_reply() does for non-streamed replies, unless nothing
      at all has been emitted yet. The dropped fragment is kept in .dropped for the log.
    * Any copy of the "[interrupted]" marker the model may imitate from history is removed."""

    def __init__(self, min_chars: int = 24) -> None:
        self.min_chars = min_chars
        self._buf = ""
        self._held = ""
        self.emitted = 0
        self.dropped = ""

    @staticmethod
    def _clean(text: str) -> str:
        return " ".join(text.replace("[interrupted]", "").split())

    def feed(self, piece: str) -> list[str]:
        self._buf += piece
        out: list[str] = []
        pos = 0
        for m in _END.finditer(self._buf):
            sentence = self._clean(self._held + " " + self._buf[pos:m.end()])
            pos = m.end()
            if not sentence:
                self._held = ""
            elif len(sentence) < self.min_chars:
                self._held = sentence
            else:
                out.append(sentence)
                self._held = ""
        self._buf = self._buf[pos:]
        self.emitted += len(out)
        return out

    def finish(self, reason: str | None) -> list[str]:
        held, rest = self._held, self._clean(self._buf)
        self._held = self._buf = ""
        out: list[str] = []
        if reason == "stop":
            text = self._clean(held + " " + rest)
            if text:
                out.append(text)
        else:
            if held:
                out.append(held)
            if rest:
                if self.emitted + len(out) == 0:
                    out.append(rest)
                else:
                    self.dropped = rest
        self.emitted += len(out)
        return out