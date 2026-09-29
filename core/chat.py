"""Real Claude Code conversations behind a UI.

Each chat is one Claude Code session. The first turn starts it; every later
turn runs `claude -p --resume <session_id>`, so the conversation history lives
in Claude Code itself instead of being flattened into one big prompt.

What we deliberately do NOT do (lessons from an earlier app that felt stiff):
  - no `--bare`, no `--system-prompt`: the user's CLAUDE.md, skills, memory and
    settings load exactly like in their terminal;
  - no forced reply language: Claude answers in whatever language the user writes;
  - tools stay on, so Claude can read files, edit the data and use skills.
The only addition is a short `--append-system-prompt-file` telling Claude what
the user is looking at in the UI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .events import EventBus

DEFAULT_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep", "Skill", "TodoWrite", "WebFetch", "WebSearch",
    "Bash(python *)", "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)",
]


def claude_command() -> list[str] | None:
    """argv prefix for Claude Code. STUDIO_CLAUDE_CMD (a JSON list) overrides it for tests."""
    override = os.environ.get("STUDIO_CLAUDE_CMD")
    if override:
        return list(json.loads(override))
    exe = shutil.which("claude")
    return [exe] if exe else None


def _now() -> float:
    return round(time.time(), 3)


class ChatStore:
    """Display copies of conversations, kept next to the user's data (.studio/chats)."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def list(self) -> list[dict]:
        with self.lock:
            try:
                items = json.loads(self._index_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                items = []
            return sorted(items, key=lambda c: c.get("updated", 0), reverse=True)

    def _write_index(self, items: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._index_path())

    def meta(self, chat_id: str) -> dict | None:
        return next((c for c in self.list() if c["id"] == chat_id), None)

    def create(self, title: str = "新对话") -> dict:
        with self.lock:
            items = self.list()
            chat = {"id": uuid.uuid4().hex[:10], "title": title, "session_id": None,
                    "created": _now(), "updated": _now()}
            items.append(chat)
            self._write_index(items)
            self.save_messages(chat["id"], [])
            return chat

    def update_meta(self, chat_id: str, **fields: Any) -> dict | None:
        with self.lock:
            items = self.list()
            for c in items:
                if c["id"] == chat_id:
                    c.update(fields)
                    c["updated"] = _now()
                    self._write_index(items)
                    return c
            return None

    def delete(self, chat_id: str) -> None:
        with self.lock:
            self._write_index([c for c in self.list() if c["id"] != chat_id])
            try:
                (self.root / f"{chat_id}.json").unlink()
            except OSError:
                pass

    def messages(self, chat_id: str) -> list[dict]:
        try:
            return json.loads((self.root / f"{chat_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save_messages(self, chat_id: str, msgs: list[dict]) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{chat_id}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)


class Turn:
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.blocks: list[dict] = []   # the assistant message being built
        self.tools: dict[str, dict] = {}


class ChatRunner:
    def __init__(self, bus: EventBus, store: ChatStore, cwd: Path, tools: list[str] | None = None):
        self.bus = bus
        self.store = store
        self.cwd = cwd
        self.tools = tools or DEFAULT_TOOLS
        self.turns: dict[str, Turn] = {}

    def available(self) -> bool:
        return claude_command() is not None

    def busy(self, chat_id: str) -> bool:
        return chat_id in self.turns

    def running(self) -> list[str]:
        return list(self.turns)

    # ---- public --------------------------------------------------------
    def send(self, chat_id: str, text: str, context: str = "", add_dirs: list[str] | None = None,
             meta: dict | None = None) -> None:
        if self.busy(chat_id):
            raise RuntimeError("这段对话还在回复中，请稍等或先停止。")
        prefix = claude_command()
        if not prefix:
            raise RuntimeError("找不到 claude 命令。请先安装并登录 Claude Code。")
        chat = self.store.meta(chat_id)
        if chat is None:
            raise KeyError(chat_id)

        msgs = self.store.messages(chat_id)
        msgs.append({"role": "user", "text": text, "at": _now(), "meta": meta or {}})
        self.store.save_messages(chat_id, msgs)
        if chat.get("title") in (None, "", "新对话"):
            self.store.update_meta(chat_id, title=_title_from(text))
        else:
            self.store.update_meta(chat_id)

        cmd = prefix + ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                        "--permission-mode", "acceptEdits", "--permission-prompts", "none",
                        "--allowedTools", ",".join(self.tools)]
        if chat.get("session_id"):
            cmd += ["--resume", chat["session_id"]]
        if context.strip():
            ctx_file = self.store.root / f"{chat_id}.context.md"
            ctx_file.write_text(context, encoding="utf-8")
            cmd += ["--append-system-prompt-file", str(ctx_file)]
        for d in add_dirs or []:
            if d and Path(d).exists():
                cmd += ["--add-dir", str(Path(d))]

        turn = Turn(chat_id)
        self.turns[chat_id] = turn
        self._emit("chat.start", chat_id, meta=meta or {})
        threading.Thread(target=self._run, args=(turn, cmd, text), daemon=True).start()

    def cancel(self, chat_id: str) -> None:
        turn = self.turns.get(chat_id)
        if not turn or not turn.proc:
            return
        turn.cancelled = True
        if os.name == "nt":  # claude is a .cmd shim on Windows; end the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(turn.proc.pid)], capture_output=True)
        else:
            turn.proc.terminate()

    def cancel_all(self) -> None:
        for chat_id in list(self.turns):
            self.cancel(chat_id)

    # ---- internals -----------------------------------------------------
    def _emit(self, event: str, chat_id: str, **data: Any) -> None:
        self.bus.publish(event, {"chat": chat_id, **data})

    def _run(self, turn: Turn, cmd: list[str], text: str) -> None:
        err_text, result_error, cost = "", None, None
        try:
            turn.proc = subprocess.Popen(
                cmd, cwd=str(self.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert turn.proc.stdin and turn.proc.stdout and turn.proc.stderr
            turn.proc.stdin.write(text.encode("utf-8"))
            turn.proc.stdin.close()
            stderr_chunks: list[bytes] = []
            threading.Thread(target=lambda: stderr_chunks.append(turn.proc.stderr.read()), daemon=True).start()
            for raw in turn.proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                res = self._handle(turn, msg)
                if res:
                    result_error = res.get("error")
                    cost = res.get("cost")
            code = turn.proc.wait()
            time.sleep(0.05)
            err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            if code != 0 and not result_error and not turn.cancelled:
                result_error = err_text or f"claude 退出码 {code}"
        except OSError as e:
            result_error = f"启动 claude 失败：{e}"
        finally:
            self._finish(turn, result_error, cost)

    def _finish(self, turn: Turn, error: str | None, cost: Any) -> None:
        for t in turn.tools.values():
            if t["status"] == "running":
                t["status"] = "cancelled" if turn.cancelled else "unknown"
        msgs = self.store.messages(turn.chat_id)
        msgs.append({"role": "assistant", "blocks": turn.blocks, "at": _now(),
                     "error": None if turn.cancelled else error, "cancelled": turn.cancelled})
        self.store.save_messages(turn.chat_id, msgs)
        self.store.update_meta(turn.chat_id)
        self.turns.pop(turn.chat_id, None)
        self._emit("chat.done", turn.chat_id, ok=not error and not turn.cancelled,
                   error=error, cancelled=turn.cancelled, cost=cost)

    def _text(self, turn: Turn, delta: str) -> None:
        if turn.blocks and turn.blocks[-1]["type"] == "text":
            turn.blocks[-1]["text"] += delta
        else:
            turn.blocks.append({"type": "text", "text": delta})
        self._emit("chat.delta", turn.chat_id, text=delta)

    def _handle(self, turn: Turn, msg: dict) -> dict | None:
        mtype = msg.get("type")
        if mtype == "system" and msg.get("subtype") == "init":
            sid = msg.get("session_id")
            if sid:
                self.store.update_meta(turn.chat_id, session_id=sid, model=msg.get("model"))
            self._emit("chat.init", turn.chat_id, model=msg.get("model"))
        elif mtype == "stream_event":
            ev = msg.get("event") or {}
            if msg.get("parent_tool_use_id"):
                return None  # subagent chatter stays out of the main transcript
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self._text(turn, delta.get("text", ""))
        elif mtype == "assistant":
            if msg.get("parent_tool_use_id"):
                return None
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_use":
                    continue
                inp = block.get("input") or {}
                tool = {"type": "tool", "id": block.get("id"), "name": block.get("name"),
                        "hint": _tool_hint(block.get("name"), inp), "status": "running", "result": ""}
                turn.tools[tool["id"]] = tool
                turn.blocks.append(tool)
                self._emit("chat.tool", turn.chat_id, tool=tool)
        elif mtype == "user":
            for block in (msg.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool = turn.tools.get(block.get("tool_use_id"))
                if not tool:
                    continue
                tool["status"] = "error" if block.get("is_error") else "done"
                tool["result"] = _result_preview(block.get("content"))
                self._emit("chat.tool", turn.chat_id, tool=tool)
        elif mtype == "result":
            sid = msg.get("session_id")
            if sid:
                self.store.update_meta(turn.chat_id, session_id=sid)
            if not turn.blocks and msg.get("result"):  # nothing streamed: fall back to the final text
                self._text(turn, msg["result"])
            error = (msg.get("result") or "Claude 报告了错误") if msg.get("is_error") else None
            return {"error": error, "cost": msg.get("total_cost_usd")}
        return None


def _title_from(text: str) -> str:
    line = " ".join(text.strip().split())
    return (line[:28] + "…") if len(line) > 28 else (line or "新对话")


def _tool_hint(name: str | None, inp: dict) -> str:
    for key in ("file_path", "path", "pattern", "command", "skill", "url", "query", "description"):
        if inp.get(key):
            return str(inp[key])[:160]
    return ""


def _result_preview(content: Any) -> str:
    if isinstance(content, list):
        content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    text = str(content or "").strip()
    return text[:400] + ("…" if len(text) > 400 else "")
