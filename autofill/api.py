"""HTTP routes for the Autofill workspace and for scripts/browser.py (the command Claude drives the
browser with). The browser belongs to the computer (one per Resume Studio window); the plan and
what was read belong to the data file."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.browser import BrowserError, ManagedBrowser
from core.chat import ChatRunner, Workspace, valid_effort, valid_mode, valid_model
from core.i18n import t
from core.server import App, FileResponse, HttpError, Request
from jobhunt import prompts as hunt_prompts
from resume import model
from resume import prompts as rp
from resume.service import Studio

from . import profile, prompts, route
from .service import Autofill
from .sites import Site

KIND = "autofill"   # the chat workspace this tab's chats had before the chat was one (old chats still name it)


def register(app: App, studio: Studio, chats: ChatRunner, browser: ManagedBrowser) -> Autofill:
    fill = Autofill(studio)
    shots = studio.store.state_dir / "browser"

    def publish(site_id: str | None = None) -> None:
        app.bus.publish("autofill", {"site": site_id})

    browser.on_change = lambda: app.bus.publish("browser", browser.status())
    browser.busy = lambda: bool(chats.running())   # Claude may use it at any point of a reply: it waits for the end

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
        return rp.ui_context(str(studio.store.path), None, None, roots)

    # every chat in the data folder, whichever workspace it was started in, knows the tracker's and the
    # browser's commands (docs/LAYOUT.md: one chat)
    chats.notes.append(lambda: hunt_prompts.tools_note(str(studio.store.path)) + "\n" + prompts.tools_note(str(studio.store.path)))
    # chats started here before the chat was one: they run in the data folder like any other
    chats.workspaces[KIND] = Workspace(KIND, studio.data_dir, label="Applying")

    def start_chat(b: dict, site: Site | None, title: str, text: str = "", procedure: str = "",
                   on_done: Callable[[bool], None] | None = None, action: str = "") -> dict:
        chat = chats.store.create(title=title[:60], site=site.id if site else None, **settings_of(b))
        if text:
            try:
                # the procedure goes to Claude with the message (so a later "go on" still has it); the chat shows the message
                chats.send(chat["id"], text + ("\n\n" + procedure if procedure else ""), shown=text, context=note(),
                           meta={"action": action or None, "site": site.id if site else None}, on_done=on_done)
            except RuntimeError as e:
                chats.store.delete(chat["id"])
                raise HttpError(409, str(e))
        return chats.store.meta(chat["id"]) or chat

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
        """Which resume subset and language the copy is compared with, and the ticks on the changes to push."""
        site, b = site_of(req), body(req)
        doc, _ = studio.store.load()
        vid = b.get("subset_id")
        if vid is not None and model.get_subset(doc, str(vid)) is None:
            raise HttpError(404, t("Subset {id} doesn't exist", id=vid))
        lang = b.get("lang")
        langs = [str(x) for x in (doc.get("settings") or {}).get("languages") or []]
        if lang is not None and str(lang) not in langs:
            raise HttpError(400, t("{lang} isn't one of this file's resume languages", lang=lang))
        ticks = b.get("ticks")
        if ticks is not None and not isinstance(ticks, dict):
            raise HttpError(400, "ticks must be an object")
        fill.choose(site, subset_id=str(vid) if vid is not None else None, lang=str(lang) if lang is not None else None,
                    ticks=ticks)
        if vid is not None or lang is not None:   # ticks come from this window only: no echo
            publish(site.id)
        return full(site)

    @app.route("PUT", "/api/autofill/{site}/local")
    def edit_local(req: Request):
        """One edit to the local copy (autofill/service.py `edit`)."""
        site, b = site_of(req), body(req)
        try:
            out = fill.edit(site, b)
        except KeyError as e:
            raise HttpError(404, f"no such item or conflict: {e}")
        except ValueError as e:
            raise HttpError(400, str(e))
        if b.get("op") != "set":   # typing comes from this window: no echo while the user types
            publish(site.id)
        return {**full(site), **out}

    @app.route("POST", "/api/autofill/{site}/open")
    def open_site(req: Request):
        """Show the site in the browser (the user signs in there, or looks)."""
        site = site_of(req)
        st = fill.load(site.id)
        url = str((st.get("current") or {}).get("url") or "") or site.home
        here = bro(lambda: browser.open(url if site.owns(url) else site.home))
        bro(browser.front)
        browser.claim()                                    # the user's now: it stays until they close it
        out = site.signed_out(here["url"])
        fill.save(site.id, signed_out=out)
        publish(site.id)
        return {**here, "signed_out": out, "browser": browser.status()}

    # ---- check for updates: by the recorded route (no Claude), or Claude reads and records it ----
    checking: dict[str, bool] = {}

    def run_check(site: Site) -> dict:
        st = fill.load(site.id)
        r = st.get("route")
        base, _ = fill.copies(site.id, st)

        def progress(n: int, total: int, url: str) -> None:
            fill.save(site.id, checking={"page": n + 1, "pages": total, "url": url})
            publish(site.id)

        try:
            with browser.hold():
                bro(browser.ensure)
                reading, scope = route.run(browser, site, r, base, progress)
            return {"ok": True, **fill.take_reading(site, reading, scope, "route")}
        except route.RouteBroken as e:
            fill.check_failed(site, str(e), e.signed_out)
            return {"ok": False, "error": str(e), "page": e.page, "signed_out": e.signed_out}
        except (BrowserError, HttpError) as e:
            fill.check_failed(site, str(e))
            return {"ok": False, "error": str(e)}
        finally:
            checking.pop(site.id, None)
            publish(site.id)

    def start_check(site: Site, wait: bool = False) -> dict:
        if checking.get(site.id):
            raise HttpError(409, t("Resume Studio is already checking {site}", site=site.name))
        checking[site.id] = True
        fill.save(site.id, checking={"page": 0, "pages": len(fill.load(site.id)["route"]["pages"]), "url": ""})
        publish(site.id)
        if wait:
            return run_check(site)
        threading.Thread(target=run_check, args=(site,), daemon=True, name=f"autofill-check-{site.id}").start()
        return {"started": True}

    def learn_chat(site: Site, b: dict, repair: str = "") -> dict:
        st = fill.load(site.id)
        bro(browser.ensure)

        def done(ok: bool) -> None:
            publish(site.id)

        base, _ = fill.copies(site.id, st)
        chat = start_chat(b, site, t("Read {site}", site=site.name), prompts.learn_message(site, bool(st.get("route")), repair),
                          prompts.learn_context(site, str(studio.store.path), st.get("route"), repair, base is not None),
                          done, action="autofill_read")
        fill.save(site.id, read_chat=chat["id"])
        publish(site.id)
        return chat

    @app.route("POST", "/api/autofill/{site}/check")
    def check(req: Request):
        """Check for updates: the route reads the site when one is recorded, else Claude reads it and records one."""
        site, b = site_of(req), body(req)
        if fill.load(site.id).get("route"):
            return {"mode": "route", **start_check(site, wait=bool(b.get("wait")))}
        return {"mode": "claude", "chat": learn_chat(site, b)}

    @app.route("POST", "/api/autofill/{site}/learn")
    def learn(req: Request):
        """Claude reads the site and records the route anew (first time, or after the site changed)."""
        site, b = site_of(req), body(req)
        return {"mode": "claude", "chat": learn_chat(site, b, str(fill.load(site.id).get("route_broken") or ""))}

    @app.route("POST", "/api/autofill/{site}/apply")
    def apply(req: Request):
        """Push the ticked changes: Claude, in the browser, reporting each one back."""
        site, b = site_of(req), body(req)
        view = fill.view(site)
        if not view["synced"]:
            raise HttpError(409, t("Check {site} for updates first", site=site.name))
        if view["conflicts"]:
            raise HttpError(409, t("Settle the conflicts first: a field was changed both here and on {site}", site=site.name))
        items = [i for i in view["pending"] if i["checked"]]
        if not items:
            raise HttpError(409, t("Nothing is ticked"))
        over = [i for i in items if any(f.get("over") for f in i["fields"])]
        if over:
            raise HttpError(409, t("Shorten the text that is over {site}'s limit first: {items}", site=site.name,
                                   items=", ".join(i["label"] for i in over)))
        bro(browser.ensure)
        st = fill.load(site.id)
        fill.save(site.id, applied={}, applying=[i["id"] for i in items])   # each push reports afresh

        def done(ok: bool) -> None:
            fill.save(site.id, applying=[])
            publish(site.id)
            # see that the site now says it (and pick up anything else that changed there)
            if fill.load(site.id).get("route") and not checking.get(site.id):
                try:
                    start_check(site)
                except HttpError:
                    pass

        chat = start_chat(b, site, t("Update {site}", site=site.name), prompts.apply_message(site, len(items)),
                          prompts.apply_context(site, str(studio.store.path), items, str((st.get("current") or {}).get("url") or "")),
                          done, action="autofill_apply")
        fill.save(site.id, apply_chat=chat["id"])
        publish(site.id)
        return {"chat": chat}

    # ---- what Claude sends back (scripts/browser.py) --------------------------------------
    def check_text(site: Site, res: dict) -> str:
        if not res.get("ok", True):
            return f"the route didn't read {site.name}: {res.get('error')}"
        v = fill.view(site)
        lines = [f"synced. {res.get('synced', 0)} change(s) from {site.name} came into the local copy; "
                 f"{res.get('conflicts', 0)} conflict(s); {len(v['pending'])} change(s) wait to go up. The local copy now has:"]
        for it in (v["local"] or {}).get("items") or []:
            lines.append(f"- {it['section']}: {profile.label_of(it['section'], it['fields'])}")
        lines.append(f"- skills: {len((v['local'] or {}).get('skills') or [])}; basics: {', '.join(k for k, x in ((v['local'] or {}).get('basics') or {}).items() if x) or '-'}")
        return "\n".join(lines)

    @app.route("POST", "/api/autofill/{site}/current")
    def current(req: Request):
        """Claude's own reading of the whole profile (when no route could be recorded)."""
        site = site_of(req)
        res, errs = fill.set_current(site, req.json())
        if errs:
            raise HttpError(400, "the profile JSON has problems:\n- " + "\n- ".join(errs))
        publish(site.id)
        return {"text": check_text(site, {"ok": True, **res})}

    @app.route("POST", "/api/autofill/{site}/route")
    def save_route(req: Request):
        """Claude's read route: saved, then run once on the live site; the answer is what it read."""
        site = site_of(req)
        r, errs = fill.set_route(site, req.json())
        if errs:
            raise HttpError(400, "the route has problems:\n- " + "\n- ".join(errs))
        site = fill.site(site.id) or site   # what it learned about the site (a site of the user's own)
        publish(site.id)
        st = fill.load(site.id)
        base, _ = fill.copies(site.id, st)
        try:
            reading, scope = route.run(browser, site, r, base)
        except route.RouteBroken as e:
            return {"text": f"saved, but running it failed{' on ' + e.page if e.page else ''}: {e}\nFix the script and send the route again."}
        return {"text": "saved. Running it read this (compare it with the page; when it matches, run `sync`):\n"
                        + json.dumps({"covers": scope, **reading}, ensure_ascii=False, indent=1)[:30000]}

    @app.route("POST", "/api/autofill/{site}/sync")
    def sync_now(req: Request):
        """`browser.py sync <site>`: check for updates now, by the route, and say what came of it."""
        site = site_of(req)
        if not fill.load(site.id).get("route"):
            raise HttpError(409, "no route is recorded yet: send one with `route` first")
        return {"text": check_text(site, start_check(site, wait=True))}

    @app.route("POST", "/api/autofill/{site}/report")
    def report(req: Request):
        site, b = site_of(req), body(req)
        try:
            fill.report(site, str(b.get("id") or ""), str(b.get("state") or ""), str(b.get("note") or ""))
        except KeyError:
            raise HttpError(404, f"no change {b.get('id')!r} waits to go up; use the ids from the approved list")
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
        browser.claim()
        return browser.status()

    @app.route("POST", "/api/browser/keep")
    def browser_keep(req: Request):
        """The user keeps a browser that was about to close itself: it is theirs now."""
        browser.claim()
        return browser.status()

    @app.route("POST", "/api/browser/stop")
    def browser_stop(req: Request):
        browser.stop()
        return browser.status()

    @app.route("POST", "/api/browser/sign-in")
    def browser_sign_in(req: Request):
        """Sign-in mode: the same profile as a plain browser (no remote control), for sites that refuse one."""
        bro(lambda: browser.sign_in(str(body(req).get("url") or "")))
        return browser.status()

    @app.route("POST", "/api/browser/sign-in/done")
    def browser_signed_in(req: Request):
        bro(browser.end_sign_in)
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
            if cmd == "extract":
                return {"data": browser.run_script(str(b.get("script") or ""), timeout=90)}
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
            if cmd == "front":                             # shown to the user: theirs to close
                browser.front()
                browser.claim()
                return browser.here()
            if cmd == "sign-in":
                browser.sign_in(str(b.get("url") or ""))
                return {"text": "the browser is open for signing in, without remote control; the window asks the user "
                                "to sign in there and switch back. Until then browser commands are refused."}
            raise HttpError(400, f"unknown command {cmd!r}")

        return bro(run)

    return fill
