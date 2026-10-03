"""Real Claude Code conversations behind a UI.

Each chat is one Claude Code session. A reply runs `claude -p` with stream-json in AND
out, the same channel Claude Code's own SDK uses, so the UI gets what the terminal has:
  - permission prompts come to the UI as cards (allow / deny / always allow), instead of
    being silently refused;
  - Claude's multiple-choice questions (AskUserQuestion) and plan approval (ExitPlanMode);
  - stop = a real interrupt, the session stays consistent;
  - images in the message, model / effort / permission mode per chat;
  - messages typed while Claude works are queued and sent after the current reply;
  - context-window use and the plan's usage limits;
  - fork a chat from any earlier message (`--fork-session --resume-session-at`).
Later turns run `--resume <session_id>`, so the history lives in Claude Code itself.

What we deliberately do NOT do (lessons from an earlier app that felt stiff):
  - no `--bare`, no `--system-prompt`: the user's CLAUDE.md, skills, memory and
    settings load exactly like in their terminal;
  - no forced reply language: Claude answers in whatever language the user writes.
The only addition is a short `--append-system-prompt-file` telling Claude what
the user is looking at in the UI.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .claude_info import EFFORTS, valid_model_id
from .events import EventBus

# Pre-approved so the usual work (reading, rendering, git status) never stops for a card.
# Edits are not listed: the permission mode decides them, like in the terminal.
DEFAULT_TOOLS = [
    "Read", "Glob", "Grep", "Skill", "WebFetch", "WebSearch",
    "Bash(python *)", "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)",
]

# Permission modes the picker offers (None = whatever the user's Claude Code settings say).
MODES = ["default", "acceptEdits", "plan", "auto"]
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def valid_model(value: Any) -> bool:
    return value is None or valid_model_id(value)


def valid_effort(value: Any) -> bool:
    return value is None or value in EFFORTS


def valid_mode(value: Any) -> bool:
    return value is None or value in MODES


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

    def create(self, title: str = "新对话", **fields: Any) -> dict:
        with self.lock:
            items = self.list()
            chat = {"id": uuid.uuid4().hex[:10], "title": title, "session_id": None,
                    "model_choice": None, "effort": None, "mode": None,
                    "created": _now(), "updated": _now(), **fields}
            items.append(chat)
            self._write_index(items)
            self.save_messages(chat["id"], [])
            return chat

    def update_meta(self, chat_id: str, touch: bool = True, **fields: Any) -> dict | None:
        with self.lock:
            items = self.list()
            for c in items:
                if c["id"] == chat_id:
                    c.update(fields)
                    if touch:
                        c["updated"] = _now()
                    self._write_index(items)
                    return c
            return None

    def delete(self, chat_id: str) -> None:
        with self.lock:
            self._write_index([c for c in self.list() if c["id"] != chat_id])
            for p in (self.root / f"{chat_id}.json", self.root / f"{chat_id}.context.md"):
                try:
                    p.unlink()
                except OSError:
                    pass
            shutil.rmtree(self.files_dir(chat_id), ignore_errors=True)

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

    def append(self, chat_id: str, msg: dict) -> None:
        with self.lock:
            msgs = self.messages(chat_id)
            msgs.append(msg)
            self.save_messages(chat_id, msgs)

    # ---- attachments: pasted / dropped files are kept next to the chat --------
    def files_dir(self, chat_id: str) -> Path:
        return self.root / "files" / chat_id

    def save_file(self, chat_id: str, name: str, data: bytes) -> Path:
        safe = re.sub(r"[^\w.\-一-鿿]+", "_", Path(name).name)[-80:] or "file"
        folder = self.files_dir(chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex[:6]}-{safe}"
        path.write_bytes(data)
        return path


class Turn:
    """One running `claude` process: the reply in progress plus anything queued behind it."""

    def __init__(self, chat_id: str, on_done: Any = None):
        self.chat_id = chat_id
        self.on_done = on_done         # callable(ok: bool), run after the turn is saved
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.closed = False            # stdin closed: no more messages can go in
        self.wlock = threading.Lock()
        self.blocks: list[dict] = []   # the assistant message being built
        self.tools: dict[str, dict] = {}
        self.asks: dict[str, dict] = {}   # request_id -> pending card block
        self.queue: list[dict] = []    # user messages waiting for the current reply to finish
        self.last_uuid: str | None = None
        self.usage: dict | None = None
        self.error: str | None = None
        self.flushed_any = False

    def write(self, obj: dict) -> bool:
        with self.wlock:
            if self.closed or not self.proc or not self.proc.stdin:
                return False
            try:
                self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def close(self) -> None:
        with self.wlock:
            if self.closed:
                return
            self.closed = True
            try:
                if self.proc and self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass


def user_content(text: str, images: list[dict]) -> Any:
    """The Messages API content for one user message: plain text, or text plus image blocks."""
    if not images:
        return text
    blocks: list[dict] = [{"type": "text", "text": text or "（见图片）"}]
    for im in images:
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": im["media_type"],
                                                   "data": base64.b64encode(Path(im["path"]).read_bytes()).decode()}})
    return blocks


class ChatRunner:
    def __init__(self, bus: EventBus, store: ChatStore, cwd: Path, tools: list[str] | None = None):
        self.bus = bus
        self.store = store
        self.cwd = cwd
        self.tools = tools or DEFAULT_TOOLS
        self.turns: dict[str, Turn] = {}
        self.limits: dict = {}         # the plan's usage windows, from Claude Code's rate_limit_event

    def available(self) -> bool:
        return claude_command() is not None

    def busy(self, chat_id: str) -> bool:
        return chat_id in self.turns

    def running(self) -> list[str]:
        return list(self.turns)

    def waiting(self) -> list[str]:
        """Chats where Claude is waiting for the user (a permission, a question, a plan)."""
        return [cid for cid, t in self.turns.items() if any(a["status"] == "pending" for a in t.asks.values())]

    def live(self, chat_id: str) -> dict | None:
        turn = self.turns.get(chat_id)
        if not turn:
            return None
        return {"blocks": turn.blocks, "queue": turn.queue}

    # ---- public --------------------------------------------------------
    def send(self, chat_id: str, text: str, context: str = "", add_dirs: list[str] | None = None,
             meta: dict | None = None, on_done: Any = None, images: list[dict] | None = None,
             files: list[dict] | None = None, shown: str | None = None) -> dict:
        """Start a reply, or queue the message behind the running one. Returns {"queued": bool}.

        `text` goes to Claude; `shown` (default: the same) is what the transcript displays."""
        chat = self.store.meta(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        msg = {"role": "user", "text": text if shown is None else shown, "at": _now(), "meta": meta or {},
               "files": [{"name": f["name"], "path": f["path"], "image": f.get("image", False)} for f in files or []]}
        images = images or []
        turn = self.turns.get(chat_id)
        if turn and (turn.closed or turn.cancelled):
            # the reply is over and its process is exiting (or being stopped): wait for it, then start fresh
            end = time.time() + 6
            while chat_id in self.turns and time.time() < end:
                time.sleep(0.05)
            turn = self.turns.get(chat_id)
        if turn:
            if on_done or (meta or {}).get("action") or turn.cancelled or turn.closed:
                raise RuntimeError("这段对话还在回复中，请稍等或先停止。")
            item = {"id": uuid.uuid4().hex[:8], "msg": msg, "prompt": text, "images": images}
            turn.queue.append(item)
            self._emit("chat.queue", chat_id, queue=turn.queue)
            return {"queued": True, "id": item["id"]}

        prefix = claude_command()
        if not prefix:
            raise RuntimeError("找不到 claude 命令。请先安装并登录 Claude Code。")
        self.store.append(chat_id, msg)
        if chat.get("title") in (None, "", "新对话"):
            self.store.update_meta(chat_id, title=_title_from(text))
        else:
            self.store.update_meta(chat_id)

        cmd = prefix + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                        "--include-partial-messages", "--permission-prompt-tool", "stdio",
                        "--allowedTools", ",".join(self.tools)]
        if chat.get("mode"):
            cmd += ["--permission-mode", chat["mode"]]
        if chat.get("model_choice"):
            cmd += ["--model", chat["model_choice"]]
        if chat.get("effort"):
            cmd += ["--effort", chat["effort"]]
        fork = chat.get("fork") or {}
        if chat.get("session_id"):
            cmd += ["--resume", chat["session_id"]]
        elif fork.get("session_id"):
            cmd += ["--resume", fork["session_id"], "--fork-session"]
            if fork.get("at"):
                cmd += ["--resume-session-at", fork["at"]]
        if context.strip():
            ctx_file = self.store.root / f"{chat_id}.context.md"
            ctx_file.write_text(context, encoding="utf-8")
            cmd += ["--append-system-prompt-file", str(ctx_file)]
        dirs = list(add_dirs or [])
        for f in files or []:
            dirs.append(str(Path(f["path"]).parent))
        for d in dict.fromkeys(dirs):
            if d and Path(d).exists():
                cmd += ["--add-dir", str(Path(d))]

        turn = Turn(chat_id, on_done)
        self.turns[chat_id] = turn
        self._emit("chat.start", chat_id, meta=meta or {})
        threading.Thread(target=self._run, args=(turn, cmd, user_content(text, images)), daemon=True).start()
        return {"queued": False}

    def unqueue(self, chat_id: str, item_id: str) -> bool:
        turn = self.turns.get(chat_id)
        if not turn:
            return False
        before = len(turn.queue)
        turn.queue[:] = [q for q in turn.queue if q["id"] != item_id]
        self._emit("chat.queue", chat_id, queue=turn.queue)
        return len(turn.queue) != before

    def answer(self, chat_id: str, request_id: str, decision: dict) -> dict:
        """The user's answer to a card: a permission, Claude's question, or its plan."""
        turn = self.turns.get(chat_id)
        ask = turn.asks.get(request_id) if turn else None
        if not ask or ask["status"] != "pending":
            raise KeyError(request_id)
        kind = ask["kind"]
        inp = ask["input"]
        new_mode = None
        if kind == "question":
            answers = {str(k): str(v) for k, v in (decision.get("answers") or {}).items()}
            if decision.get("skip"):
                resp = {"behavior": "deny", "message": "用户没有回答这个问题，跳过了。"}
                ask["status"] = "denied"
            else:
                resp = {"behavior": "allow", "updatedInput": {**inp, "answers": answers}}
                ask["status"], ask["answers"] = "answered", answers
        elif kind == "plan":
            if decision.get("approve"):
                new_mode = decision.get("mode") if decision.get("mode") in ("acceptEdits", "default", "auto") else "default"
                resp = {"behavior": "allow", "updatedInput": inp,
                        "updatedPermissions": [{"type": "setMode", "mode": new_mode, "destination": "session"}]}
                ask["status"], ask["mode"] = "approved", new_mode
            else:
                feedback = str(decision.get("feedback") or "").strip()
                resp = {"behavior": "deny", "message": "用户希望继续完善计划，暂不执行。" + (f"用户的意见：{feedback}" if feedback else "")}
                ask["status"], ask["feedback"] = "revise", feedback
        else:
            if decision.get("allow"):
                resp = {"behavior": "allow", "updatedInput": inp}
                sug = ask.get("suggestions") or []
                pick = decision.get("suggestion")
                if isinstance(pick, int) and 0 <= pick < len(sug):
                    resp["updatedPermissions"] = [sug[pick]]
                    if sug[pick].get("type") == "setMode":
                        new_mode = sug[pick].get("mode")
                    ask["always"] = pick
                ask["status"] = "allowed"
            else:
                msg = str(decision.get("message") or "").strip()
                resp = {"behavior": "deny", "message": msg or "用户拒绝了这个操作。"}
                ask["status"] = "denied"
        turn.write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": resp}})
        if new_mode and new_mode in MODES:   # the next reply keeps the mode the user just chose
            self.store.update_meta(chat_id, touch=False, mode=new_mode)
            self._emit("chat.meta", chat_id, meta=self.store.meta(chat_id))
        self._emit("chat.ask", chat_id, ask=ask)
        return ask

    def set_live(self, chat_id: str, model: str | None = None, mode: str | None = None) -> None:
        """Apply a picker change to the reply in progress too (Claude Code's own control requests)."""
        turn = self.turns.get(chat_id)
        if not turn:
            return
        if model is not None:
            turn.write({"type": "control_request", "request_id": uuid.uuid4().hex,
                        "request": {"subtype": "set_model", "model": model or "default"}})
        if mode is not None:
            turn.write({"type": "control_request", "request_id": uuid.uuid4().hex,
                        "request": {"subtype": "set_permission_mode", "mode": mode}})

    def cancel(self, chat_id: str) -> None:
        turn = self.turns.get(chat_id)
        if not turn or not turn.proc:
            return
        turn.cancelled = True
        turn.queue.clear()
        for ask in turn.asks.values():
            if ask["status"] == "pending":
                ask["status"] = "cancelled"
        # a real interrupt first (the session stays consistent); kill if it does not stop
        sent = turn.write({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}})
        turn.close()
        threading.Timer(4.0 if sent else 0.0, self._kill, args=(turn,)).start()

    def _kill(self, turn: Turn) -> None:
        if not turn.proc or turn.proc.poll() is not None:
            return
        if os.name == "nt":  # claude may be a .cmd shim on Windows; end the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(turn.proc.pid)], capture_output=True)
        else:
            turn.proc.terminate()

    def cancel_all(self) -> None:
        for chat_id in list(self.turns):
            turn = self.turns.get(chat_id)
            if turn:
                turn.cancelled = True
                turn.close()
                self._kill(turn)

    # ---- internals -----------------------------------------------------
    def _emit(self, event: str, chat_id: str, **data: Any) -> None:
        self.bus.publish(event, {"chat": chat_id, **data})

    def _run(self, turn: Turn, cmd: list[str], content: Any) -> None:
        cost = None
        try:
            turn.proc = subprocess.Popen(
                cmd, cwd=str(self.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert turn.proc.stdout and turn.proc.stderr
            stderr_chunks: list[bytes] = []
            threading.Thread(target=lambda: stderr_chunks.append(turn.proc.stderr.read()), daemon=True).start()
            turn.write({"type": "user", "message": {"role": "user", "content": content}})
            for raw in turn.proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                res = self._handle(turn, msg)
                if res is not None:
                    cost = res.get("cost")
                    self._flush(turn, res.get("error"), cost)
                    if not self._next(turn):
                        turn.close()
            code = turn.proc.wait()
            time.sleep(0.05)
            err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            if code != 0 and not turn.error and not turn.cancelled and not turn.flushed_any:
                turn.error = err_text or f"claude 退出码 {code}"
        except OSError as e:
            turn.error = f"启动 claude 失败：{e}"
        finally:
            self._finish(turn, cost)

    def _next(self, turn: Turn) -> bool:
        """After a reply: send the next queued message into the same process."""
        if turn.cancelled or not turn.queue:
            return False
        item = turn.queue.pop(0)
        self.store.append(turn.chat_id, item["msg"])
        self.store.update_meta(turn.chat_id)
        self._emit("chat.next", turn.chat_id, message=item["msg"], queue=turn.queue)
        return turn.write({"type": "user", "message": {"role": "user", "content": user_content(item["prompt"], item["images"])}})

    def _flush(self, turn: Turn, error: str | None, cost: Any) -> None:
        """One reply is complete: save it and start a fresh assistant message."""
        for t in turn.tools.values():
            if t["status"] == "running":
                t["status"] = "cancelled" if turn.cancelled else "unknown"
        for a in turn.asks.values():
            if a["status"] == "pending":
                a["status"] = "cancelled"
        self.store.append(turn.chat_id, {"role": "assistant", "blocks": turn.blocks, "at": _now(),
                                         "error": None if turn.cancelled else error, "cancelled": turn.cancelled,
                                         "uuid": turn.last_uuid, "cost": cost})
        turn.error = turn.error or error
        turn.flushed_any = True
        turn.blocks, turn.tools, turn.asks = [], {}, {}
        if turn.usage:
            self.store.update_meta(turn.chat_id, touch=False, context=turn.usage)
        self._emit("chat.reply", turn.chat_id, error=None if turn.cancelled else error,
                   context=turn.usage, queue=turn.queue)

    def _finish(self, turn: Turn, cost: Any) -> None:
        if turn.blocks or not turn.flushed_any:   # crashed, cancelled or never answered
            self._flush(turn, None if turn.cancelled else turn.error, cost)
        self.store.update_meta(turn.chat_id)
        self.turns.pop(turn.chat_id, None)
        ok = not turn.error and not turn.cancelled
        if turn.on_done:
            try:
                turn.on_done(ok)
            except Exception:  # noqa: BLE001 - a follow-up must never break the chat
                pass
        self._emit("chat.done", turn.chat_id, ok=ok, error=None if turn.cancelled else turn.error,
                   cancelled=turn.cancelled, cost=cost)

    def _text(self, turn: Turn, delta: str, kind: str = "text") -> None:
        if turn.blocks and turn.blocks[-1]["type"] == kind:
            turn.blocks[-1]["text"] += delta
        else:
            turn.blocks.append({"type": kind, "text": delta})
        self._emit("chat.delta", turn.chat_id, text=delta, kind=kind)

    def _block(self, turn: Turn, block: dict) -> None:
        turn.blocks.append(block)
        self._emit("chat.block", turn.chat_id, block=block)

    def _ask(self, turn: Turn, msg: dict) -> None:
        req = msg.get("request") or {}
        rid = msg.get("request_id")
        if req.get("subtype") != "can_use_tool":
            turn.write({"type": "control_response", "response": {"subtype": "error", "request_id": rid,
                                                                 "error": f"Resume Studio 不支持 {req.get('subtype')}"}})
            return
        name = req.get("tool_name") or ""
        kind = "question" if name == "AskUserQuestion" else "plan" if name == "ExitPlanMode" else "permission"
        ask = {"type": "ask", "id": rid, "kind": kind, "tool": name, "input": req.get("input") or {},
               "hint": _tool_hint(name, req.get("input") or {}), "desc": req.get("description") or "",
               "suggestions": req.get("permission_suggestions") or [], "tool_use_id": req.get("tool_use_id"),
               "status": "pending"}
        turn.asks[rid] = ask
        turn.blocks.append(ask)
        self._emit("chat.ask", turn.chat_id, ask=ask)

    def _tasks(self, turn: Turn, name: str, inp: dict, result: Any = None, tool_use_id: str = "") -> None:
        """Keep the chat's task list (TaskCreate / TaskUpdate / TodoWrite) in its meta."""
        meta = self.store.meta(turn.chat_id) or {}
        tasks: list[dict] = list(meta.get("tasks") or [])
        if name == "TodoWrite" and isinstance(inp.get("todos"), list):
            tasks = [{"id": str(i + 1), "subject": t.get("content") or "", "active": t.get("activeForm") or "",
                      "status": t.get("status") or "pending"} for i, t in enumerate(inp["todos"])]
        elif name == "TaskCreate" and isinstance(result, dict) and isinstance(result.get("task"), dict):
            tid = str(result["task"].get("id"))
            tasks = [t for t in tasks if t["id"] != tid]
            tasks.append({"id": tid, "subject": result["task"].get("subject") or inp.get("subject") or "",
                          "active": inp.get("activeForm") or "", "status": "pending"})
        elif name == "TaskUpdate" and inp.get("taskId") is not None:
            tid = str(inp["taskId"])
            for t in tasks:
                if t["id"] == tid:
                    if inp.get("status"):
                        t["status"] = inp["status"]
                    if inp.get("subject"):
                        t["subject"] = inp["subject"]
                    if inp.get("activeForm"):
                        t["active"] = inp["activeForm"]
            tasks = [t for t in tasks if t["status"] != "deleted"]
        else:
            return
        self.store.update_meta(turn.chat_id, touch=False, tasks=tasks)
        self._emit("chat.tasks", turn.chat_id, tasks=tasks)

    def _handle(self, turn: Turn, msg: dict) -> dict | None:
        mtype = msg.get("type")
        if mtype == "system":
            sub = msg.get("subtype")
            if sub == "init":
                fields = {"model": msg.get("model"), "mode_actual": msg.get("permissionMode")}
                if msg.get("session_id"):
                    fields.update(session_id=msg["session_id"], fork=None)
                self.store.update_meta(turn.chat_id, touch=False, **fields)
                self._emit("chat.init", turn.chat_id, model=msg.get("model"), mode=msg.get("permissionMode"))
            elif sub == "compact_boundary":
                self._block(turn, {"type": "note", "text": "上下文已压缩"})
            elif sub == "thinking_tokens":
                self._emit("chat.thinking", turn.chat_id, tokens=msg.get("estimated_tokens"))
        elif mtype == "control_request":
            self._ask(turn, msg)
        elif mtype == "control_cancel_request":
            ask = turn.asks.get(msg.get("request_id"))
            if ask and ask["status"] == "pending":
                ask["status"] = "cancelled"
                self._emit("chat.ask", turn.chat_id, ask=ask)
        elif mtype == "rate_limit_event":
            info = msg.get("rate_limit_info") or {}
            self.limits = {"status": info.get("status"), "windows": info.get("unifiedWindows") or {},
                           "type": info.get("rateLimitType"), "resets": info.get("resetsAt"), "at": _now()}
            self.bus.publish("claude.limits", self.limits)
        elif mtype == "stream_event":
            ev = msg.get("event") or {}
            if msg.get("parent_tool_use_id"):
                return None  # subagent chatter stays out of the main transcript
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta":
                if delta.get("type") == "text_delta":
                    self._text(turn, delta.get("text", ""))
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    self._text(turn, delta["thinking"], "thinking")
        elif mtype == "assistant":
            if msg.get("parent_tool_use_id"):
                return None
            message = msg.get("message") or {}
            if msg.get("uuid"):
                turn.last_uuid = msg["uuid"]
            usage = message.get("usage") or {}
            if usage and message.get("model") != "<synthetic>":
                used = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                            "cache_read_input_tokens", "output_tokens"))
                turn.usage = {**(turn.usage or {}), "used": used, "model": message.get("model")}
            for block in message.get("content") or []:
                if block.get("type") == "text" and message.get("model") == "<synthetic>" and block.get("text"):
                    self._text(turn, block["text"])   # local commands (/context, /cost…) answer without streaming
                if block.get("type") != "tool_use":
                    continue
                inp = block.get("input") or {}
                tool = {"type": "tool", "id": block.get("id"), "name": block.get("name"),
                        "hint": _tool_hint(block.get("name"), inp), "status": "running", "result": "",
                        "input": _small_input(block.get("name"), inp)}
                turn.tools[tool["id"]] = tool
                turn.blocks.append(tool)
                self._emit("chat.tool", turn.chat_id, tool=tool)
                if block.get("name") in ("TodoWrite", "TaskUpdate"):
                    self._tasks(turn, block["name"], inp)
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
                if tool["name"] == "TaskCreate":
                    self._tasks(turn, "TaskCreate", tool.get("input") or {}, msg.get("tool_use_result"))
        elif mtype == "result":
            sid = msg.get("session_id")
            if sid:
                self.store.update_meta(turn.chat_id, touch=False, session_id=sid)
            if not any(b["type"] == "text" for b in turn.blocks) and msg.get("result") and not msg.get("is_error"):
                self._text(turn, msg["result"])   # nothing streamed: fall back to the final text
            usage = msg.get("modelUsage") or {}
            model = (turn.usage or {}).get("model")
            mu = usage.get(model) if model in usage else next(iter(usage.values()), None) if usage else None
            if mu and mu.get("contextWindow"):
                turn.usage = {**(turn.usage or {}), "window": mu["contextWindow"]}
            error = None
            if msg.get("is_error") or msg.get("subtype", "success") not in ("success", None):
                error = msg.get("result") or "Claude 报告了错误"
                if turn.cancelled:
                    error = None
            return {"error": error, "cost": msg.get("total_cost_usd")}
        return None


def _title_from(text: str) -> str:
    line = " ".join(text.strip().split())
    return (line[:28] + "…") if len(line) > 28 else (line or "新对话")


def _tool_hint(name: str | None, inp: dict) -> str:
    for key in ("file_path", "path", "pattern", "command", "skill", "url", "query", "subject", "description"):
        if inp.get(key):
            return str(inp[key])[:160]
    return ""


def _small_input(name: str | None, inp: dict) -> dict:
    """What the UI needs from a tool's input (kept small; the transcript is stored)."""
    keep = {k: inp[k] for k in ("subject", "activeForm", "taskId", "status") if k in inp}
    if name == "TaskCreate" and inp.get("subject"):
        keep["subject"] = str(inp["subject"])[:200]
    return keep


def _result_preview(content: Any) -> str:
    if isinstance(content, list):
        content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    text = str(content or "").strip()
    return text[:400] + ("…" if len(text) > 400 else "")
