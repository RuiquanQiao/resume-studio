"""Archive, restore and delete a chat's session in the agent itself, through the agent's own protocol.

Codex has all three (`thread/archive`, `thread/unarchive`, `thread/delete`); ACP agents may answer
`session/delete` when they list `sessionCapabilities.delete`. Claude Code and Pi have no such call, so
deleting removes the transcript where they keep it. Archiving is the agent's own idea only where it has one:
elsewhere it hides the chat in this window and leaves the agent's files alone.
Any failure raises before the window's copy is touched."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import agents
from .drivers import _rpc_probe
from .i18n import t

CLIENT = {"name": "resume-studio", "title": "Resume Studio", "version": "1"}


def _claude_files(sid: str) -> list[Path]:
    """The transcript (`projects/<folder>/<id>.jsonl`) and its own folder (subagents, tool results)."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or agents.HOME / ".claude") / "projects"
    found = list(root.glob(f"*/{sid}.jsonl"))
    return found + [p.with_suffix("") for p in found if p.with_suffix("").is_dir()]


def _pi_files(sid: str) -> list[Path]:
    home = Path(os.environ.get("PI_CODING_AGENT_DIR") or agents.HOME / ".pi" / "agent")
    root = Path(os.environ.get("PI_CODING_AGENT_SESSION_DIR") or home / "sessions")
    return list(root.glob(f"*/*_{sid}.jsonl"))


def _delete_files(agent, sid: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", sid):
        raise RuntimeError("Invalid agent session ID.")
    paths = (_claude_files if agent.id == "claude" else _pi_files)(sid)
    for p in paths:
        shutil.rmtree(p) if p.is_dir() else p.unlink()


def operate(chat: dict, operation: str, cwd: Path) -> str | None:
    """operation: archive, unarchive or delete. Returns a note when the agent kept its copy."""
    sid = chat.get("session_id")
    agent = agents.get(chat.get("agent") or "claude")
    if not sid or (operation != "delete" and agent.protocol != "codex"):
        return
    if agent.protocol in ("claude", "pi"):
        _delete_files(agent, sid)
        return None
    command = agent.command()
    if not command:
        raise RuntimeError(f"{agent.name} is not installed; its session was not changed.")
    if agent.protocol == "codex":
        try:
            _rpc_probe(command + agent.args, cwd, 30, [("initialize", {"clientInfo": CLIENT}), ("initialized", None),
                                                        ("thread/" + operation, {"threadId": sid})], strict=True)
        except RuntimeError as e:
            if "active writer" in str(e):   # Codex locks a thread while a window has it open
                raise RuntimeError(t("This conversation is open in the Codex app. Close it there, then try again.")) from e
            raise
        return None
    answers = _rpc_probe(command + agent.args, cwd, 30, [("initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": CLIENT})])
    if not (((answers.get("initialize") or {}).get("agentCapabilities") or {}).get("sessionCapabilities") or {}).get("delete"):
        return t("{name} has no way to delete its own copy of a conversation, so it stays in {name}'s history.", name=agent.name)
    _rpc_probe(command + agent.args, cwd, 30, [("initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": CLIENT}),
                                               ("session/delete", {"sessionId": sid})], strict=True)
