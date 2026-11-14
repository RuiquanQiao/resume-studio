"""Claude Code PreToolUse hook: the window's chats never change Resume Studio's own code (core/codeguard.py).

Claude Code runs it before Edit / Write / NotebookEdit / Bash / PowerShell in every permission mode; exit 2
refuses the call and the reason goes back to the model. Its settings come in the RESUME_STUDIO_GUARD
environment variable the window sets for the chat; without it (a terminal session) it allows everything."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.codeguard import ENV, CodeGuard, refusal  # noqa: E402


def main() -> int:
    raw = os.environ.get(ENV)
    if not raw:
        return 0
    try:
        call = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        return 0
    guard = CodeGuard.from_env(raw)
    hit = guard.check_tool(str(call.get("tool_name") or ""), call.get("tool_input") or {}, call.get("cwd") or None)
    if not hit:
        return 0
    sys.stderr.buffer.write(refusal(hit).encode("utf-8"))
    sys.stderr.flush()
    return 2


if __name__ == "__main__":
    sys.exit(main())
