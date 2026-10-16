"""HTTP routes for the Applications tab. They speak only the JobTool contract (jobs/base.py)."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable

from core import dialogs
from core.chat import ChatRunner, Workspace, valid_effort, valid_mode, valid_model
from core.i18n import t
from core.server import App, HttpError, Request
from core.store import to_plain
from core.webhost import WebHost
from resume import model, prompts
from resume.service import Studio

from . import fit
from .base import JobTool, ToolError


def workspace_for(tool: JobTool, studio: Studio, chats: ChatRunner, extra_tools: list[str], env: dict) -> None:
    """(Re)register the tool's folder as a chat workspace: chats for it run there."""
    if not tool.installed():
        chats.workspaces.pop(tool.id, None)
        return

    def prepare() -> None:
        doc, _ = studio.store.load()
        tool.sync_cv(doc, source=str(studio.store.path))

    skill_root = str(prompts.SKILL_ROOT)
    chats.workspaces[tool.id] = Workspace(
        tool.id, tool.workspace_cwd(), label=tool.name, tools=extra_tools, env=env,
        add_dirs=list(dict.fromkeys([str(studio.data_dir), skill_root])),
        context=lambda: tool.chat_context(str(studio.store.path)), prepare=prepare)


def register(app: App, studio: Studio, chats: ChatRunner, tool: JobTool, *, pick_folder: Callable[[], list[str]],
             refresh_workspace: Callable[[], None], web: WebHost | None = None) -> None:
    def web_status() -> dict:
        if web is None or not tool.has_web():
            return {"available": False, "state": "off"}
        st = web.status()
        if st["state"] == "error":
            st["log"] = web.log_tail()
        return {"available": True, "routes": tool.web_routes(), **st}

    def start_web(restart: bool = False, rebuild: bool = False) -> None:
        if web is not None and tool.has_web():
            web.start(lambda progress: tool.web(progress, rebuild=rebuild), restart=restart)

    def body(req: Request) -> dict:
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "body must be a JSON object")
        return data

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ToolError as e:
            raise HttpError(409, str(e))
        except KeyError:
            raise HttpError(404, t("No such application"))

    def need_installed() -> None:
        if not tool.installed():
            raise HttpError(409, t("{name} isn't connected yet", name=tool.name))

    def sync(force: bool = False) -> dict:
        doc, _ = studio.store.load()
        return tool.sync_cv(doc, force=force, source=str(studio.store.path))

    def links(v: Any) -> list[dict]:
        """The postings a subset is sent to: `postings` (a list), plus the older single `posting`."""
        out = [dict(p) for p in (v.get("postings") or []) if isinstance(p, dict)]
        if isinstance(v.get("posting"), dict):
            out.insert(0, dict(v["posting"]))
        return [{"tool": str(p.get("tool") or ""), "id": str(p.get("id") or "")} for p in out]

    def is_this(p: dict, app_id: str) -> bool:
        return p["tool"] == tool.id and p["id"] == str(app_id)

    def linked(doc: Any, app_id: str) -> list[dict]:
        return [{"id": str(v.get("id")), "label": str(v.get("label") or v.get("id"))}
                for v in doc.get("subsets") or [] if any(is_this(p, app_id) for p in links(v))]

    def link(app_id: str, subset_id: str | None) -> None:
        """One resume per application: the posting is linked to `subset_id` and to no other subset."""
        doc, _ = studio.store.load()
        for v in doc.get("subsets") or []:
            vid = str(v.get("id"))
            cur = links(v)
            want = [p for p in cur if not is_this(p, app_id)]
            if vid == subset_id:
                want.append({"tool": tool.id, "id": str(app_id)})
            if want == cur and v.get("posting") is None:
                continue
            new = to_plain(v)
            new.pop("posting", None)
            if want:
                new["postings"] = want
            else:
                new.pop("postings", None)
            studio.update_item("subset", vid, new, None)

    def settings_of(b: dict) -> dict:
        """The model / effort / permission mode the user last picked in the chat, for new chats."""
        out = {}
        if b.get("model") is not None and valid_model(b["model"]):
            out["model_choice"] = b["model"] or None
        if b.get("effort") is not None and valid_effort(b["effort"]):
            out["effort"] = b["effort"] or None
        if b.get("mode") is not None and valid_mode(b["mode"]):
            out["mode"] = b["mode"] or None
        return out

    def start_chat(b: dict, title: str, text: str | None, *, in_tool: bool, context: str = "",
                   add_dirs: list[str] | None = None, action: str = "") -> dict:
        if in_tool:
            need_installed()
            refresh_workspace()
        chat = chats.store.create(title=title[:60], workspace=tool.id if in_tool else None, **settings_of(b))
        if text:
            try:
                chats.send(chat["id"], text, context=context, add_dirs=add_dirs or [], meta={"action": action or None})
            except RuntimeError as e:
                chats.store.delete(chat["id"])
                raise HttpError(409, str(e))
        return chats.store.meta(chat["id"]) or chat

    # ---- what the tab shows --------------------------------------------------
    @app.route("GET", "/api/jobs")
    def overview(req: Request):
        if tool.installed() and not tool.busy:
            try:
                sync()   # the tool's copy of the CV follows the library (never over edits made there)
            except (OSError, ValueError):
                pass
        st = tool.status()
        doc, _ = studio.store.load()
        apps = tool.applications() if st.get("installed") else []
        for a in apps:
            a["subsets"] = linked(doc, a["id"])
        if st.get("installed"):
            st["profile"] = tool.profile_state(doc)
        return {"status": st, "applications": apps, "states": tool.states() if st.get("installed") else [],
                "actions": tool.actions(), "modes": tool.modes() if st.get("installed") else [], "web": web_status()}

    # ---- the tool's own web interface, hosted in the window's job-search workspace ----
    @app.route("GET", "/api/jobs/web")
    def web_get(req: Request):
        return web_status()

    @app.route("POST", "/api/jobs/web/start")
    def web_start(req: Request):
        """Start it (or restart / rebuild it). Installing and building happen on the first start and
        after an update only; the window shows the steps."""
        b = body(req)
        need_installed()
        if web is None or not tool.has_web():
            raise HttpError(409, t("This version of {name} has no web interface.", name=tool.name))
        start_web(restart=bool(b.get("restart") or b.get("rebuild")), rebuild=bool(b.get("rebuild")))
        return web_status()

    @app.route("POST", "/api/jobs/web/stop")
    def web_stop(req: Request):
        if web is not None:
            web.stop()
        return web_status()

    @app.route("POST", "/api/jobs/mode")
    def run_mode(req: Request):
        """One of the tool's modes, as a chat in its folder."""
        b = body(req)
        need_installed()
        m = next((x for x in tool.modes() if x["id"] == str(b.get("mode") or "")), None)
        if m is None:
            raise HttpError(404, t("Unknown mode: {name}", name=b.get("mode")))
        return {"chat": start_chat(b, f"{tool.name}: {m['id']}", tool.prompt("mode", mode=m), in_tool=True, action="jobs_mode")}

    @app.route("GET", "/api/jobs/app/{aid}")
    def detail(req: Request):
        need_installed()
        a = guard(lambda: tool.application(req.params["aid"]))
        doc, _ = studio.store.load()
        a["subsets"] = linked(doc, a["id"])
        return a

    @app.route("PUT", "/api/jobs/app/{aid}/status")
    def set_status(req: Request):
        need_installed()
        guard(lambda: tool.set_status(req.params["aid"], str(body(req).get("state") or "")))
        return guard(lambda: tool.application(req.params["aid"]))

    # ---- work done by Claude ---------------------------------------------------
    @app.route("POST", "/api/jobs/evaluate")
    def evaluate(req: Request):
        b = body(req)
        posting = str(b.get("posting") or "").strip()
        if not posting:
            raise HttpError(400, t("Paste the posting's link or text first"))
        first = " ".join(posting.split())[:40]
        return {"chat": start_chat(b, t("Evaluate: {posting}", posting=first), tool.prompt("evaluate", posting=posting),
                                   in_tool=True, action="jobs_evaluate")}

    @app.route("POST", "/api/jobs/setup")
    def setup(req: Request):
        """Fill the tool's targeting profile from the resume data ({update: true} once it was filled)."""
        b = body(req)
        update = bool(b.get("update"))
        chat = start_chat(b, t("Update {name}'s profile", name=tool.name) if update else t("Set up {name}", name=tool.name),
                          tool.prompt("profile_update" if update else "setup", data_path=str(studio.store.path)),
                          in_tool=True, action="jobs_setup")
        doc, _ = studio.store.load()
        tool.profile_used(doc)
        return {"chat": chat}

    @app.route("POST", "/api/jobs/scan")
    def scan(req: Request):
        b = body(req)
        return {"chat": start_chat(b, t("Find new postings"), tool.prompt("scan"), in_tool=True, action="jobs_scan")}

    @app.route("POST", "/api/jobs/chat")
    def tool_chat(req: Request):
        """An empty chat in the tool's folder (the user types the first message)."""
        b = body(req)
        return {"chat": start_chat(b, str(b.get("title") or tool.name), None, in_tool=True)}

    @app.route("POST", "/api/jobs/app/{aid}/action")
    def action(req: Request):
        b = body(req)
        need_installed()
        a = guard(lambda: tool.application(req.params["aid"]))
        kind = str(b.get("action") or "")
        label = next((x["label"] for x in tool.actions() if x["id"] == kind), kind)
        text = guard(lambda: tool.prompt("action", app=a, action=kind))
        return {"chat": start_chat(b, f"{label}: {a.get('company') or a['id']}", text, in_tool=True, action="jobs_" + kind)}

    # ---- which resume goes out: one of the user's subsets, linked to the posting ----
    @app.route("GET", "/api/jobs/app/{aid}/fit")
    def fit_rank(req: Request):
        """The user's subsets ranked by how much of this posting they already cover."""
        need_installed()
        a = guard(lambda: tool.application(req.params["aid"]))
        doc, _ = studio.store.load()
        return {"subsets": fit.rank(doc, a), "linked": linked(doc, a["id"]),
                "requirements": len((a.get("summary") or {}).get("requirements") or []),
                "keywords": len(fit.keywords(a))}

    @app.route("PUT", "/api/jobs/app/{aid}/resume")
    def use_resume(req: Request):
        """Send this subset for this posting ({subset_id: null} unlinks it)."""
        b = body(req)
        need_installed()
        a = guard(lambda: tool.application(req.params["aid"]))
        vid = str(b.get("subset_id") or "") or None
        doc, _ = studio.store.load()
        if vid and (vid == model.ALL_ID or model.get_item(doc, "subset", vid) is None):
            raise HttpError(404, t("Subset {id} doesn't exist", id=vid))
        link(a["id"], vid)
        doc, _ = studio.store.load()
        return {"linked": linked(doc, a["id"]), **studio.state()}

    @app.route("POST", "/api/jobs/app/{aid}/choose")
    def choose_resume(req: Request):
        """Ask Claude which subset to send (it links it in resume.yaml once the user agrees)."""
        b = body(req)
        need_installed()
        a = guard(lambda: tool.application(req.params["aid"]))
        doc, _ = studio.store.load()
        text = prompts.choose_message(a, a.get("report_path") or "", fit.rank(doc, a), tool.id)
        context = prompts.ui_context(str(studio.store.path), None, None,
                                     [str(p) for p in (doc.get("settings") or {}).get("project_roots") or []])
        chat = start_chat(b, t("Which resume: {job}", job=a.get("company") or a["id"]), text, in_tool=False,
                          context=context, add_dirs=[str(tool.data_folder())], action="jobs_choose")
        return {"chat": chat}

    @app.route("POST", "/api/jobs/app/{aid}/tailor")
    def tailor(req: Request):
        """Adjust a subset for this kind of posting, or start a new one; then a resume chat picks the
        entries and writes the headline. A subset is for a kind of role, never for one company."""
        b = body(req)
        need_installed()
        a = guard(lambda: tool.application(req.params["aid"]))
        doc, _ = studio.store.load()
        vid = str(b.get("subset_id") or "")
        if vid and (vid == model.ALL_ID or model.get_item(doc, "subset", vid) is None):
            raise HttpError(404, t("Subset {id} doesn't exist", id=vid))
        new = not vid
        if new:
            s = a.get("summary") or {}
            label = str(b.get("label") or "").strip() or a.get("role") or s.get("archetype") or t("Application #{id}", id=a["id"])
            vid, _ = studio.create_subset(None, None, label[:60])
        link(a["id"], vid)
        doc, _ = studio.store.load()
        subset = model.get_subset(doc, vid)
        text = prompts.tailor_message(subset, a, a.get("report_path") or "", str(studio.store.path), new=new)
        roots = [str(p) for p in (doc.get("settings") or {}).get("project_roots") or []]
        context = prompts.ui_context(str(studio.store.path), subset, None, roots)
        chat = start_chat(b, t("Tailor: {job}", job=subset.get("label") or vid), text, in_tool=False, context=context,
                          add_dirs=[str(tool.data_folder())], action="jobs_tailor")
        return {"subset_id": vid, "chat": chat, **studio.state()}

    # ---- the CV the tool reads ----------------------------------------------------
    @app.route("POST", "/api/jobs/cv/sync")
    def cv_sync(req: Request):
        need_installed()
        r = sync(force=bool(body(req).get("force")))
        return {"sync": r, "status": tool.status()}

    @app.route("POST", "/api/jobs/cv/resolve")
    def cv_resolve(req: Request):
        """Ask Claude to bring edits made on the tool's side into the resume library."""
        b = body(req)
        need_installed()
        name = str(b.get("file") or "cv.md")
        doc, _ = studio.store.load()
        diff = tool.cv_diff(doc, name, str(studio.store.path))  # type: ignore[attr-defined]
        if not diff.strip():
            raise HttpError(409, t("{file} already matches your library", file=name))
        # the chat must not overwrite career-ops' copy while it is being compared: no sync for this one
        refresh_workspace()
        ws = chats.workspaces.get(tool.id)
        saved = ws.prepare if ws else None
        if ws:
            ws.prepare = None
        try:
            chat = start_chat(b, t("Bring {file} into the library", file=name), tool.prompt("cv_conflict", file=name, diff=diff),
                              in_tool=True, action="jobs_cv")
        finally:
            if ws:
                ws.prepare = saved
        return {"chat": chat}

    @app.route("PUT", "/api/jobs/settings")
    def save_settings(req: Request):
        b = body(req)
        fields = {}
        if "cv_lang" in b:
            fields["cv_lang"] = str(b["cv_lang"]) if b["cv_lang"] else None
        for k in ("notes", "sync"):
            if k in b:
                fields[k] = bool(b[k])
        tool.save_settings(**fields)   # type: ignore[attr-defined]
        if tool.installed():
            sync()
        return {"status": tool.status()}

    # ---- install, update ------------------------------------------------------------
    def background(task: str, fn: Callable[[Callable[[str, str], None]], Any]) -> dict:
        def progress(step: str, state: str) -> None:
            app.bus.publish("jobs.progress", {"task": task, "step": step, "state": state})

        def run() -> None:
            try:
                out = fn(progress)
                refresh_workspace()
                if web is not None and web.state != "off":
                    start_web(restart=True)   # its new version (built again only if its source changed)
                app.bus.publish("jobs.progress", {"task": task, "state": "done", "result": out if isinstance(out, dict) else None})
            except ToolError as e:
                app.bus.publish("jobs.progress", {"task": task, "state": "error", "error": str(e)})
            except Exception as e:  # noqa: BLE001 - surface anything to the UI
                app.bus.publish("jobs.progress", {"task": task, "state": "error", "error": f"{type(e).__name__}: {e}"})
            app.bus.publish("jobs", {})

        if tool.busy:
            raise HttpError(409, t("{name} is busy; wait for it to finish.", name=tool.name))
        threading.Thread(target=run, name=f"jobs-{task}", daemon=True).start()
        return {"started": task}

    @app.route("POST", "/api/jobs/install")
    def install(req: Request):
        return background("install", tool.install)

    @app.route("POST", "/api/jobs/folder")
    def use_folder(req: Request):
        try:
            picked = pick_folder()
        except Exception as e:  # noqa: BLE001
            raise HttpError(500, t("Can't open the file picker: {error}", error=e))
        if not picked:
            return {"status": tool.status()}
        guard(lambda: tool.use_folder(Path(picked[0])))
        refresh_workspace()
        if web is not None and web.state != "off":
            start_web(restart=True)
        return {"status": tool.status()}

    @app.route("GET", "/api/jobs/update")
    def check_update(req: Request):
        return guard(tool.check_update)

    @app.route("POST", "/api/jobs/update")
    def update(req: Request):
        need_installed()
        return background("update", tool.update)

    @app.route("POST", "/api/jobs/repair")
    def repair(req: Request):
        need_installed()
        return background("repair", tool.repair)

    @app.route("POST", "/api/jobs/rollback")
    def rollback(req: Request):
        need_installed()
        return background("rollback", lambda progress: tool.rollback())

    @app.route("POST", "/api/jobs/reveal")
    def reveal(req: Request):
        """Open the tool's folder, or one application's report, with the computer's own apps."""
        b = body(req)
        need_installed()
        target = tool.workspace_cwd()
        if b.get("app"):
            a = guard(lambda: tool.application(str(b["app"])))
            if a.get("report_path"):
                target = Path(a["report_path"])
        if os.name == "nt" and not os.environ.get("STUDIO_NO_SHELL") and target.exists():
            os.startfile(str(target))
        return {"ok": target.exists(), "path": str(target)}


def folder_picker(inst: Any) -> Callable[[], list[str]]:
    def pick() -> list[str]:
        return dialogs.pick("folder", str(Path.home()), inst.dialog.get("fn"))
    return pick
