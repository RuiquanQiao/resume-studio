"""Provider session lifecycle operations. Never discard the display copy on failure."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from pathlib import Path

from . import agents
from .i18n import t


def _local_session(agent, sid, operation):
    # Only exact, validated session IDs and files within the provider's own session root.
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", sid):
        raise RuntimeError("Invalid agent session ID.")
    if agent.id == "claude":
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        root, pattern = home / "projects", sid + ".jsonl"
    else:
        home = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")
        root = Path(os.environ.get("PI_CODING_AGENT_SESSION_DIR") or home / "sessions")
        pattern = "*_" + sid + ".jsonl"
    archive = root.parent / (root.name + "-resume-studio-archive")
    source_root = archive if operation in ("unarchive", "delete_archived") else root
    paths = [p for p in source_root.glob("*/" + pattern) if p.is_file() and not p.is_symlink()
             and p.resolve().is_relative_to(source_root.resolve())]
    if len(paths) != 1:
        raise RuntimeError(f"Expected one {agent.name} session file; found {len(paths)}. Nothing was changed.")
    source = paths[0]
    if operation in ("delete", "delete_archived"):
        source.unlink()
        return
    destination_root = root if operation == "unarchive" else archive
    destination = destination_root / source.relative_to(source_root)
    if destination.exists():
        raise RuntimeError("The destination session file already exists. Nothing was changed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def _rpc(command, cwd, init, method, params, *, codex=False):
    proc = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = queue.Queue()
    def read():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    def call(request_id, name, data):
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": name, "params": data}) + "\n").encode())
        proc.stdin.flush()
        while True:
            try:
                line = lines.get(timeout=20)
            except queue.Empty:
                raise RuntimeError("The agent did not respond to the session operation.")
            if line is None:
                raise RuntimeError("The agent exited before confirming the session operation.")
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if reply.get("id") == request_id:
                if reply.get("error"):
                    raise RuntimeError(str(reply["error"].get("message") or reply["error"]))
                return reply.get("result") or {}
    try:
        result = call(1, "initialize", init)
        if codex:
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"initialized"}\n')
            proc.stdin.flush()
        else:
            caps = (result.get("agentCapabilities") or {}).get("sessionCapabilities") or {}
            operation = method.split("/")[-1]
            capability = "archive" if operation == "unarchive" else operation
            if capability not in caps or caps[capability] is False or caps[capability] is None:
                raise RuntimeError(t("This agent does not support session {operation} through its protocol.", operation=operation))
        return call(2, method, params)
    finally:
        proc.kill()
        proc.wait()


def operate(chat, operation, cwd):
    sid = chat.get("session_id")
    if not sid:
        return
    agent = agents.get(chat.get("agent") or "claude")
    if agent.id in ("claude", "pi"):
        if operation == "delete" and chat.get("archived"):
            _local_session(agent, sid, "delete_archived")
        else:
            _local_session(agent, sid, operation)
        return
    command = agent.command()
    if not command:
        raise RuntimeError(f"{agent.name} is not installed; its session was not changed.")
    if agent.protocol == "codex":
        init = {"clientInfo": {"name": "resume-studio", "title": "Resume Studio", "version": "1"},
                "capabilities": {"experimentalApi": True}}
        _rpc(command + agent.args, cwd, init, "thread/" + operation, {"threadId": sid}, codex=True)
    elif agent.protocol == "acp":
        init = {"protocolVersion": 1, "clientCapabilities": {},
                "clientInfo": {"name": "resume-studio", "title": "Resume Studio", "version": "1"}}
        _rpc(command + agent.args, cwd, init, "session/" + operation, {"sessionId": sid})
    else:
        raise RuntimeError(f"{agent.name} does not expose a verified {operation} operation. Its session was not changed.")
