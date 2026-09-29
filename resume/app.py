"""Assemble the Resume Studio server. Used by scripts/studio.py and by the tests."""
from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.chat import ChatRunner, ChatStore
from core.server import App
from core.watcher import FileWatcher

from . import api
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


def create_app(data_path: str | Path, *, window: bool = False, watch_interval: float = 0.5) -> Instance:
    studio = Studio(data_path)
    studio.ensure_file()
    app = App(static_dir=WEB)
    chats = ChatRunner(app.bus, ChatStore(studio.store.state_dir / "chats"), cwd=studio.data_dir)
    api.register(app, studio, chats)
    identity = {"app": "resume-studio", "data": str(studio.store.path)}
    inst = Instance(app, studio, chats, identity, watcher=None)  # type: ignore[arg-type]
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
