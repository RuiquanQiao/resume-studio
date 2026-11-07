"""A short name for a chat the user started by typing, written by the chat's own agent.

Claude Code names its interactive sessions itself (an `ai-title` line in the transcript, by a small model), but
never one run with `-p`, which is how this app runs it: none of the 127 SDK sessions in the user's folder had one
(2026-11-08). So the app asks the same way the CLI does, once, right after the first message: the user's own
command line tool in its one-shot mode, on its smallest model, with no tools and nothing kept. The first
message's start is the name until the answer comes (and stays the name when an agent has no one-shot mode, or
the ask fails). A button's chat is named by the button (core/chat.py `send(title=…)`), never here.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from . import agents

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT = 90

PROMPT = """Write a title for a conversation that starts with the user message below.
The title says what the conversation is about, in at most 6 words (at most 16 characters in Chinese, Japanese
or Korean), in the language the message is written in. No quotes, no ending punctuation, no "Title:".
Reply with the title only.

<message>
{text}
</message>"""


def _claude(prefix: list[str], out: Path) -> list[str]:
    # its own naming uses a small model too; no tools, no session kept, one turn
    return prefix + ["-p", "--model", "haiku", "--output-format", "text", "--no-session-persistence",
                     "--max-turns", "1", "--tools", ""]


def _codex(prefix: list[str], out: Path) -> list[str]:
    return prefix + ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                     "-c", 'model_reasoning_effort="low"', "-o", str(out), "-"]


# agents with a one-shot mode this app knows; the others keep the first message's start
ONE_SHOT: dict[str, Callable[[list[str], Path], list[str]]] = {"claude": _claude, "codex": _codex}


def clean(raw: str) -> str:
    """The answer's first line, without quotes, a "Title:" label or ending punctuation."""
    line = next((x.strip() for x in str(raw or "").splitlines() if x.strip()), "")
    line = re.sub(r"^(title|标题|タイトル|제목)\s*[:：]\s*", "", line, flags=re.I)
    line = line.strip(" \t\"'“”‘’「」『』《》*`#").rstrip("。.!！?？;；,，:：")
    line = " ".join(line.split())
    return line[:60]


def ask(agent_id: str, text: str, cwd: Path) -> str:
    """The agent's title for a conversation starting with `text`; "" when it has none to give."""
    build = ONE_SHOT.get(agent_id)
    prefix = agents.get(agent_id).command() if build else None
    if not build or not prefix:
        return ""
    cwd.mkdir(parents=True, exist_ok=True)   # an empty folder: no project instructions (CLAUDE.md, AGENTS.md) to load
    fd, name = tempfile.mkstemp(prefix="title-", suffix=".txt", dir=cwd)
    os.close(fd)
    out = Path(name)
    try:
        proc = subprocess.run(build(prefix, out), input=PROMPT.format(text=text.strip()[:2000]), cwd=str(cwd),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
                              env={**os.environ, "PYTHONUTF8": "1"}, creationflags=NO_WINDOW)
        if proc.returncode != 0:
            return ""
        written = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        return clean(written.strip() or proc.stdout)
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        try:
            out.unlink()
        except OSError:
            pass
