"""The window's routes for plugins and the plugin market (docs/PLUGINS.md). The AI writes records with
scripts/plugin.py, straight to the file; a watcher tells the window, so a change from anywhere shows up by itself."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.chat import ChatRunner
from core.server import App, HttpError, Request
from core.watcher import SignatureWatcher
from resume.service import Studio

from . import manifest, market, prompts
from .records import Records
from .registry import Registry


def register(app: App, studio: Studio, chats: ChatRunner, prefs: Any, installed_dir: Path,
             *, watch_interval: float = 0.5) -> tuple[Registry, SignatureWatcher]:
    reg = Registry(installed_dir, prefs)
    state = studio.store.state_dir
    publish = lambda: app.bus.publish("plugins", {})  # noqa: E731

    def signature() -> tuple:
        # the records of every plugin that is on, and which plugins are here and on
        on = reg.enabled()
        return (tuple((p["id"], p["manifest"]["version"]) for p in on),
                tuple(Records(state, p["manifest"]).signature() for p in on),
                tuple(sorted(f.name for f in installed_dir.iterdir())) if installed_dir.is_dir() else ())
    watcher = SignatureWatcher(signature, publish, interval=max(watch_interval, 0.5))
    watcher.start()
    chats.notes.append(lambda: prompts.tools_note(str(studio.store.path), reg, state))

    def body(req: Request) -> dict:
        b = req.json()
        if not isinstance(b, dict):
            raise HttpError(400, "body must be a JSON object")
        return b

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            out = fn()
        except KeyError as e:
            raise HttpError(404, str(e).strip("'\""))
        except (ValueError, manifest.ManifestError, market.MarketError) as e:
            raise HttpError(400, str(e))
        publish()
        return out

    def on_plugin(pid: str) -> dict:
        try:
            p = reg.get(pid)
        except KeyError as e:
            raise HttpError(404, str(e).strip("'\""))
        if not p["on"]:
            raise HttpError(409, f"the plugin {pid} is off")
        return p

    def summary(p: dict) -> dict:
        out = {k: p[k] for k in ("id", "source", "on", "bundled", "error") if k in p}
        if "manifest" in p:
            out["manifest"] = p["manifest"]
        return out

    @app.route("GET", "/api/plugins")
    def plugins(req: Request):
        """Every plugin on this computer, and the records of those that are on ({plugin: {collection: rows}})."""
        allp = reg.all()
        records = {}
        for p in allp:
            if p.get("on") and "manifest" in p:
                try:
                    records[p["id"]] = Records(state, p["manifest"]).all()
                except ValueError as e:
                    records[p["id"]] = {"error": str(e)}
        return {"plugins": [summary(p) for p in allp], "records": records}

    @app.route("PUT", "/api/plugins/{pid}")
    def switch(req: Request):
        b = body(req)
        return guard(lambda: (reg.switch(req.params["pid"], bool(b.get("on"))), summary(reg.get(req.params["pid"])))[1])

    @app.route("DELETE", "/api/plugins/{pid}")
    def uninstall(req: Request):
        return guard(lambda: (reg.uninstall(req.params["pid"]), {"ok": True})[1])

    @app.route("POST", "/api/plugins/{pid}/records/{cid}")
    def add(req: Request):
        p = on_plugin(req.params["pid"])
        b = body(req)
        return guard(lambda: Records(state, p["manifest"]).add(req.params["cid"], b, by="you"))

    @app.route("PUT", "/api/plugins/{pid}/records/{cid}/{rid}")
    def edit(req: Request):
        p = on_plugin(req.params["pid"])
        b = body(req)
        return guard(lambda: Records(state, p["manifest"]).edit(req.params["cid"], req.params["rid"], b, by="you"))

    @app.route("DELETE", "/api/plugins/{pid}/records/{cid}/{rid}")
    def delete(req: Request):
        p = on_plugin(req.params["pid"])
        return guard(lambda: Records(state, p["manifest"]).delete(req.params["cid"], req.params["rid"]))

    # ---- the market ----
    @app.route("GET", "/api/plugin-market")
    def market_list(req: Request):
        """The market's list, each entry with what this computer has of it."""
        got = market.listing()
        here = {p["id"]: p for p in reg.all() if "manifest" in p}
        for e in got["plugins"]:
            p = here.get(e["id"])
            e["installed"] = p["manifest"]["version"] if p else None
            e["on"] = bool(p and p["on"])
            e["update"] = bool(p and e.get("version") and not e.get("bundled")
                               and manifest.version_key(e["version"]) > manifest.version_key(p["manifest"]["version"]))
        return got

    @app.route("POST", "/api/plugin-market/install")
    def install(req: Request):
        """Install (or update) a market plugin by id, or turn on a bundled one."""
        pid = str(body(req).get("id") or "")
        entry = next((e for e in market.listing()["plugins"] if e["id"] == pid), None)
        if not entry:
            raise HttpError(404, f"the market has no plugin {pid!r}")
        if entry.get("bundled"):
            return guard(lambda: (reg.switch(pid, True), summary(reg.get(pid)))[1])
        try:
            data = market.download(entry)
        except market.MarketError as e:
            raise HttpError(400, str(e))
        except OSError as e:
            raise HttpError(502, f"the download failed: {e}")
        return guard(lambda: summary(reg.install_zip(data, expect_id=pid)))

    return reg, watcher
