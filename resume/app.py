"""Assemble the Resume Studio server. Used by scripts/studio.py and by the tests."""
from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core import dialogs
from core.chat import ChatRunner, ChatStore, claude_command
from core.claude_info import ClaudeInfo
from core.server import App, HttpError
from core.watcher import FileWatcher

from . import api
from .evidence import EvidenceTracker
from .service import Studio

WEB = Path(__file__).resolve().parent.parent / "web"


@dataclass
class Instance:
    app: App
    studio: Studio
    chats: ChatRunner
    identity: dict
    watcher: FileWatcher
    focus: dict = field(default_factory=dict)   # {"fn": callable} — how to bring the UI to front
    url: dict = field(default_factory=dict)     # {"url": str} once bound
    dialog: dict = field(default_factory=dict)  # {"fn": callable(kind, start)} — the window's own picker
    info: ClaudeInfo | None = None


def create_app(data_path: str | Path, *, window: bool = False, watch_interval: float = 0.5) -> Instance:
    studio = Studio(data_path)
    studio.ensure_file()
    app = App(static_dir=WEB)
    chats = ChatRunner(app.bus, ChatStore(studio.store.state_dir / "chats"), cwd=studio.data_dir)
    tracker = EvidenceTracker(studio.store.state_dir)
    # what the user's Claude Code offers (models, effort, commands): cached, refreshed at every start
    info = ClaudeInfo(claude_command, studio.data_dir, studio.store.state_dir / "claude-info.json")
    info.refresh_async(lambda: app.bus.publish("claude.info", {}))
    api.register(app, studio, chats, tracker, info)
    identity = {"app": "resume-studio", "data": str(studio.store.path)}
    inst = Instance(app, studio, chats, identity, watcher=None)  # type: ignore[arg-type]
    inst.info = info
    inst.url["url"] = ""
    inst.focus["fn"] = lambda: webbrowser.open(inst.url["url"]) if inst.url["url"] else None

    app.route("GET", "/api/instance")(lambda req: {**identity, "window": window})

    @app.route("POST", "/api/focus")
    def focus(req):
        fn: Callable | None = inst.focus.get("fn")
        if fn:
            fn()
        return {"ok": True}

    @app.route("POST", "/api/reveal")
    def reveal(req):
        """Open a folder in Explorer — only inside the data folder."""
        target = Path(str(req.json().get("path") or ""))
        try:
            target.resolve().relative_to(studio.data_dir.resolve())
        except ValueError:
            return {"ok": False}
        if not target.exists():
            return {"ok": False}
        if os.name == "nt" and not os.environ.get("STUDIO_NO_SHELL"):
            os.startfile(str(target if target.is_dir() else target.parent))
        return {"ok": True}

    @app.route("POST", "/api/pick")
    def pick(req):
        """Native folder / file picker. The page cannot see real paths, so the server asks."""
        b = req.json() or {}
        kind = "files" if b.get("kind") == "files" else "folder"
        try:
            paths = dialogs.pick(kind, b.get("initial"), inst.dialog.get("fn"))
        except Exception as e:  # noqa: BLE001
            raise HttpError(500, f"打不开选择窗口：{e}")
        return {"paths": paths}

    @app.route("GET", "/api/clipboard")
    def clipboard(req):
        """For the page's own right-click menu: WebView2's clipboard read needs a permission prompt."""
        try:
            return {"text": dialogs.read_clipboard()}
        except Exception:  # noqa: BLE001
            return {"text": ""}

    @app.route("POST", "/api/reveal-any")
    def reveal_any(req):
        """Open one of the entry's evidence paths in Explorer (paths the user picked themselves)."""
        target = Path(str(req.json().get("path") or "")).expanduser()
        doc, _ = studio.store.load()
        known = {str(p) for e in doc.get("entries") or [] for p in e.get("evidence") or []}
        known |= {str(p) for p in (doc.get("settings") or {}).get("project_roots") or []}
        if str(req.json().get("path")) not in known or not target.exists():
            return {"ok": False}
        if os.name == "nt" and not os.environ.get("STUDIO_NO_SHELL"):
            os.startfile(str(target if target.is_dir() else target.parent))
        return {"ok": True}

    @app.route("POST", "/api/open")
    def open_url(req):
        url = str(req.json().get("url") or "")
        if url.startswith(("http://", "https://")) and not os.environ.get("STUDIO_NO_SHELL"):
            webbrowser.open(url)
        return {"ok": True}

    inst.watcher = FileWatcher(studio.store.path, studio.store.digest_text,
                               lambda digest: app.bus.publish("file", {"digest": digest}), interval=watch_interval)
    inst.watcher.start()
    return inst
