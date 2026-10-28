"""Assemble the Resume Studio server. Used by scripts/studio.py and by the tests."""
from __future__ import annotations

import json
import os
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from autofill import api as autofill_api
from core import dialogs, i18n
from core.browser import ManagedBrowser
from core.agents import AgentHub
from core.chat import ChatRunner, ChatStore
from core.server import App, HttpError, TextResponse, running_instance
from core.watcher import FileWatcher, SignatureWatcher
from core.webhost import WebHost
from jobhunt import api as hunt_api
from jobs import api as jobs_api
from jobs import career_ops
from jobs.base import JobTool
from studyplan import api as plan_api

from . import api, prompts
from .evidence import EvidenceTracker
from .service import Studio

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LOCAL = ROOT / ".local"   # this computer's app preferences (git-ignored); scripts/_bootstrap.py reads it too


@dataclass
class Instance:
    app: App          # the app the HTTP server was bound to; after a switch it forwards to the new one
    studio: Studio
    chats: ChatRunner
    identity: dict
    watcher: FileWatcher
    focus: dict = field(default_factory=dict)   # {"fn": callable} — how to bring the UI to front
    url: dict = field(default_factory=dict)     # {"url": str} once bound
    dialog: dict = field(default_factory=dict)  # {"fn": callable(kind, start)} — the window's own picker
    agents: AgentHub | None = None
    # {"fn": callable(old_studio, new_studio)} — the launcher moves its server.json along
    switched: dict = field(default_factory=dict)
    window: bool = False
    prefs: i18n.Prefs | None = None
    jobtool: JobTool | None = None             # the job-posting tool (per computer, survives data switches)
    jobs_watcher: SignatureWatcher | None = None
    plan_watcher: SignatureWatcher | None = None   # the plan's files (Claude writes them straight)
    web: WebHost | None = None                 # the tool's own web interface, hosted in the window
    browser: ManagedBrowser | None = None      # the browser Autofill drives (its own profile, per computer)

    def stop(self) -> None:
        """Before switching to another data file (the hosted web interface keeps running)."""
        self.chats.cancel_all()
        self.watcher.stop()
        if self.jobs_watcher:
            self.jobs_watcher.stop()
        if self.plan_watcher:
            self.plan_watcher.stop()
        hunt = getattr(self, "hunt", None)   # set once the job hunt is registered
        if hunt and hunt.logo_watcher:
            hunt.logo_watcher.stop()

    def shutdown(self) -> None:
        """At exit."""
        self.stop()
        if self.web:
            self.web.shutdown()
        if self.browser:
            self.browser.stop()


def create_app(data_path: str | Path, *, window: bool = False, watch_interval: float = 0.5,
               prefs_dir: str | Path | None = None) -> Instance:
    """prefs_dir: where this computer's app preferences live (default <skill>/.local)."""
    local = Path(prefs_dir) if prefs_dir else LOCAL
    inst = Instance(App(static_dir=WEB), None, None, {}, None, window=window,  # type: ignore[arg-type]
                    prefs=i18n.Prefs(local))
    # career-ops installs next to the app's own preferences (git-ignored), never in the user profile
    inst.jobtool = career_ops.CareerOps(inst.prefs, home=local / "providers" / "career-ops",
                                        cache=ROOT / ".cache", skill_md=prompts.SKILL_MD)
    inst.web = WebHost(inst.jobtool.id, ROOT / ".cache" / "logs" / f"{inst.jobtool.id}-web.log",
                       window_ports=lambda: _ports(inst.url.get("url")))
    # sign-ins live in this profile, next to the app's own preferences; tests run it headless and unhurried
    inst.browser = ManagedBrowser(local / "browser", ROOT / ".cache" / "logs" / "browser.log",
                                  headless=os.environ.get("RESUME_STUDIO_BROWSER_HEADLESS") == "1",
                                  pace=float(os.environ.get("RESUME_STUDIO_BROWSER_PACE") or 1))
    inst.url["url"] = ""
    inst.focus["fn"] = lambda: webbrowser.open(inst.url["url"]) if inst.url["url"] else None
    _load(inst, inst.app, data_path, watch_interval)
    return inst


def _ports(url: str | None) -> set[int]:
    """The window's own port (the hosted web interface only accepts frames from it)."""
    try:
        port = urlparse(url or "").port
    except ValueError:
        return set()
    return {port} if port else set()


def switch_data(inst: Instance, path: str | Path) -> None:
    """Work on another data file from now on, in the same window and on the same port: a fresh
    app (studio, chats, watcher) for it, and the bound one hands it every request.
    Chats and history live next to each data file, so they switch along."""
    old_studio, interval = inst.studio, inst.watcher.interval
    app = App(static_dir=WEB)
    inst.stop()
    _load(inst, app, path, interval)
    inst.app.forward = app
    fn = inst.switched.get("fn")
    if fn:
        fn(old_studio, inst.studio)


def _load(inst: Instance, app: App, data_path: str | Path, watch_interval: float) -> None:
    """Point `inst` at `data_path` and register every route on `app`. Routes reach the window
    (focus, pickers) and the switch through `inst`, which outlives any one data file."""
    studio = Studio(data_path)
    studio.ensure_file()
    # what each installed agent offers (models, effort, modes, account): cached, refreshed at every start
    hub = AgentHub(studio.data_dir, studio.store.state_dir)
    chats = ChatRunner(app.bus, ChatStore(studio.store.state_dir / "chats"), cwd=studio.data_dir, hub=hub)
    tracker = EvidenceTracker(studio.store.state_dir)
    for agent_id, info in hub.infos.items():
        if agent_id == "claude" or info.agent.installed():
            info.refresh_async(lambda a=agent_id: app.bus.publish("claude.info", {"agent": a}))
    api.register(app, studio, chats, tracker, hub, prefs=inst.prefs)
    identity = {"app": "resume-studio", "data": str(studio.store.path)}
    inst.studio, inst.chats, inst.identity, inst.agents = studio, chats, identity, hub
    window = inst.window

    app.route("GET", "/api/instance")(lambda req: {**identity, "window": window})

    # ---- interface language: the computer's, unless the user picked one ----
    prefs = inst.prefs
    assert prefs is not None
    i18n.set_lang(prefs.ui_lang())

    @app.route("POST", "/api/data-file")
    def data_file(req):
        """Open another data file ({"mode": "open"}) or start a new one ({"mode": "new"}), picked
        in the native dialog. Remembered as this computer's choice for the next start."""
        mode = (req.json() or {}).get("mode")
        if mode not in ("open", "new"):
            raise HttpError(400, "mode must be open or new")
        try:
            picked = dialogs.pick("yaml" if mode == "open" else "save", str(studio.data_dir), inst.dialog.get("fn"))
        except Exception as e:  # noqa: BLE001
            raise HttpError(500, i18n.t("Can't open the file picker: {error}", error=e))
        if not picked:
            return {"path": None}
        target = Path(picked[0]).expanduser().resolve()
        if mode == "new" and target.suffix.lower() not in (".yaml", ".yml"):
            target = target.with_suffix(".yaml")
        if target == studio.store.path.resolve():
            return {"path": str(target), "same": True}
        existed = target.exists()     # "new" on an existing file opens it; it is never overwritten
        if mode == "open":
            if not existed:
                raise HttpError(404, i18n.t("File not found: {path}", path=target))
            try:
                Studio(target).store.load()
            except Exception as e:  # noqa: BLE001
                raise HttpError(400, i18n.t("This file can't be read as a resume: {error}", error=e))
        probe = Studio(target)   # one window per data file: two would overwrite each other's edits
        if running_instance(probe.store.state_dir / "server.json", {"app": "resume-studio", "data": str(probe.store.path)}):
            raise HttpError(409, i18n.t("This file is already open in another Resume Studio window."))
        switch_data(inst, target)
        prefs.save(data=str(target))
        return {"path": str(target), "created": not existed}

    @app.route("GET", "/i18n.js")
    def i18n_js(req):
        """Loaded before the page's scripts, so every string they build is already translated."""
        lang = i18n.current()
        data = {"lang": lang, "dict": i18n.catalog(lang), "chosen": prefs.load().get("ui_lang"),
                "system": i18n.system_lang(), "langs": i18n.UI_LANGS}
        return TextResponse("window.I18N = " + json.dumps(data, ensure_ascii=False) + ";\n", "application/javascript")

    @app.route("PUT", "/api/prefs")
    def set_prefs(req):
        """ui_lang: a language code, or null to follow the computer again. The page reloads after."""
        b = req.json() or {}
        if "ui_lang" in b:
            prefs.save(ui_lang=b["ui_lang"] if b["ui_lang"] in i18n.UI_LANGS else None)   # None drops the choice
            i18n.set_lang(prefs.ui_lang())
        return {"ui_lang": i18n.current(), "chosen": prefs.load().get("ui_lang")}

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
            raise HttpError(500, i18n.t("Can't open the file picker: {error}", error=e))
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

    # ---- the job-posting tool: chats in its folder, its tracker and reports in the Applications tab ----
    tool = inst.jobtool
    assert tool is not None
    refresh_ws = lambda: jobs_api.workspace_for(tool, studio, chats, career_ops.TOOLS, tool.env())  # noqa: E731
    refresh_ws()
    web = inst.web
    if web is not None:
        web.on_change = lambda: app.bus.publish("jobs.web", {})
    jobs_api.register(app, studio, chats, tool, pick_folder=jobs_api.folder_picker(inst), refresh_workspace=refresh_ws,
                      web=web)
    inst.jobs_watcher = SignatureWatcher(tool.signature, lambda: app.bus.publish("jobs", {}),
                                         interval=max(watch_interval * 3, 1.0))
    inst.jobs_watcher.start()

    # ---- autofill: online profiles kept in step with a resume subset, in the browser ----
    assert inst.browser is not None
    fill = autofill_api.register(app, studio, chats, inst.browser)

    # ---- the job hunt: applications, companies and platforms (Resume Studio's own tracker) ----
    inst.hunt = hunt_api.register(app, studio, tool, prefs, platforms=fill.summary, browser=inst.browser)

    # ---- the plan: one document Claude helps the user write, with its versions (docs/PLAN.md) ----
    inst.plan, inst.plan_watcher = plan_api.register(app, studio, chats, watch_interval=watch_interval)
