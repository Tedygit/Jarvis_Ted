"""jarvis_tools.py - read-only coding tools for Jarvis_Ted (Account 3, M6 first slice, 2026-10-02).

Everything here only READS the project: list files, read a file range, search text, git status /
diff / log. Nothing writes, deletes, runs a build, or uses a shell. Paths are confined to the
project root. Output is clipped (MAX_OUT_CHARS) because the local model has a small context.

try_tool_command(text) maps a few plain spoken phrases to a tool and returns the result text, or
None when the sentence is ordinary conversation. NOT yet wired into stream_asr_test.py.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

MAX_OUT_CHARS = 1500
MAX_FILE_BYTES = 1_000_000
MAX_LIST_ENTRIES = 60
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode", "segments", "memory"}


class ToolError(Exception):
    """A readable problem to report back (bad path, binary file, git missing...)."""


class CodeTools:
    def __init__(self, root) -> None:
        self.root = Path(root).resolve()

    def _safe(self, rel: str) -> Path:
        p = (self.root / (rel or ".")).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ToolError("that path is outside the project")
        return p

    @staticmethod
    def _clip(s: str, n: int = MAX_OUT_CHARS) -> str:
        return s if len(s) <= n else s[:n].rstrip() + f"\n... [{len(s) - n} more characters cut]"

    @staticmethod
    def _is_binary(p: Path) -> bool:
        with open(p, "rb") as fh:
            return b"\0" in fh.read(2048)

    def list_dir(self, rel: str = ".") -> str:
        p = self._safe(rel)
        if not p.is_dir():
            raise ToolError(f"{rel} is not a folder")
        names = [e for e in p.iterdir() if e.name not in SKIP_DIRS]
        names.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        out = [("[dir] " if e.is_dir() else "") + e.name for e in names[:MAX_LIST_ENTRIES]]
        if len(names) > MAX_LIST_ENTRIES:
            out.append(f"... and {len(names) - MAX_LIST_ENTRIES} more")
        return self._clip(f"{p.relative_to(self.root) or '.'}:\n" + "\n".join(out))

    def read_file(self, rel: str, start: int = 1, count: int = 40) -> str:
        p = self._safe(rel)
        if not p.is_file():
            raise ToolError(f"{rel} is not a file")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"{rel} is too large to read ({p.stat().st_size} bytes)")
        if self._is_binary(p):
            raise ToolError(f"{rel} looks like a binary file")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        end = min(len(lines), start + max(1, int(count)) - 1)
        if start > len(lines):
            raise ToolError(f"{rel} has only {len(lines)} lines")
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
        return self._clip(f"{p.relative_to(self.root)} (lines {start}-{end} of {len(lines)}):\n{body}")

    def search(self, text: str, glob: str = "*", max_hits: int = 15) -> str:
        needle = text.strip().lower()
        if not needle:
            raise ToolError("nothing to search for")
        hits, files = [], 0
        for dirpath, dirs, names in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(names):
                if not fnmatch.fnmatch(name, glob):
                    continue
                fp = Path(dirpath) / name
                try:
                    if fp.stat().st_size > MAX_FILE_BYTES or self._is_binary(fp):
                        continue
                    files += 1
                    for n, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if needle in line.lower():
                            hits.append(f"{fp.relative_to(self.root)}:{n}: {line.strip()[:120]}")
                            if len(hits) >= max_hits:
                                return self._clip("\n".join(hits) + f"\n(stopped at {max_hits} matches)")
                except OSError:
                    continue
        return self._clip("\n".join(hits) if hits else f"no matches for '{text}' in {files} files")

    def _git(self, *args: str) -> str:
        if not shutil.which("git"):
            raise ToolError("git is not installed or not on PATH")
        r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        if r.returncode != 0:
            raise ToolError("git error: " + (r.stderr.strip().splitlines() or ["unknown"])[0][:200])
        return r.stdout.strip()

    def git_status(self) -> str:
        return self._clip(self._git("status", "--short", "--branch"))

    def git_diff(self, rel: str | None = None) -> str:
        if rel:
            p = self._safe(rel)
            return self._clip(self._git("diff", "-U2", "--", str(p)) or f"no changes in {rel}")
        return self._clip(self._git("diff", "--stat") or "no unstaged changes")

    def git_log(self, n: int = 5) -> str:
        n = min(max(1, int(n)), 15)
        return self._clip(self._git("log", f"-n{n}", "--date=short", "--pretty=format:%h %ad %s") or "no commits yet")


_RULES = [
    (re.compile(r"^(?:list|show) (?:the )?files(?: in (?:the )?(?:folder )?(.+))?$"), "list"),
    (re.compile(r"^read (?:the )?file (.+?)(?: from line (\d+))?$"), "read"),
    (re.compile(r"^search (?:the )?(?:code |project )?for (.+)$"), "search"),
    (re.compile(r"^git status$"), "status"),
    (re.compile(r"^git diff(?: (?:of|for) (.+))?$"), "diff"),
    (re.compile(r"^git log$"), "log"),
]


def try_tool_command(tools: CodeTools, text: str) -> str | None:
    """Result text if the sentence is a tool command, else None (ordinary conversation)."""
    s = re.sub(r"[.!?,]+$", "", text.strip().lower())
    s = re.sub(r"^(?:please |can you |could you )", "", s)
    for rx, kind in _RULES:
        m = rx.match(s)
        if not m:
            continue
        try:
            if kind == "list":
                return tools.list_dir(m.group(1) or ".")
            if kind == "read":
                return tools.read_file(m.group(1), int(m.group(2) or 1))
            if kind == "search":
                return tools.search(m.group(1))
            if kind == "status":
                return tools.git_status()
            if kind == "diff":
                return tools.git_diff(m.group(1))
            return tools.git_log()
        except ToolError as exc:
            return f"Tool problem: {exc}"
    return None