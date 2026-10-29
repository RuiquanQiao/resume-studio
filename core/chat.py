"""Real agent conversations behind a UI: Claude Code, Codex, Qwen Code, Pi, DeepSeek Harness…

Each chat is one session of the user's own coding agent (core/agents.py), picked per chat.
A reply runs the agent's command line in its machine mode, the same channel its own SDK or
editor integration uses (core/drivers.py), so the UI gets what the terminal has:
  - permission prompts come to the UI as cards (allow / deny / always allow), instead of
    being silently refused;
  - the agent's multiple-choice questions and plan approval;
  - stop = a real interrupt, the session stays consistent;
  - images in the message, model / effort / permission mode per chat;
  - messages typed while the agent works are queued and sent after the current reply;
  - context-window use and the plan's usage limits;
  - fork a chat from any earlier message (where the agent can).
Later turns resume the agent's session, so the history lives in the agent itself.

What we deliberately do NOT do (lessons from an earlier app that felt stiff):
  - no `--bare`, no `--system-prompt`: the user's CLAUDE.md / AGENTS.md, skills, memory and
    settings load exactly like in their terminal;
  - no forced reply language: the agent answers in whatever language the user writes.
The only addition is a short note telling the agent what the user is looking at in the UI.
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

from . import agents
from .claude_info import EFFORTS, valid_model_id
from .events import EventBus
from .i18n import t

# default titles a chat may still carry from older versions: still replaced by the first message
UNTITLED = ("新对话", "New chat")

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


_WORD = re.compile(r"^[A-Za-z][\w\-]{0,40}$")


def valid_effort(value: Any, agent: Any = None) -> bool:
    """Claude Code's levels; another agent's own (Pi's `minimal`, Codex's `none`…) are one plain word."""
    if value is None:
        return True
    if agents.get(agent).protocol == "claude":
        return value in EFFORTS
    return isinstance(value, str) and bool(_WORD.match(value))


def valid_mode(value: Any, agent: Any = None) -> bool:
    """Claude Code's modes; an ACP agent's own mode ids (`auto-edit`, `yolo`…) are one plain word."""
    if value is None:
        return True
    if agents.get(agent).protocol in ("claude", "codex"):
        return value in MODES
    return isinstance(value, str) and bool(_WORD.match(value))


def _now() -> float:
    return round(time.time(), 3)


class ChatStore:
    """Display copies of conversations, kept next to the user's data (resume-studio-data/chats)."""

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

    def create(self, title: str | None = None, **fields: Any) -> dict:
        with self.lock:
            items = self.list()
            chat = {"id": uuid.uuid4().hex[:10], "title": title or t("New chat"), "session_id": None,
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
            msgs = json.loads((self.root / f"{chat_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        # chats saved before browser commands were kept as steps: work them out from the command shown
        for m in msgs:
            for b in m.get("blocks") or []:
                if b.get("type") == "tool" and b.get("name") in ("Bash", "PowerShell") and "browser.py" in str(b.get("hint") or "") \
                        and not isinstance((b.get("input") or {}).get("browser"), list):
                    step = _browser_step(str(b["hint"]))
                    if step:
                        b["input"] = {**(b.get("input") or {}), "browser": step}
        return msgs

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
    """One running agent process: the reply in progress plus anything queued behind it."""

    def __init__(self, chat_id: str, on_done: Any = None):
        self.chat_id = chat_id
        self.started_at = _now()
        self.on_done = on_done         # callable(ok: bool), run after the turn is saved
        self.driver: Any = None        # core.drivers.Driver: the agent's protocol
        self.state: dict = {}          # the driver's own (thread / turn / session ids, RPC numbering)
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
    blocks: list[dict] = [{"type": "text", "text": text or t("(see image)")}]
    for im in images:
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": im["media_type"],
                                                   "data": base64.b64encode(Path(im["path"]).read_bytes()).decode()}})
    return blocks


class Workspace:
    """Another folder a chat can run in (e.g. a connected tool's checkout), with what it needs there.

    A chat created with `workspace=<name>` runs its agent in `cwd` instead of the data folder, so
    that folder's CLAUDE.md and skills load. `context()` replaces the window's note for those
    chats, `prepare()` runs before every message (e.g. to sync files the tool reads), and `env`
    is added to the process environment."""

    def __init__(self, name: str, cwd: Path, *, label: str = "", tools: list[str] | None = None,
                 env: dict[str, str] | None = None, add_dirs: list[str] | None = None,
                 context: Any = None, prepare: Any = None):
        self.name, self.cwd, self.label = name, Path(cwd), label or name
        self.tools = tools or []
        self.env = env or {}
        self.add_dirs = add_dirs or []
        self.context = context      # callable() -> str
        self.prepare = prepare      # callable() -> None


class ChatRunner:
    def __init__(self, bus: EventBus, store: ChatStore, cwd: Path, tools: list[str] | None = None,
                 hub: "agents.AgentHub | None" = None):
        self.bus = bus
        self.store = store
        self.cwd = cwd
        self.tools = tools or DEFAULT_TOOLS      # Claude Code's pre-approved tools
        self.hub = hub                           # each agent's info (models, modes…), when the app has one
        self.workspaces: dict[str, Workspace] = {}
        # what every chat in the data folder is told besides the window's own context (the tracker's and
        # the browser's commands), so a chat can do anything from any workspace
        self.notes: list[Any] = []    # callable() -> str
        self.turns: dict[str, Turn] = {}
        self.limits: dict[str, dict] = {}   # agent id -> its plan's usage windows
        self.default_agent: Any = lambda: agents.DEFAULT   # callable() -> the agent a new chat runs on

    def workspace(self, chat: dict | None) -> Workspace | None:
        return self.workspaces.get(str((chat or {}).get("workspace") or ""))

    def agent_info(self, agent_id: str):
        return self.hub.info(agent_id) if self.hub else None

    def set_limits(self, agent_id: str, limits: dict) -> None:
        self.limits[agent_id] = limits
        self.bus.publish("claude.limits", {**limits, "agent": agent_id})

    def available(self, agent_id: str | None = None) -> bool:
        return agents.get(agent_id).installed()

    def busy(self, chat_id: str) -> bool:
        return chat_id in self.turns

    def running(self) -> list[str]:
        return list(self.turns)

    def waiting(self) -> list[str]:
        """Chats where the agent is waiting for the user (a permission, a question, a plan)."""
        return [cid for cid, t in self.turns.items() if any(a["status"] == "pending" for a in t.asks.values())]

    def live(self, chat_id: str) -> dict | None:
        turn = self.turns.get(chat_id)
        if not turn:
            return None
        return {"blocks": turn.blocks, "queue": turn.queue, "started_at": turn.started_at}

    # ---- public --------------------------------------------------------
    def send(self, chat_id: str, text: str, context: str = "", add_dirs: list[str] | None = None,
             meta: dict | None = None, on_done: Any = None, images: list[dict] | None = None,
             files: list[dict] | None = None, shown: str | None = None) -> dict:
        """Start a reply, or queue the message behind the running one. Returns {"queued": bool}.

        `text` goes to the agent; `shown` (default: the same) is what the transcript displays."""
        from .drivers import driver_for   # drivers builds on this module
        chat = self.store.meta(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        if chat.get("archived"):
            raise RuntimeError(t("Restore this chat before sending a message."))
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
                raise RuntimeError(t("This chat is still replying; wait or stop it first."))
            item = {"id": uuid.uuid4().hex[:8], "msg": msg, "prompt": text, "images": images}
            turn.queue.append(item)
            self._emit("chat.queue", chat_id, queue=turn.queue)
            return {"queued": True, "id": item["id"]}

        if not chat.get("agent"):   # a chat keeps the agent it first ran on: its session lives there
            # (chats from before agents could be picked were all Claude Code's)
            started = chat.get("session_id") or chat.get("fork") or self.store.messages(chat_id)
            chat = self.store.update_meta(chat_id, touch=False, agent="claude" if started else agents.get(self.default_agent()).id) or chat
        agent = agents.get(chat.get("agent"))
        prefix = agent.command()
        if not prefix:
            if agent.id == "claude":
                raise RuntimeError(t("The claude command was not found. Install Claude Code and sign in first."))
            raise RuntimeError(t("The {cmd} command was not found. Install {name} and sign in first.", cmd=agent.exe, name=agent.name))
        ws = self.workspace(chat)
        if chat.get("workspace") and ws is None:
            raise RuntimeError(t("This chat runs in {name}, which isn't connected right now.", name=chat["workspace"]))
        if ws is not None:
            if ws.prepare:
                try:
                    ws.prepare()
                except Exception:  # noqa: BLE001 - a failed sync shows in the UI; the chat still runs
                    pass
            if ws.context:
                context = ws.context()
        if ws is None or ws.context is None:
            context = "\n".join([context.rstrip(), *(n() for n in self.notes)]).strip() + "\n"
        self.store.append(chat_id, msg)
        if chat.get("title") in (None, "", *UNTITLED, t("New chat")):
            self.store.update_meta(chat_id, title=_title_from(text))
        else:
            self.store.update_meta(chat_id)

        dirs = list(add_dirs or []) + (ws.add_dirs if ws else [])
        for f in files or []:
            dirs.append(str(Path(f["path"]).parent))
        spec = {"chat": chat, "prefix": prefix, "prompt": text, "images": images, "context": context,
                "add_dirs": [str(Path(d)) for d in dict.fromkeys(dirs) if d and Path(d).exists()],
                "cwd": ws.cwd if ws else self.cwd, "env": {**os.environ, **ws.env} if ws and ws.env else None,
                "workspace": ws}
        turn = Turn(chat_id, on_done)
        turn.driver = driver_for(agent)
        self.turns[chat_id] = turn
        self._emit("chat.start", chat_id, meta=meta or {}, started_at=turn.started_at)
        threading.Thread(target=turn.driver.run, args=(self, turn, spec), daemon=True).start()
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
        """The user's answer to a card: a permission, the agent's question, or its plan."""
        turn = self.turns.get(chat_id)
        ask = turn.asks.get(request_id) if turn else None
        if not ask or ask["status"] != "pending":
            raise KeyError(request_id)
        new_mode = turn.driver.answer(self, turn, ask, decision)
        if new_mode and new_mode in MODES:   # the next reply keeps the mode the user just chose
            self.store.update_meta(chat_id, touch=False, mode=new_mode)
            self._emit("chat.meta", chat_id, meta=self.store.meta(chat_id))
        self._emit("chat.ask", chat_id, ask=ask)
        return ask

    def set_live(self, chat_id: str, model: str | None = None, mode: str | None = None) -> None:
        """Apply a picker change to the reply in progress too, where the agent can take it mid-reply."""
        turn = self.turns.get(chat_id)
        if turn and turn.driver:
            turn.driver.set_live(turn, model, mode)

    def cancel(self, chat_id: str) -> None:
        turn = self.turns.get(chat_id)
        if not turn or not turn.proc:
            return
        turn.cancelled = True
        turn.queue.clear()
        # a real interrupt first (the session stays consistent); kill if it does not stop
        sent = turn.driver.interrupt(turn)
        for ask in turn.asks.values():
            if ask["status"] == "pending":
                ask["status"] = "cancelled"
        turn.close()
        threading.Timer(4.0 if sent else 0.0, self._kill, args=(turn,)).start()

    def _kill(self, turn: Turn) -> None:
        from .drivers import runner_kill
        runner_kill(turn)

    def cancel_all(self) -> None:
        for chat_id in list(self.turns):
            turn = self.turns.get(chat_id)
            if turn:
                turn.cancelled = True
                turn.close()
                self._kill(turn)

    # ---- what the drivers call -------------------------------------------
    def _emit(self, event: str, chat_id: str, **data: Any) -> None:
        self.bus.publish(event, {"chat": chat_id, **data})

    def _next(self, turn: Turn) -> bool:
        """After a reply: send the next queued message into the same process."""
        if turn.cancelled or not turn.queue:
            return False
        item = turn.queue.pop(0)
        turn.started_at = _now()
        self.store.append(turn.chat_id, item["msg"])
        self.store.update_meta(turn.chat_id)
        self._emit("chat.next", turn.chat_id, message=item["msg"], queue=turn.queue, started_at=turn.started_at)
        return turn.driver.send(self, turn, item["prompt"], item["images"])

    def _flush(self, turn: Turn, error: str | None, cost: Any) -> None:
        """One reply is complete: save it and start a fresh assistant message."""
        for x in turn.tools.values():
            if x["status"] == "running":
                x["status"] = "cancelled" if turn.cancelled else "unknown"
        for a in turn.asks.values():
            if a["status"] == "pending":
                a["status"] = "cancelled"
        self.store.append(turn.chat_id, {"role": "assistant", "blocks": turn.blocks, "at": _now(),
                                         "error": None if turn.cancelled else error, "cancelled": turn.cancelled,
                                         "uuid": turn.last_uuid, "cost": cost})
        turn.error = turn.error or error
        turn.flushed_any = True
        turn.blocks, turn.tools, turn.asks = [], {}, {}
        if turn.usage and "used" in turn.usage:   # a window alone would wipe the last known use
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
        if not delta:
            return
        if turn.blocks and turn.blocks[-1]["type"] == kind:
            turn.blocks[-1]["text"] += delta
        else:
            turn.blocks.append({"type": kind, "text": delta})
        self._emit("chat.delta", turn.chat_id, text=delta, kind=kind)

    def _block(self, turn: Turn, block: dict) -> None:
        turn.blocks.append(block)
        self._emit("chat.block", turn.chat_id, block=block)


def _title_from(text: str) -> str:
    line = " ".join(text.strip().split())
    return (line[:28] + "…") if len(line) > 28 else (line or t("New chat"))


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
    if name in ("Bash", "PowerShell") and inp.get("command"):
        step = _browser_step(str(inp["command"]))   # Resume Studio's browser command: shown as a step, not a command line
        if step:
            keep["browser"] = step
    return keep


def _browser_step(command: str) -> list[dict] | None:
    from .browser import steps_of
    return steps_of(command) or None


def _result_preview(content: Any) -> str:
    if isinstance(content, list):
        content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    text = str(content or "").strip()
    return text[:400] + ("…" if len(text) > 400 else "")
