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

from core import agents
from core.browser import BrowserError, ManagedBrowser, UserHasControl
from core.chat import ChatRunner, Workspace, valid_effort, valid_mode, valid_model
from core.handoff import Control
from core.i18n import t
from core.server import App, FileResponse, HttpError, Request
from jobhunt import prompts as hunt_prompts
from resume import model
from resume import prompts as rp
from resume.service import Studio

from . import profile, prompts, recipe, route, signin
from . import sites as sites_mod
from .service import Autofill
from .sites import Site

KIND = "autofill"   # the chat workspace this tab's chats had before the chat was one (old chats still name it)


def register(app: App, studio: Studio, chats: ChatRunner, browser: ManagedBrowser) -> Autofill:
    fill = Autofill(studio)
    shots = studio.store.cache_dir / "browser"

    def publish(site_id: str | None = None) -> None:
        app.bus.publish("autofill", {"site": site_id})

    # a site's page shows whether its read is running: any reply in a site's chat moves it, not only the one a
    # button started (a second message in 国聘's read chat left "the AI is reading" on after it ended, 2026-11-15)
    def chat_turned(chat_id: str, running: bool) -> None:
        site = (chats.store.meta(chat_id) or {}).get("site")
        if site:
            publish(str(site))
    chats.on_turn.append(chat_turned)

    browser.on_change = lambda: app.bus.publish("browser", browser.status())
    browser.busy = lambda: bool(chats.running())   # Claude may use it at any point of a reply: it waits for the end
    browser.active = chats.busy                    # a replying chat keeps its tab: no other work takes it meanwhile

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
        except (BrowserError, UserHasControl) as e:
            raise HttpError(409, str(e))

    # ---- who drives the chat's tab: the AI, or the user (core/handoff.py) -----------------------------
    hand = browser.handoff
    hand.cli = prompts.cli(str(studio.store.path))
    checks = signin.Checks(fill.root)
    judge = signin.Judge(browser, checks)   # is the user signed in: the address, the site's own check, the page

    def control_changed(key: str | None, c: Control) -> None:
        """The chat says it too, under "Working…": the tab is the user's, with the way to hand it back."""
        turn = chats.turns.get(key or "")
        if turn is None:
            return
        if c.mode == "user":
            chats.wait_status(key, t("You have taken over the browser. The AI is paused until you hand it back."), "control", {})
        elif c.mode == "ai" and turn.status_kind == "control":
            chats.wait_status(key, None)
    hand.on_change = control_changed

    def stop_work(key: str) -> None:
        if chats.store.meta(key):
            chats.cancel(key)
    hand.on_stop = stop_work
    hand.stoppable = lambda key: bool(chats.store.meta(key))

    # a reply that ended holds no tab: the next one starts with the AI driving (and the bar gone)
    chats.on_turn.append(lambda chat_id, running: None if running else hand.release(chat_id))

    def settings_of(b: dict) -> dict:
        out = {}
        if b.get("model") is not None and valid_model(b["model"]):
            out["model_choice"] = b["model"] or None
        if b.get("effort") is not None and valid_effort(b["effort"], b.get("agent")):
            out["effort"] = b["effort"] or None
        if b.get("mode") is not None and valid_mode(b["mode"], b.get("agent")):
            out["mode"] = b["mode"] or None
        if b.get("agent") and agents.valid(b["agent"]):
            out["agent"] = b["agent"]
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
            if b.get("company"):
                # a company's 网申 profile: its name and address are the company's
                site = fill.add_company_site(str(b["company"]))
            else:
                site = fill.add_site(preset=str(b.get("preset") or ""), **site_fields(b))
        except KeyError:
            raise HttpError(404, t("Unknown company: {name}", name=str(b.get("company"))))
        except ValueError as e:
            if str(e) == "company-site":
                raise HttpError(400, t("First give the address where you sign up on its careers site."))
            raise bad_home(e)
        publish(site.id)
        fill.want_logos()
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
        fill.want_logos()   # a new address: the logo follows it
        return {"sites": fill.summary(), "presets": fill.presets()}

    @app.route("DELETE", "/api/autofill/{site}")
    def remove_site(req: Request):
        fill.remove_site(site_of(req).id)
        publish(None)
        return {"sites": fill.summary(), "presets": fill.presets()}

    def full(site: Site) -> dict:
        v = fill.view(site)
        return {**v, "reading": bool(v["read_chat"]) and chats.busy(v["read_chat"]), "browser": browser.status(),
                "pushing": bool(pushing.get(site.id))}   # recipes replaying now (no AI)

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
        bro(lambda: browser.ensure(window=True))           # opened where the user sees it, not first without a window
        here = bro(lambda: browser.open(url if site.owns(url) else site.home))
        bro(browser.front)
        browser.claim()                                    # the user's now: it stays until they close it
        said, _ = judge(site, str(here.get("url") or ""))
        if said is not None:                               # no answer (no check, an ordinary address): as it was
            fill.save(site.id, signed_out=not said)
        out = bool(fill.load(site.id).get("signed_out"))
        publish(site.id)
        if needs_reading(site):
            watch_sign_in(site)
        return {**here, "signed_out": out, "browser": browser.status()}

    def needs_reading(site: Site) -> bool:
        """The site's page asks the user to do something before it reads again: sign in, or a read that failed with
        no way to tell whether the sign-in expired. Not a route the site's own check says is broken while signed in."""
        st = fill.load(site.id)
        if not st.get("route"):
            return False
        return bool(st.get("signed_out")) or (bool(st.get("route_broken"))
                                              and (st.get("last_check") or {}).get("sign_in") != "in")

    watching: dict[str, bool] = {}

    def watch_sign_in(site: Site) -> None:
        """The user opened the site to sign in ("Sign in to …"): the window sees by itself when they are, and reads the
        site again by the route (no AI, seconds), so the yellow bar goes once the read works; no "I've signed in" to
        press, and nothing at all when they were signed in already (国聘, 2026-11-16: the bar stayed although the
        site had them signed in). Signed in means the site's sign-in check says so when one is recorded, else a page
        of the site with no sign of a sign-in on it (no sign-in address, password field or lone "登录" button); then
        the read itself is the test, tried again while it fails."""
        if watching.get(site.id):
            return
        watching[site.id] = True

        def run() -> None:
            end = time.monotonic() + 15 * 60
            tried_at, tried_url = 0.0, ""
            try:
                while time.monotonic() < end and browser.state == "running" and needs_reading(site):
                    if tried_at:
                        time.sleep(3)
                    try:
                        with browser.acting(None), hand.exempt():   # a read-only look, never the user's input
                            url = str(bro(browser.here).get("url") or "")
                            if not site.owns(url):
                                continue
                            said, _ = judge(site, url, deep=not checks.find(url, site))
                    except (HttpError, BrowserError, UserHasControl):
                        continue
                    if said is False:
                        continue
                    # signed in (by the check), or no sign against it: read. Without a check, a read that failed is
                    # tried again when the page changes, else after a while (the user may still be signing in)
                    if said is None and tried_at and url == tried_url and time.monotonic() - tried_at < 20:
                        continue
                    tried_at, tried_url = time.monotonic(), url
                    if checking.get(site.id):
                        continue
                    try:
                        start_check(site, wait=True)
                    except HttpError:
                        pass
            finally:
                watching.pop(site.id, None)
        threading.Thread(target=run, daemon=True, name=f"autofill-signin-{site.id}").start()

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
            with browser.hold(), browser.acting(f"check:{site.id}"):
                bro(browser.ensure)
                reading, scope = route.run(browser, site, r, base, progress, judge=judge)
            return {"ok": True, **fill.take_reading(site, reading, scope, "route")}
        except route.RouteBroken as e:
            fill.check_failed(site, str(e), e.signed_out, e.sign_in)
            return {"ok": False, "error": str(e), "page": e.page, "signed_out": e.signed_out, "sign_in": e.sign_in}
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

    # ---- push the ticked changes: by recorded recipes (no AI), else the AI records them as it goes ----------
    pushing: dict[str, bool] = {}

    def profile_url(st: dict) -> str:
        """Where the profile is: where it was last read, else the recorded route's first page."""
        pages = (st.get("route") or {}).get("pages") or [{}]
        return str((st.get("current") or {}).get("url") or pages[0].get("url") or "")

    def after_push(site: Site) -> None:
        fill.save(site.id, applying=[])
        publish(site.id)
        # see that the site now says it (and pick up anything else that changed there); not while it asks to sign in
        st = fill.load(site.id)
        if st.get("route") and not st.get("signed_out") and not checking.get(site.id):
            try:
                start_check(site)
            except HttpError:
                pass

    def failed_note(e: recipe.RecipeFailed) -> dict:
        return {"by": "recipe", "kind": e.kind, "step": e.step + 1 if e.step >= 0 else None, "what": e.what,
                "saved": e.saved}

    def push_one(site: Site, change: dict) -> recipe.RecipeFailed | None:
        """One change by its recipe, in the browser tab this thread works in; reported done only once read back."""
        fill.report(site, change["id"], "doing", by="recipe")
        publish(site.id)
        st = fill.load(site.id)
        try:
            said = recipe.push(browser, site, st, change, judge=judge)
        except recipe.RecipeFailed as e:
            fill.report(site, change["id"], "failed", str(e), **failed_note(e))
            fill.recipe_failed(site, change, e)
            if e.kind == "signed_out":
                fill.save(site.id, signed_out=True)
            publish(site.id)
            return e
        fill.report(site, change["id"], "done", said, by="recipe")
        publish(site.id)
        return None

    def ai_chat(site: Site, b: dict, items: list[dict], redo: list[str], on_done: Callable[[bool], None]) -> dict:
        st = fill.load(site.id)
        chat = start_chat(b, site, t("Update {site}", site=site.name), prompts.apply_message(site, len(items)),
                          prompts.apply_context(site, str(studio.store.path), items, profile_url(st), st, redo),
                          on_done, action="autofill_apply")
        fill.save(site.id, apply_chat=chat["id"])
        publish(site.id)
        return chat

    def run_pushes(site: Site, by_recipe: list[dict], by_ai: list[dict], redo: list[str], b: dict) -> None:
        stopped = False
        try:
            with browser.hold(), browser.acting(f"push:{site.id}"):
                for change in by_recipe:
                    if stopped:
                        fill.report(site, change["id"], "skipped", "", by="recipe")
                        continue
                    e = push_one(site, change)
                    stopped = e is not None and e.kind == "signed_out"
        except BrowserError as e:   # the browser itself went away: what is left waits for the next push
            for change in by_recipe:
                if (fill.load(site.id).get("applied") or {}).get(change["id"], {}).get("state") == "doing":
                    fill.report(site, change["id"], "failed", str(e), by="recipe", kind="browser")
            stopped = True
        finally:
            pushing.pop(site.id, None)
        if by_ai and not stopped:
            fill.save(site.id, applying=[i["id"] for i in by_ai])
            try:
                ai_chat(site, b, by_ai, redo, lambda ok: after_push(site))
                return
            except HttpError as e:
                for i in by_ai:
                    fill.report(site, i["id"], "failed", str(e.args[-1] if e.args else e))
        after_push(site)

    @app.route("POST", "/api/autofill/{site}/apply")
    def apply(req: Request):
        """Push the ticked changes. One with a recorded recipe (and a read route to check it by) goes up by the recipe,
        no AI; the rest go to the AI, which records a recipe for each kind as it makes it. `only`: just these changes
        (ticked or not); `ai`: by the AI, recording their recipes anew (a recipe that broke)."""
        site, b = site_of(req), body(req)
        view = fill.view(site)
        if not view["synced"]:
            raise HttpError(409, t("Check {site} for updates first", site=site.name))
        if view["conflicts"]:
            raise HttpError(409, t("Settle the conflicts first: a field was changed both here and on {site}", site=site.name))
        if pushing.get(site.id) or view["applying"]:
            raise HttpError(409, t("Resume Studio is already making changes on {site}", site=site.name))
        only = b.get("only")
        items = [i for i in view["pending"] if (i["id"] in only if isinstance(only, list) else i["checked"])]
        if not items:
            raise HttpError(409, t("Nothing is ticked"))
        over = [i for i in items if any(f.get("over") for f in i["fields"])]
        if over:
            raise HttpError(409, t("Shorten the text that is over {site}'s limit first: {items}", site=site.name,
                                   items=", ".join(i["label"] for i in over)))
        force_ai = bool(b.get("ai"))
        by_recipe = [] if force_ai else [i for i in items if i["via"] == "recipe"]
        by_ai = [i for i in items if i not in by_recipe]
        # a recipe recorded anew: the kinds the user sent to the AI, and the ones whose recipe broke
        redo = sorted({k for i in by_ai for k in recipe.keys_of(i)
                       if force_ai or k in (view.get("recipe_broken") or {})})
        # changes go up on the user's behalf: in a browser they can watch and take over, from the start (showing it
        # later would open it anew and lose what was typed)
        bro(lambda: browser.ensure(window=True))
        fill.save(site.id, applied={}, applying=[i["id"] for i in items])   # each push reports afresh
        if not by_recipe:
            try:
                chat = ai_chat(site, b, by_ai, redo, lambda ok: after_push(site))
            except HttpError:
                fill.save(site.id, applying=[])
                raise
            return {"mode": "claude", "chat": chat, "recipe": [], "ai": [i["id"] for i in by_ai]}
        pushing[site.id] = True
        publish(site.id)
        threading.Thread(target=run_pushes, args=(site, by_recipe, by_ai, redo, b), daemon=True,
                         name=f"autofill-push-{site.id}").start()
        return {"mode": "recipe" if not by_ai else "mixed", "chat": None,
                "recipe": [i["id"] for i in by_recipe], "ai": [i["id"] for i in by_ai]}

    @app.route("POST", "/api/autofill/{site}/recipe")
    def save_recipe(req: Request):
        """`browser.py recipe <site> <key>`: Claude's write recipe for one kind of change, checked and kept."""
        site, b = site_of(req), body(req)
        key = str(b.get("key") or "")
        r, errs = fill.set_recipe(site, key, b.get("recipe"))
        if errs:
            raise HttpError(400, "the recipe has problems:\n- " + "\n- ".join(errs))
        publish(site.id)
        return {"text": f"saved the {key} recipe ({len(r['steps'])} steps). Now make the change by it: "
                        f"`push {site.id} <change id>` (add `--dry` first to fill the dialog in without saving)."}

    @app.route("POST", "/api/autofill/{site}/push")
    def push_by_recipe(req: Request):
        """`browser.py push <site> <id>`: one approved change by its recipe, in the chat's own tab, read back by the
        route and reported. `dry`: fill it in, stop before saving, leave the dialog open."""
        site, b = site_of(req), body(req)
        cid = str(b.get("id") or "")
        change = next((i for i in fill.pending(site) if i["id"] == cid), None)
        if change is None:
            raise HttpError(404, f"no change {cid!r} waits to go up; use the ids from the approved list")
        if not fill.load(site.id).get("route"):
            raise HttpError(409, "no read route is recorded: a recipe's change is checked by reading it back")
        with browser.acting(req.headers.get("X-Resume-Studio-Chat")):
            if b.get("dry"):
                try:
                    recipe.push(browser, site, fill.load(site.id), change, dry=True, judge=judge)
                except recipe.RecipeFailed as e:
                    more = f" ({e.detail})" if e.detail else ""
                    return {"text": f"stopped at step {e.step + 1} ({e.what}): {e}{more}. Nothing was saved."}
                return {"text": "filled in by the recipe and NOT saved; the dialog is still open. Check it with "
                                "`snapshot --dialog`, close it without saving (its cancel button, or press Escape), "
                                "then fix the recipe or `push` for real."}
            e = push_one(site, change)
        if e is None:
            return {"text": f"done: {(fill.load(site.id).get('applied') or {}).get(cid, {}).get('note')}. Reported done."}
        where = f" at step {e.step + 1} ({e.what})" if e.step >= 0 else ""
        tail = ("Something may have been saved: snapshot the page and check before doing anything else."
                if e.saved else "Nothing was saved.")
        more = f" ({e.detail})" if e.detail else ""
        return {"text": f"failed{where}: {e}{more}. {tail} Reported failed; fix the recipe and send it again, "
                        "or make the change by hand and report it."}

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
            with browser.acting(req.headers.get("X-Resume-Studio-Chat")):   # in the tab of the chat that sent it
                reading, scope = route.run(browser, site, r, base, judge=judge)
        except route.RouteBroken as e:
            if e.signed_out:
                return {"text": f"saved, but {site.name} is not signed in now ({e}). Run `signed-out {site.id}`, then "
                                f"`wait-signed-in {site.id}`, and send the route again once the user has signed in."}
            return {"text": f"saved, but running it failed{' on ' + e.page if e.page else ''}: {e}\nFix the script and send the route again."}
        return {"text": "saved. Running it read this (compare it with the page; when it matches, run `sync`):\n"
                        + json.dumps({"covers": scope, **reading}, ensure_ascii=False, indent=1)[:30000]
                        + needs_check_text(site, reading.get("url") or r["pages"][0]["url"])}

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
        return {"text": f"the window now asks the user to sign in to {site.name} in the browser window. Run "
                        f"`wait-signed-in {site.id}` now: it returns once they have, and you go on in the same reply "
                        "(the chat shows the user that you are waiting). Don't end your reply to ask them to tell you."}

    # ---- has the user signed in? Asked of the site (a recorded sign-in check), else the user says so ----
    # 国聘 (2026-11-15): the AI stopped to ask "tell me when you're signed in". Then 零跑 (北森, 2026-11-15): the wait
    # took a sign-in dialog with no password field (a phone number and an SMS code) for "signed in", and the AI went
    # on clicking while the user was typing the code. Now the tab is the user's while they sign in, and only they, or
    # a check that asks the site's own server, give it back.
    PASSWORD_SHOWN = "async () => [...document.querySelectorAll('input[type=password]')].some((e) => e.offsetParent !== null)"

    def owner_of(url: str) -> Site | None:
        return next((s for s in fill.sites() if s.owns(url)), None)

    def by_check(url: str, site: Site | None) -> bool | None:
        """What the recorded sign-in check for this page says (None: none recorded, or no answer now)."""
        rec = checks.find(url, site or owner_of(url))
        if not rec:
            return None
        return signin.run(browser, str(rec.get("script") or ""))[0]

    def on_sign_in(site: Site | None, url: str) -> bool:
        """The page at `url` (open in this work's tab) asks to sign in. A sign-in check, when recorded, answers;
        else the signs: the site's own sign-in paths, an address that names one, a password field."""
        said = by_check(url, site) if url.startswith("http") else None
        if said is not None:
            return not said
        if (site and site.signed_out(url)) or sites_mod.login_url(url):
            return True
        if site is None:   # a page of a site not on the list: its address only (a password field there may be anything)
            return False
        try:
            with hand.exempt():
                return bool(browser.run_script(PASSWORD_SHOWN, timeout=5))
        except (BrowserError, UserHasControl):
            return False

    def signed_in_now(site: Site | None) -> None:
        if site and fill.load(site.id).get("signed_out"):
            fill.save(site.id, signed_out=False)
            publish(site.id)

    @app.route("POST", "/api/autofill/{site}/wait-signed-in")
    def wait_signed_in(req: Request):
        """`browser.py wait-signed-in <site>`: the chat's tab is the user's until they sign in. Back once they choose
        "I've signed in" (on the page's bar or in the chat), or the site's sign-in check says they are. At most 90 s a
        run, so the AI's command never times out; it runs it again while the user is still at it."""
        return wait_for_sign_in(site_of(req), req.headers.get("X-Resume-Studio-Chat") or "")

    @app.route("POST", "/api/browser/wait-signed-in")
    def wait_signed_in_any(req: Request):
        """`browser.py wait-signed-in` with no site: any site (a company's careers site, a page the AI found)."""
        return wait_for_sign_in(None, req.headers.get("X-Resume-Studio-Chat") or "")

    def host_of(url: str) -> str:
        m = re.match(r"https?://(?:www\.)?([^/:?#]+)", url or "")
        return m.group(1) if m else ""

    def again(site: Site | None) -> str:
        return f"`{prompts.cli(str(studio.store.path))} wait-signed-in{' ' + site.id if site else ''}`"

    def record_check_text(url: str, site: Site | None) -> str:
        """After a sign-in the user confirmed: no check is recorded for the site, so the AI records one now."""
        key = signin.key_for(url, site)
        if checks.all().get(key):
            return ""
        b = prompts.cli(str(studio.store.path))
        whose = f"the {key[7:]} recruiting system" if key.startswith("system:") else key[7:]
        return (f" No sign-in check is recorded for {whose} yet, so Resume Studio can't tell by itself when the user is "
                f"signed in there. Record one now, before going on: `{b} requests` lists the calls this page made; find the "
                "one whose answer is the signed-in user (a name, an id, a phone number), then "
                f"`{b} sign-in-check --stdin` with "
                "{\"script\": \"async (h) => { const r = await fetch('<that address>', {credentials: 'include'}); "
                "const j = await r.json(); return {signed_in: <true when j names a user>}; }\"}. "
                "It is run at once here (it must say true) and in a fresh browser with no sign-in (it must say false).")

    def needs_check_text(site: Site, url: str) -> str:
        """A route just recorded on a site with no sign-in check: without one, a check that fails later can't tell an
        expired sign-in from a route that broke (国聘 asks to sign in again often), so the AI records one now."""
        said = record_check_text(url, site)
        return "\n\nBefore `sync`:" + said if said else ""

    def plain_now() -> bool:
        """The user is signing in without remote control, or switching back from it (the browser is off for a
        moment in between: not the user closing it)."""
        return getattr(browser, "state", "") == "signin" or bool(getattr(browser, "returning", False))

    def wait_for_sign_in(site: Site | None, chat_id: str) -> dict:
        end = time.monotonic() + float(getattr(fill, "sign_in_wait", 90.0))
        try:
            with browser.acting(chat_id or None), hand.exempt():
                # the user signing in without remote control (sign-in mode): nothing to look at until they switch back
                plain = False
                while plain_now():
                    plain = True
                    if chat_id:
                        chats.wait_status(chat_id, t("Waiting for you to sign in in the browser window and switch back."),
                                          "signin", {"url": browser.signin_url, "site": site.id if site else ""})
                    if time.monotonic() > end:
                        return {"text": "not signed in yet: the user is signing in in the browser without remote control. "
                                        f"Run {again(site)} again at once; don't end your reply."}
                    time.sleep(1.5)
                if plain and getattr(browser, "state", "") != "running":   # checked before `here`, which starts it again
                    return {"text": "the user closed the sign-in window without switching back. Open the page "
                                    f"again; if it still asks to sign in, ask them in one line and run {again(site)}."}
                here = bro(browser.here)
                url = str(here.get("url") or "")
                owner = site or owner_of(url)
                name = owner.name if owner else (host_of(url) or "the site")
                if plain:   # the user switched back themselves: that is their "I've signed in"
                    signed_in_now(owner)
                    return {"text": f"the user signed in without remote control and switched back; the tab is on {url}. Take "
                                    "a new snapshot, then go on." + record_check_text(url, owner)}
                rec = checks.find(url, owner)
                if rec and signin.run(browser, str(rec.get("script") or ""))[0]:
                    signed_in_now(owner)   # the site says they are signed in already: no need to hand the tab over
                    return {"text": f"signed in: {name}'s sign-in check says the user is signed in. Go on with what you "
                                    "were doing (open the page again if this one still shows a sign-in)."}
                # the user signs in where they can see it: a windowless browser opens anew with a window now, before
                # the tab is theirs (opening it later would restart the browser under their typing)
                if hand.get(browser.current).mode != "signin":   # once: a wait run again doesn't pull the window up again
                    bro(browser.front)
                    browser.claim()
                tid = browser.current
                hand.ask_sign_in(tid, name)
                signs = signin.Signs(browser) if not rec else None
                last_check = time.monotonic()
                while True:
                    # the chat's own line under "Working…": the reply waits on the user, it isn't stuck
                    if chat_id:
                        chats.wait_status(chat_id, t("Waiting for you to sign in to {site} in Resume Studio's browser.", site=name),
                                          "signin", {"url": url, "site": site.id if site else ""})
                    c = hand.wait_ai(tid, 1.5)
                    if tid not in browser.pages:
                        # the browser may be in the middle of switching to sign-in mode (closed, not yet reopened):
                        # it holds its lock through that, so the state read after it is the settled one
                        with browser.lock:
                            pass
                    if plain_now():
                        # the user chose "No code arriving?": the same browser without remote control. They switch
                        # back when they are signed in, and that is their "I've signed in"
                        while plain_now():
                            if chat_id:
                                chats.wait_status(chat_id, t("Waiting for you to sign in in the browser window and switch back."),
                                                  "signin", {"url": browser.signin_url, "site": site.id if site else ""})
                            if time.monotonic() > end:
                                return {"text": "not signed in yet: the user is signing in in the browser without remote "
                                                f"control. Run {again(site)} again at once; don't end your reply."}
                            time.sleep(1.5)
                        if getattr(browser, "state", "") != "running":   # they closed that window instead
                            return {"text": "the user closed the sign-in window without switching back. Open the page "
                                            f"again; if it still asks to sign in, ask them in one line and run {again(site)}."}
                        signed_in_now(owner)
                        return {"text": "the user signed in without remote control and switched back. Take a new snapshot, "
                                        "then go on." + record_check_text(url, owner)}
                    if tid not in browser.pages:
                        return {"text": "the user closed the tab the sign-in was in. Open the page again with `open`; if it "
                                        f"still asks to sign in, ask the user in one line and run {again(site)}."}
                    if c.mode == "ai":
                        if c.back == "end":
                            return {"text": "the user stopped the wait. End your reply now."}
                        said = by_check(url, owner) if rec else None
                        if said is False:
                            return {"text": f"the user handed the tab back, but {name}'s sign-in check says they are not signed "
                                            "in. Take a snapshot: if the page still asks to sign in, ask them in one line and "
                                            f"run {again(site)} again."}
                        signed_in_now(owner)
                        if c.back == "check":
                            return {"text": f"signed in: {name}'s sign-in check says the user is signed in. Take a new "
                                            "snapshot, then go on with what you were doing."}
                        return {"text": f"the user handed the tab back: they have signed in to {name}. Take a new snapshot "
                                        "before anything else (the page may have changed), then go on." + record_check_text(url, owner)}
                    if rec and time.monotonic() - last_check >= 3:
                        last_check = time.monotonic()
                        if by_check(url, owner):
                            hand.give(tid, "check")
                            continue
                    if signs is not None and signs.seen():
                        hand.detected(tid, "likely")
                    if time.monotonic() > end:
                        return {"text": f"not signed in yet: the user is still signing in to {name} (a code, a scan). Run "
                                        f"{again(site)} again at once, without ending your reply; the tab stays theirs and "
                                        "the window shows them that you are waiting."}
        finally:
            if chat_id:
                chats.wait_status(chat_id, None)

    @app.route("POST", "/api/browser/wait")
    def wait_user(req: Request):
        """`browser.py wait`: the user took over the chat's tab; back once they hand it back (at most 90 s a run)."""
        chat_id = req.headers.get("X-Resume-Studio-Chat") or ""
        tid = browser.tab_of.get(chat_id or None)
        c = hand.get(tid)
        if c.mode == "ai":
            return {"text": "the tab is yours: the user isn't using it. Go on."}
        c = hand.wait_ai(tid, float(getattr(fill, "sign_in_wait", 90.0)))
        if c.mode == "ai":
            if c.back == "end":
                return {"text": "the user stopped your reply's work in the browser. End your reply now."}
            return {"text": "the user handed the tab back. Take a new snapshot before anything else: they may have "
                            "changed the page. Then go on."}
        if c.mode == "signin":
            return {"text": f"the user is signing in: run {again(None)} instead."}
        return {"text": f"still the user's: they are using the page. Run `{prompts.cli(str(studio.store.path))} wait` "
                        "again at once, without ending your reply."}

    @app.route("POST", "/api/browser/control")
    def control(req: Request):
        """The user's own say over a chat's tab, from the chat (the page's bar does the same): `take` it, `give` it
        back (also "I've signed in"), or `stop` the AI's reply."""
        b = body(req)
        chat_id, action = str(b.get("chat") or ""), str(b.get("action") or "")
        tid = browser.tab_of.get(chat_id)
        if action not in ("take", "give", "stop"):
            raise HttpError(400, "action must be take, give or stop")
        if action == "stop":
            if tid:
                hand.give(tid, "end")
            stop_work(chat_id)
        elif action == "give" and browser.state == "signin":   # signed in without remote control: switch back
            bro(browser.end_sign_in)
        elif not tid:
            raise HttpError(409, t("This chat has no page open in the browser."))
        elif action == "take":
            hand.take(tid, "button")
        else:
            hand.give(tid, "button")
        return {"control": hand.get(tid).mode if tid else "ai"}

    @app.route("POST", "/api/browser/sign-in-check")
    def save_check(req: Request):
        """`browser.py sign-in-check`: the AI's check for "is the user signed in here", tested both ways first: on
        the chat's page (signed in: it must say true) and in a fresh browser context with no cookies (false)."""
        b = body(req)
        script = str(b.get("script") or "")
        errs = signin.problems(script)
        if errs:
            raise HttpError(400, "the check has problems:\n- " + "\n- ".join(errs))
        chat_id = req.headers.get("X-Resume-Studio-Chat") or ""
        with browser.acting(chat_id or None):
            url = str(bro(browser.here).get("url") or "")
            if not url.startswith("http"):
                raise HttpError(409, "open a page of the site first (signed in), then send the check")
            site = owner_of(url)
            yes, said = signin.run(browser, script)
            if yes is not True:
                raise HttpError(409, f"on this page, where the user is signed in, the check didn't say signed_in true: {said}. "
                                     "Fix it and send it again.")
            try:
                out = browser.run_signed_out(url, script)
            except BrowserError as e:
                raise HttpError(409, f"the check couldn't be tried without a sign-in: {e}")
            if not (isinstance(out, dict) and out.get("signed_in") is False):
                raise HttpError(409, "in a fresh browser with no sign-in the check didn't say signed_in false: "
                                     + json.dumps(out, ensure_ascii=False)[:300] + ". It must ask the server about the "
                                     "signed-in user, not read something every visitor has.")
        key = signin.key_for(url, site)
        checks.save(key, script, url)
        whose = f"every company on {key[7:]}" if key.startswith("system:") else key[7:]
        return {"text": f"saved: the sign-in check for {whose}. It said true here and false without a sign-in. From now "
                        "on Resume Studio knows by itself when the user has signed in there."}

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
            if cmd == "hover":
                return browser.hover(ref)
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
            if cmd == "requests":
                calls = browser.requests(api_only=not b.get("all"))[-int(b.get("max") or 60):]
                if not calls:
                    return {"text": "no requests from this page yet (open or reload it first)"}
                return {"text": "\n".join(f"{c['method']} {c['status'] if c['status'] is not None else '…'} "
                                          f"{c['type'] or '-'} {c['url'][:300]}" for c in calls)}
            raise HttpError(400, f"unknown command {cmd!r}")

        # Sign-in mode (a plain browser, without remote control) closes the browser the user may be signing in
        # with: only the user switches to it, from the window (零跑, 2026-11-15: the AI switched on its own and the
        # half-typed sign-in was gone)
        if cmd == "sign-in":
            raise HttpError(409, "only the user switches the browser to sign-in mode (it closes the page they may be "
                                 "signing in on). If the sign-in fails in this browser (a code never arrives), tell them in "
                                 "one line that they can choose \"No code arriving?\" in the window, and keep waiting with "
                                 "`wait-signed-in`.")

        # each chat works in a browser tab of its own (scripts/browser.py sends its id): two chats, or a chat and a
        # check for updates, never land on each other's page
        with browser.acting(req.headers.get("X-Resume-Studio-Chat")):
            browser.take_waited()
            r = bro(run)
            # a chat's site that was waiting for a sign-in: a page of the site that isn't one clears it, so the window
            # stops asking (it waited for the next full read, 2026-11-15)
            chat_site = fill.site(str((chats.store.meta(req.headers.get("X-Resume-Studio-Chat") or "") or {}).get("site") or ""))
            if chat_site and isinstance(r, dict) and r.get("url") and fill.load(chat_site.id).get("signed_out") \
                    and chat_site.owns(str(r["url"])) and not on_sign_in(chat_site, str(r["url"])):
                signed_in_now(chat_site)
            # a page that asks to sign in is said to the AI where it lands, with what to do: wait in this reply (the
            # chat shows the user it waits), never end the reply asking to be told (2026-11-15: the chat just stopped)
            if cmd in ("open", "click", "back", "tab", "press", "snapshot") and isinstance(r, dict) and str(r.get("url") or "").startswith("http"):
                here_url = str(r["url"])
                owner = owner_of(here_url)
                if on_sign_in(owner, here_url):
                    if owner:
                        fill.save(owner.id, signed_out=True)
                        publish(owner.id)
                    lead = ("this page asks to sign in. Tell the user in one line to sign in in Resume Studio's browser, then "
                            f"run `wait-signed-in{' ' + owner.id if owner else ''}` and go on in this same reply once it returns; "
                            "never type a password or code, and never end your reply to wait.")
                    r["note"] = f"{r['note']} {lead}" if r.get("note") else lead
            # time spent queuing is said, so the AI never takes it for the site being slow or unreadable
            waited = {k: v for k, v in browser.take_waited().items() if v >= 2}
            if waited and isinstance(r, dict):
                say = "; ".join(f"{round(v)} s for {k}" for k, v in waited.items())
                lead = f"this command first waited {say}: that was the browser being busy, not this page being slow."
                r["note"] = f"{r['note']} {lead}" if r.get("note") else lead
            return r

    return fill
