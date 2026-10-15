"""HTTP routes for the Autofill workspace and for scripts/browser.py (the command Claude drives the
browser with). The browser belongs to the computer (one per Resume Studio window); the plan and
what was read belong to the data file."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from core.browser import BrowserError, ManagedBrowser
from core.chat import ChatRunner, Workspace, valid_effort, valid_mode, valid_model
from core.i18n import t
from core.server import App, FileResponse, HttpError, Request
from resume import model
from resume import prompts as rp
from resume.service import Studio

from . import prompts
from .service import Autofill
from .sites import Site

KIND = "autofill"   # the chat workspace of this tab; its chats also carry their `site`


def register(app: App, studio: Studio, chats: ChatRunner, browser: ManagedBrowser) -> Autofill:
    fill = Autofill(studio)
    shots = studio.store.state_dir / "browser"

    def publish(site_id: str | None = None) -> None:
        app.bus.publish("autofill", {"site": site_id})

    browser.on_change = lambda: app.bus.publish("browser", browser.status())

    def body(req: Request) -> dict:
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "body must be a JSON object")
        return data

    def site_of(req: Request) -> Site:
        s = fill.site(req.params["site"])
        if s is None:
            raise HttpError(404, t("Unknown site: {name}", name=req.params["site"]))
        return s

    def bro(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except BrowserError as e:
            raise HttpError(409, str(e))

    def settings_of(b: dict) -> dict:
        out = {}
        if b.get("model") is not None and valid_model(b["model"]):
            out["model_choice"] = b["model"] or None
        if b.get("effort") is not None and valid_effort(b["effort"]):
            out["effort"] = b["effort"] or None
        if b.get("mode") is not None and valid_mode(b["mode"]):
            out["mode"] = b["mode"] or None
        return out

    def note() -> str:
        doc, _ = studio.store.load()
        roots = [str(p) for p in (doc.get("settings") or {}).get("project_roots") or []]
        return rp.ui_context(str(studio.store.path), None, None, roots) + "\n" + prompts.tools_note(str(studio.store.path)) + "\n"

    # Autofill's chats run in the data folder like any resume chat, and every message in them (the
    # user's follow-ups too) tells Claude how to use the browser
    chats.workspaces[KIND] = Workspace(KIND, studio.data_dir, label="Autofill", context=note)

    def start_chat(b: dict, site: Site | None, title: str, text: str = "", procedure: str = "",
                   on_done: Callable[[bool], None] | None = None, action: str = "") -> dict:
        chat = chats.store.create(title=title[:60], workspace=KIND, site=site.id if site else None, **settings_of(b))
        if text:
            try:
                # the procedure goes to Claude with the message (so a later "go on" still has it); the chat shows the message
                chats.send(chat["id"], text + ("\n\n" + procedure if procedure else ""), shown=text,
                           meta={"action": action or None, "site": site.id if site else None}, on_done=on_done)
            except RuntimeError as e:
                chats.store.delete(chat["id"])
                raise HttpError(409, str(e))
        return chats.store.meta(chat["id"]) or chat

    @app.route("POST", "/api/autofill/chat")
    def empty_chat(req: Request):
        """A chat in the Autofill workspace (the user types the first message)."""
        b = body(req)
        site = fill.site(str(b.get("site") or ""))
        return {"chat": start_chat(b, site, str(b.get("title") or t("Autofill")))}

    # ---- the workspace -----------------------------------------------------------------
    @app.route("GET", "/api/autofill")
    def overview(req: Request):
        return {"browser": browser.status(), "sites": fill.summary(), "presets": fill.presets()}

    def site_fields(b: dict) -> dict:
        out = {}
        for k in ("name", "home", "lang", "notes"):
            if b.get(k) is not None:
                if not isinstance(b[k], str):
                    raise HttpError(400, f"{k} must be text")
                out[k] = b[k]
        lang = out.get("lang")
        if lang:
            doc, _ = studio.store.load()
            if lang not in [str(x) for x in (doc.get("settings") or {}).get("languages") or []]:
                raise HttpError(400, t("{lang} isn't one of this file's resume languages", lang=lang))
        return out

    def bad_home(e: ValueError) -> HttpError:
        if str(e) == "home":
            return HttpError(400, t("The address must be a web address, like https://www.example.com/profile"))
        return HttpError(400, str(e))

    @app.route("POST", "/api/autofill/sites")
    def add_site(req: Request):
        """A site on the list: one Resume Studio knows (`preset`), or any other by its address."""
        b = body(req)
        try:
            site = fill.add_site(preset=str(b.get("preset") or ""), **site_fields(b))
        except ValueError as e:
            raise bad_home(e)
        publish(site.id)
        return {"site": site.public(), "sites": fill.summary(), "presets": fill.presets()}

    @app.route("PUT", "/api/autofill/{site}/settings")
    def edit_site(req: Request):
        site, b = site_of(req), body(req)
        try:
            fill.edit_site(site.id, **site_fields(b))
            if b.get("index") is not None:
                fill.move_site(site.id, int(b["index"]))
        except ValueError as e:
            raise bad_home(e)
        publish(site.id)
        return {"sites": fill.summary(), "presets": fill.presets()}

    @app.route("DELETE", "/api/autofill/{site}")
    def remove_site(req: Request):
        fill.remove_site(site_of(req).id)
        publish(None)
        return {"sites": fill.summary(), "presets": fill.presets()}

    def full(site: Site) -> dict:
        v = fill.view(site)
        return {**v, "reading": bool(v["read_chat"]) and chats.busy(v["read_chat"]), "browser": browser.status()}

    @app.route("GET", "/api/autofill/{site}")
    def site_view(req: Request):
        return full(site_of(req))

    @app.route("PUT", "/api/autofill/{site}")
    def choose(req: Request):
        """Which resume version and language go to this site, and the user's ticks and edits."""
        site, b = site_of(req), body(req)
        doc, _ = studio.store.load()
        vid = b.get("version_id")
        if vid is not None and model.get_version(doc, str(vid)) is None:
            raise HttpError(404, t("Job {id} doesn't exist", id=vid))
        lang = b.get("lang")
        langs = [str(x) for x in (doc.get("settings") or {}).get("languages") or []]
        if lang is not None and str(lang) not in langs:
            raise HttpError(400, t("{lang} isn't one of this file's resume languages", lang=lang))
        choices = b.get("choices")
        if choices is not None and not isinstance(choices, dict):
            raise HttpError(400, "choices must be an object")
        fill.choose(site, version_id=str(vid) if vid is not None else None, lang=str(lang) if lang is not None else None,
                    choices=choices)
        if vid is not None or lang is not None:   # ticks and edits come from this window only: no echo
            publish(site.id)
        return full(site)

    @app.route("POST", "/api/autofill/{site}/open")
    def open_site(req: Request):
        """Show the site in the browser (the user signs in there, or looks)."""
        site = site_of(req)
        st = fill.load(site.id)
        url = str((st.get("current") or {}).get("url") or "") or site.home
        here = bro(lambda: browser.open(url if site.owns(url) else site.home))
        bro(browser.front)
        out = site.signed_out(here["url"])
        fill.save(site.id, signed_out=out)
        publish(site.id)
        return {**here, "signed_out": out, "browser": browser.status()}

    @app.route("POST", "/api/autofill/{site}/read")
    def read_profile(req: Request):
        site, b = site_of(req), body(req)
        st = fill.load(site.id)
        version, lang = fill.target(site, st)
        want = fill.desired(site, st)
        bro(browser.ensure)
        label = str(version.get("label") or version.get("id"))
        if model.is_all(version):
            label = t("All entries")

        def done(ok: bool) -> None:
            publish(site.id)

        chat = start_chat(b, site, t("Read {site}", site=site.name), prompts.read_message(site, label, lang),
                          prompts.read_context(site, str(studio.store.path), want, lang), done, action="autofill_read")
        fill.save(site.id, read_chat=chat["id"])
        publish(site.id)
        return {"chat": chat}

    @app.route("POST", "/api/autofill/{site}/apply")
    def apply(req: Request):
        """Make the ticked changes: Claude, in the browser, reporting each one back."""
        site, b = site_of(req), body(req)
        view = fill.view(site)
        if not view["plan"]["read"]:
            raise HttpError(409, t("Read the {site} profile first", site=site.name))
        items = [i for i in view["plan"]["items"] if i["checked"] and i["op"] != "same"]
        if not items:
            raise HttpError(409, t("Nothing is ticked"))
        over = [i for i in items if any(f.get("over") for f in i["fields"])]
        if over:
            raise HttpError(409, t("Shorten the text that is over {site}'s limit first: {items}", site=site.name,
                                   items=", ".join(i["label"] for i in over)))
        bro(browser.ensure)
        st = fill.load(site.id)
        applied = {k: v for k, v in (st.get("applied") or {}).items() if k not in {i["id"] for i in items}}
        fill.save(site.id, applied=applied, applying=[i["id"] for i in items])

        def done(ok: bool) -> None:
            fill.save(site.id, applying=[])
            publish(site.id)

        chat = start_chat(b, site, t("Update {site}", site=site.name), prompts.apply_message(site, len(items)),
                          prompts.apply_context(site, str(studio.store.path), items, str((view["current"] or {}).get("url") or "")),
                          done, action="autofill_apply")
        fill.save(site.id, apply_chat=chat["id"])
        publish(site.id)
        return {"chat": chat}

    # ---- what Claude sends back (scripts/browser.py) --------------------------------------
    @app.route("POST", "/api/autofill/{site}/current")
    def current(req: Request):
        site = site_of(req)
        plan, errs = fill.set_current(site, req.json())
        if errs:
            raise HttpError(400, "the profile JSON has problems:\n- " + "\n- ".join(errs))
        publish(site.id)
        lines = [f"saved. The window now shows {plan['counts']['add']} to add, {plan['counts']['update']} to change, "
                 f"{plan['counts']['remove']} only on {site.name}, {plan['counts']['same']} already the same:"]
        for i in plan["items"]:
            if i["op"] != "same":
                lines.append(f"- {i['op']} {i['section']}: {i['label']}" + (f" ({', '.join(f['name'] for f in i['fields'])})" if i["op"] == "update" else ""))
        return {"text": "\n".join(lines)}

    @app.route("POST", "/api/autofill/{site}/report")
    def report(req: Request):
        site, b = site_of(req), body(req)
        try:
            fill.report(site, str(b.get("id") or ""), str(b.get("state") or ""), str(b.get("note") or ""))
        except KeyError:
            raise HttpError(404, f"no change {b.get('id')!r} in the plan; use the ids from the approved list")
        except ValueError as e:
            raise HttpError(400, str(e))
        publish(site.id)
        return {"text": "ok"}

    @app.route("POST", "/api/autofill/{site}/signed-out")
    def signed_out(req: Request):
        site = site_of(req)
        fill.save(site.id, signed_out=True)
        publish(site.id)
        return {"text": f"the window now asks the user to sign in to {site.name} in the browser window"}

    # ---- the browser itself ----------------------------------------------------------------
    @app.route("GET", "/api/browser")
    def browser_status(req: Request):
        return browser.status()

    @app.route("POST", "/api/browser/start")
    def browser_start(req: Request):
        bro(browser.ensure)
        bro(browser.front)
        return browser.status()

    @app.route("POST", "/api/browser/stop")
    def browser_stop(req: Request):
        browser.stop()
        return browser.status()

    def inside_data(path: str) -> str:
        p = Path(path).expanduser().resolve()
        try:
            p.relative_to(studio.data_dir.resolve())
        except ValueError:
            raise HttpError(403, f"only files in {studio.data_dir} can be uploaded (exported resumes live there)")
        if not p.is_file():
            raise HttpError(404, f"no such file: {p}")
        return str(p)

    @app.route("GET", "/api/browser/shot/{name}")
    def shot(req: Request):
        """A screenshot Claude took, for its step in the chat."""
        name = req.params["name"]
        path = shots / name
        if not re.fullmatch(r"shot-\d+\.png", name) or not path.is_file():
            raise HttpError(404, t("That screenshot is gone"))
        return FileResponse(path)

    @app.route("POST", "/api/browser/do")
    def do(req: Request):
        """One command from scripts/browser.py; the answer is text for Claude."""
        b = body(req)
        cmd = str(b.get("cmd") or "")
        ref = str(b.get("ref") or "")

        def run() -> Any:
            if cmd == "open":
                return browser.open(str(b.get("url") or ""), new_tab=bool(b.get("new_tab")))
            if cmd == "snapshot":
                within = "dialog" if b.get("dialog") else (str(b["within"]) if b.get("within") else None)
                return browser.snapshot(within, int(b.get("max") or 60000))
            if cmd == "text":
                return browser.text()
            if cmd == "click":
                return browser.click(ref)
            if cmd == "type":
                return browser.type(ref, str(b.get("text") or ""), clear=not b.get("append"))
            if cmd == "select":
                return browser.select(ref, str(b.get("option") or ""))
            if cmd == "press":
                return browser.press(str(b.get("key") or ""))
            if cmd == "scroll":
                return browser.scroll(ref or None, int(b.get("dy") or 600))
            if cmd == "upload":
                return browser.upload(ref, [inside_data(str(f)) for f in b.get("files") or []])
            if cmd == "screenshot":
                shots.mkdir(parents=True, exist_ok=True)
                for old in sorted(shots.glob("shot-*.png"))[:-199]:   # the chats show the recent ones
                    old.unlink(missing_ok=True)
                return browser.screenshot(shots / f"shot-{int(time.time() * 1000)}.png", full=bool(b.get("full")))
            if cmd == "tabs":
                return {"tabs": browser.tabs()}
            if cmd == "tab":
                return browser.use_tab(int(b.get("index") or 0))
            if cmd == "back":
                return browser.back()
            if cmd == "front":
                browser.front()
                return browser.here()
            raise HttpError(400, f"unknown command {cmd!r}")

        return bro(run)

    return fill
