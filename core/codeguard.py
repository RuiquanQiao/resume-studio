"""The window's agents never change the app's own code (user, 2026-11-14: an agent asked to adjust a track
edited the app's source instead).

The app folder is read-only to them except what is data: the data file and its resume-studio-data folder,
`.local/` (this computer's settings, plugins, career-ops' checkout) and `.cache/`. Each agent is held to it
through its own channel:

- Claude Code: a PreToolUse hook (scripts/codeguard_hook.py) that refuses Edit / Write / NotebookEdit on a
  protected path, and Bash / PowerShell commands that write to one. Hooks bind in every permission mode.
- Codex: its sandbox's writable roots leave the app folder out (the chat runs in the data folder), and the
  window answers its approval requests: a file change to the data file is allowed, one to the code refused.

`blocked_path` is exact. `blocked_command` reads a command line for writes into protected paths (redirects,
file cmdlets, sed -i, git commands that change the checkout, inline Python that writes files); a command
can always hide what it does, so the sandbox (Codex) and the edit tools (Claude Code) are the real walls."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Iterable

ENV = "RESUME_STUDIO_GUARD"   # the guard's settings, handed to the hook through the agent's environment
REFUSAL = ("Resume Studio's own code is read-only to you: {path} belongs to the app, not to the user's data. "
           "Change only the user's data (the data file, resume-studio-data, the tracker through its commands). "
           "If the app itself seems wrong, tell the user what you found instead of editing it.")

_SEP = re.compile(r"\s*(?:;|&&|\|\||\||\r?\n)\s*")
_WRITE_VERB = re.compile(
    r"(?ix)(?:^|[\s(;&|])(?:set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|rename-item|"
    r"clear-content|rm|rmdir|del|erase|mv|cp|copy|move|ren|rename|tee|touch|truncate|patch|robocopy|xcopy|"
    r"sed\s+-i|perl\s+-[a-z]*i|sc|ac|ni|ri|mi|cpi)(?=$|[\s(])")
_PY_WRITE = re.compile(r"write_text|write_bytes|\.write\(|open\([^)]*,\s*['\"][wax]|shutil\.|os\.(?:remove|rename|replace|unlink)|"
                       r"\.unlink\(|\.rename\(|\.replace\(|WriteAllText|WriteAllBytes|\[IO\.File\]")
_GIT_WRITE = re.compile(r"(?ix)\bgit\b((?:\s+-C\s+(?:\"[^\"]*\"|'[^']*'|\S+))?)\s+(?:-\S+\s+)*"
                        r"(checkout|restore|reset|apply|am|stash|rm|mv|commit|clean|cherry-pick|revert|merge|rebase|pull|switch|add)\b")
_INLINE = re.compile(r"(?i)(?:python[0-9.]*|\bpy|node)(?:\.exe)?['\"]?\s+(?:-[A-Za-z]+\s+)*-[ce]\b|<<|@['\"]\s*\r?\n|\|\s*(?:python|py|node)")
_REDIRECT = re.compile(r"(?<![<>0-9&=-])(?:[12*]?>>?)\s*(\"[^\"]+\"|'[^']+'|[^\s;|&<>)]+)")
_TOKEN = re.compile(r"\"([^\"]+)\"|'([^']+)'|([^\s\"'();|&<>=,]+)")


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))


class CodeGuard:
    def __init__(self, root: Path, allowed: Callable[[], Iterable[Path]]):
        self.root = Path(root)
        self.allowed = allowed   # callable: the data paths inside the app folder an agent may write

    # ---- settings for the hook (another process) --------------------------
    def settings(self) -> dict:
        return {"root": str(self.root), "allow": [str(p) for p in self.allowed()]}

    def env(self) -> dict[str, str]:
        return {ENV: json.dumps(self.settings(), ensure_ascii=True)}

    @classmethod
    def from_env(cls, value: str) -> "CodeGuard":
        s = json.loads(value)
        allow = [Path(p) for p in s.get("allow") or []]
        return cls(Path(s["root"]), lambda: allow)

    # ---- the rules ----------------------------------------------------------
    def _inside(self, p: str, base: str) -> bool:
        return p == base or p.startswith(base.rstrip("\\/") + os.sep)

    def blocked_path(self, path: str | Path, cwd: str | Path | None = None) -> bool:
        """True when `path` is the app's code: inside the app folder and in none of its data places."""
        if not str(path).strip():
            return False
        p = Path(os.path.expandvars(os.path.expanduser(str(path).strip())))
        if not p.is_absolute():
            p = Path(cwd or os.getcwd()) / p
        n, root = _norm(p), _norm(self.root)
        if not self._inside(n, root):
            return False
        return not any(self._inside(n, _norm(a)) for a in self.allowed())

    def data_path(self, path: str | Path, cwd: str | Path | None = None) -> bool:
        """`path` is one of the data places inside the app folder (the data file, resume-studio-data, .local…)."""
        p = Path(str(path).strip())
        if not p.is_absolute():
            p = Path(cwd or os.getcwd()) / p
        n = _norm(p)
        return any(self._inside(n, _norm(a)) for a in self.allowed())

    def safe_cwd(self) -> Path | None:
        """A folder of the user's data to run in, when the data file's own folder holds the code."""
        return next((Path(a) for a in self.allowed() if Path(a).is_dir() and not self.contains_code(a)), None)

    def contains_code(self, folder: str | Path) -> bool:
        """The folder holds the app's code (it is the app folder, or a folder above it)."""
        return self._inside(_norm(self.root), _norm(folder)) or self.blocked_path(folder)

    def _paths(self, text: str) -> list[str]:
        out = []
        for m in _TOKEN.finditer(text):
            tok = next(g for g in m.groups() if g is not None)
            if ("/" in tok or "\\" in tok or re.search(r"\.[A-Za-z0-9]{1,5}$", tok)) and not tok.startswith(("http:", "https:", "-")):
                out.append(tok.strip("`"))
        return out

    def blocked_command(self, command: str, cwd: str | Path | None = None) -> str | None:
        """The protected path a command writes to, if it reads like one that does; else None."""
        cmd = str(command or "")
        for m in _REDIRECT.finditer(cmd):
            target = m.group(1).strip("\"'")
            if target.lower() not in ("$null", "nul", "/dev/null") and self.blocked_path(target, cwd):
                return target
        # inline code (python -c, a heredoc, a here-string) is one program: a write call anywhere in it and a
        # protected path anywhere. A script run by its path (python scripts/hunt.py …) is not read here
        if _PY_WRITE.search(cmd) and _INLINE.search(cmd):
            for p in self._paths(cmd):
                if self.blocked_path(p, cwd):
                    return p
        for seg in _SEP.split(cmd):
            g = _GIT_WRITE.search(seg)
            if g:
                where = (g.group(1) or "").strip()
                where = re.sub(r"(?i)^-C\s+", "", where).strip("\"'") if where else str(cwd or os.getcwd())
                if self.contains_code(where) or self.blocked_path(where, cwd):
                    return where
                for p in self._paths(seg[g.end():]):
                    if self.blocked_path(p, where):
                        return p
            v = _WRITE_VERB.search(seg)
            if v:   # what follows the verb is what it writes (a path before it is the program being run)
                for p in self._paths(seg[v.start():]):
                    if self.blocked_path(p, cwd):
                        return p
        return None

    def check_tool(self, tool: str, inp: dict, cwd: str | Path | None = None) -> str | None:
        """A Claude Code tool call that would change the code: the path, else None."""
        if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
            return str(path) if self.blocked_path(path, cwd) else None
        if tool in ("Bash", "PowerShell"):
            return self.blocked_command(str(inp.get("command") or ""), cwd)
        return None


def refusal(path: str) -> str:
    return REFUSAL.format(path=path)
