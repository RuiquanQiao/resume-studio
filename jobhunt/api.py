"""The tracker's routes: the window's tables and pages, and one command endpoint for scripts/hunt.py."""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

from core.i18n import t
from core.server import App, FileResponse, HttpError, Request
from jobs.base import JobTool, ToolError
from jobs.signups import Signups
from resume import model
from resume.service import Studio

from . import logos
from .logos import fetch_logo
from .postings import Postings
from .scan import Scanner
from .store import LOGO_TYPES, STATE_IDS, Hunt

TRACKERS = ["studio", "career-ops"]


def subset_to_send(studio: Studio, subset_id: str | None) -> str:
    doc, _ = studio.store.load()
    if not subset_id:
        raise HttpError(409, t("Pick the subset this goes out with first"))
    if subset_id == model.ALL_ID or model.get_item(doc, "subset", subset_id) is None:
        raise HttpError(404, t("Subset {id} doesn't exist", id=subset_id))
    return subset_id


def keep_version(studio: Studio, subset_id: str, where: str) -> int:
    """The version that goes out: what the subset says now, kept as one if it isn't already (after
    going back to v1, it goes out as v1, not as a copy of it)."""
    st = studio.subset_versions(subset_id)["status"] or {}
    if st.get("same_as"):
        return int(st["same_as"])
    r = studio.commit_subset(subset_id, t("Sent: {where}", where=where))
    return int(r["made"]["n"])


def register(app: App, studio: Studio, tool: JobTool, prefs: Any, *, platforms: Callable[[], list[dict]],
             browser: Any = None) -> Hunt:
    hunt = Hunt(studio.store.state_dir)
    hunt.on_change = lambda: app.bus.publish("hunt", {})
    posts = Postings(hunt)
    scanner = Scanner(hunt, posts, tool, browser, lambda: app.bus.publish("hunt", {}))
    hunt.scanner = scanner   # the app (and tests) reach the scanner through the tracker
    studio.on_subset_renamed.append(hunt.rename_subset)
    hunt.migrate()
    # the sign-ups the Applications tab kept before move in, once
    if not hunt.exists():
        old = Signups(studio.store.state_dir).all()
        if old:
            hunt.import_signups(old)

    def tracker() -> str:
        v = (prefs.load() or {}).get("tracker")
        return v if v in TRACKERS else "studio"

    def body(req: Request) -> dict:
        try:
            b = req.json()
        except ValueError:
            raise HttpError(400, "invalid JSON")
        return b if isinstance(b, dict) else {}

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except KeyError as e:
            raise HttpError(404, str(e).strip("'\""))
        except ValueError as e:
            raise HttpError(400, str(e))
        except ToolError as e:
            raise HttpError(409, str(e))

    def subset_label(doc: Any, sid: str) -> str:
        sub = model.get_item(doc, "subset", sid) if sid else None
        return str((sub or {}).get("label") or sid or "")

    def logo_url(c: dict) -> str:
        return f"/api/hunt/logos/{c['id']}?v={c.get('updated', '')}" if c.get("logo") else ""

    # ---- logos: a company with a site gets its logo from it, in the background, with no Claude ----
    tried: set[tuple[str, tuple[str, ...]]] = set()   # (company, its sites) already looked at this run
    tried_lock = threading.Lock()

    def logo_sites(c: dict) -> tuple[str, ...]:
        return tuple(u for u in dict.fromkeys([c.get("site") or "", *[s["url"] for s in c.get("sites") or []]]) if u)

    def find_logo(cid: str) -> None:
        try:
            c = hunt.company(cid)
        except KeyError:
            return
        sites = logo_sites(c)
        finder = hunt.find_logo
        with tried_lock:
            if c.get("logo") or "logo" in (c.get("kept") or []) or not sites or finder is None or (cid, sites) in tried:
                return
            tried.add((cid, sites))
        got = finder(list(sites))
        if not got:
            return
        data, ctype, src = got
        try:
            c = hunt.company(cid)
            if c.get("logo") or "logo" in (c.get("kept") or []):   # Claude or the user got there first
                return
            hunt.save_logo(cid, data, ctype)
            hunt.update_company(cid, {"sources": {"logo": src}}, source="auto")
        except (KeyError, ValueError):
            pass

    def want_logos(*cids: str) -> None:
        ids = list(cids) or [c["id"] for c in hunt.companies()]
        threading.Thread(target=lambda: [find_logo(i) for i in ids], name="hunt-logos", daemon=True).start()

    hunt.find_logo = (lambda sites: logos.from_sites(sites, types=LOGO_TYPES)) \
        if os.environ.get("STUDIO_LOGOS") != "off" else None
    hunt.want_logos = want_logos
    want_logos()   # the companies that have a site and no logo yet

    def fill(ref: str, fields: dict) -> dict:
        """Claude's research on a company, every field at once, the logo downloaded into the data folder."""
        fields = fields if isinstance(fields, dict) else {}
        c, kept = hunt.fill(ref, fields)
        logo_error = ""
        if fields.get("logo_url"):
            if "logo" in (c.get("kept") or []):
                kept = sorted({*kept, "logo"})
            else:
                try:
                    data, ctype = fetch_logo(str(fields["logo_url"]))
                    hunt.save_logo(c["id"], data, ctype)
                except ValueError as e:
                    logo_error = str(e)
        want_logos(c["id"])   # no logo from Claude (or it failed): the site's own icon
        return {"company": company_view(c["id"]), "kept": kept, "logo_error": logo_error, "text": "filled"}

    def company_view(ref: str) -> dict:
        c = hunt.company(ref)
        doc, _ = studio.store.load()
        apps = sorted(hunt.applications(c["id"]), key=lambda a: a.get("order") or 0)
        evs = [e for e in hunt.events() if e["app"] in {a["id"] for a in apps}]
        st = posts.scan_state(c["id"])
        return {"company": {**c, "logo_url": logo_url(c), "subset_label": subset_label(doc, c.get("subset") or ""),
                            "subset_exists": bool(c.get("subset")) and model.get_item(doc, "subset", c["subset"]) is not None},
                "applications": apps, "events": evs,
                "postings": sorted(posts.all(c["id"]), key=lambda p: (bool(p.get("closed")), p.get("category") or "", p["title"])),
                "scan": {k: v for k, v in st.items() if k != "route"} | {"recorded": bool(st.get("route")),
                                                                       "running": scanner.running.get(c["id"])},
                "keywords": posts.scans()["keywords"]}

    def app_view(ref: str) -> dict:
        a = hunt.application(ref)
        c = hunt.company(a["company"])
        return {"application": a, "company": c, "events": hunt.events(a["id"])}

    # ---- sending: the subset is kept as a version and pinned; career-ops gets the rows if it is the tracker ----
    def copy_to_tracker(apps: list[dict], c: dict, label: str, n: int) -> int:
        if tracker() != "career-ops" or not tool.installed():
            return 0
        new = [a for a in apps if not a.get("co_app")]
        if not new:
            return 0
        rows = [{"company": c["name"], "role": a["role"], "status": "applied", "url": a.get("url") or "",
                 "notes": t("Sent from Resume Studio · {subset} v{n}", subset=label, n=n)} for a in new]
        ids = tool.add_applications(rows)
        for a, i in zip(new, ids):
            hunt.link_tracker(a["id"], i)
        return len(ids)

    def send_company(ref: str, source: str) -> dict:
        c = hunt.company(ref)
        sub = subset_to_send(studio, c.get("subset") or None)
        todo = [a for a in sorted(hunt.applications(c["id"]), key=lambda a: a.get("order") or 0)
                if a.get("channel") == "site" and not a.get("sent")]
        if not todo:
            raise HttpError(409, t("Every role on {company}'s site has already gone out", company=c["name"]))
        n = keep_version(studio, sub, c["name"])
        out = [hunt.mark_sent(a["id"], sub, n, source=source) for a in todo]
        doc, _ = studio.store.load()
        copied = guard(lambda: copy_to_tracker(out, c, subset_label(doc, sub), n))
        return {"n": n, "subset": sub, "sent": [a["id"] for a in out], "copied": copied}

    def send_app(ref: str, subset: str | None, source: str) -> dict:
        a = hunt.application(ref)
        c = hunt.company(a["company"])
        sub = subset_to_send(studio, subset or a.get("subset") or c.get("subset") or None)
        n = keep_version(studio, sub, f"{c['name']} — {a['role']}")
        a = hunt.mark_sent(a["id"], sub, n, source=source)
        doc, _ = studio.store.load()
        copied = guard(lambda: copy_to_tracker([a], c, subset_label(doc, sub), n))
        return {"n": n, "subset": sub, "sent": [a["id"]], "copied": copied}

    # ---- the window ------------------------------------------------------------
    @app.route("GET", "/api/hunt")
    def overview(req: Request):
        tb = hunt.table()
        summ = posts.summary()
        for c in tb["companies"]:
            c["logo_url"] = logo_url(c)
            c["postings"] = summ.get(c["id"]) or {"open": 0, "matching": 0}
            c["postings"]["needs"] = posts.scan_state(c["id"]).get("needs") or ""
        sc = posts.scans()
        return {**tb, "platforms": platforms(), "tracker": tracker(), "career_ops": tool.installed(),
                "scan": {"keywords": sc["keywords"], "daily": sc["daily"], "last_all": sc["last_all"],
                         "running": dict(scanner.running), "providers": scanner.providers_ready()},
                "changes": posts.changes()}

    # ---- open postings: scanning the companies' sites ----
    def scan_ids(b: dict) -> list[str]:
        ids = b.get("companies")
        if isinstance(ids, list) and ids:
            return [hunt.company(str(x))["id"] for x in ids]
        return [c["id"] for c in hunt.companies()]

    @app.route("POST", "/api/hunt/scan")
    def scan(req: Request):
        """Scan all (or some) companies' sites in the background; `auto` (the window opening) only once a day,
        when the daily scan is on. Never starts Claude: what needs Claude is marked on the company."""
        b = body(req)
        if b.get("auto"):
            sc = posts.scans()
            stale = not sc["last_all"] or sc["last_all"] < time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 86400))
            if not sc["daily"] or not stale or not hunt.companies():
                return {"started": [], "skipped": True}
        ids = guard(lambda: scan_ids(b))
        scanner.start(ids, auto=bool(b.get("auto")))
        return {"started": ids}

    @app.route("PUT", "/api/hunt/scan-settings")
    def scan_settings(req: Request):
        b = body(req)
        return posts.settings(keywords=b.get("keywords"), daily=b.get("daily"))

    @app.route("POST", "/api/hunt/postings/{pid}/apply")
    def apply_posting(req: Request):
        a = guard(lambda: posts.apply(req.params["pid"]))
        return app_view(a["id"])

    @app.route("GET", "/api/hunt/logos/{cid}")
    def logo(req: Request):
        path = guard(lambda: hunt.logo_path(req.params["cid"]))
        if path is None:
            raise HttpError(404, "no logo")
        return FileResponse(path)

    @app.route("PUT", "/api/hunt/settings")
    def save_settings(req: Request):
        v = str(body(req).get("tracker") or "")
        if v not in TRACKERS:
            raise HttpError(400, t("Unknown tracker “{value}”", value=v))
        prefs.save(tracker=v)
        app.bus.publish("hunt", {})
        return {"tracker": tracker()}

    @app.route("POST", "/api/hunt/companies")
    def new_company(req: Request):
        b = body(req)
        if b.get("subset"):
            subset_to_send(studio, str(b["subset"]))
        c = guard(lambda: hunt.add_company(b))
        want_logos(c["id"])
        return company_view(c["id"])

    @app.route("GET", "/api/hunt/companies/{cid}")
    def get_company(req: Request):
        return guard(lambda: company_view(req.params["cid"]))

    @app.route("PUT", "/api/hunt/companies/{cid}")
    def edit_company(req: Request):
        b = body(req)
        if b.get("subset"):
            subset_to_send(studio, str(b["subset"]))
        want_logos(guard(lambda: hunt.update_company(req.params["cid"], b))["id"])
        return guard(lambda: company_view(req.params["cid"]))

    @app.route("DELETE", "/api/hunt/companies/{cid}")
    def delete_company(req: Request):
        cid = guard(lambda: hunt.company(req.params["cid"]))["id"]
        posts.drop_company(cid)
        guard(lambda: hunt.delete_company(cid))
        return {"ok": True}

    @app.route("POST", "/api/hunt/companies/{cid}/roles")
    def new_role(req: Request):
        a = guard(lambda: hunt.add_application(req.params["cid"], body(req)))
        return app_view(a["id"])

    @app.route("POST", "/api/hunt/companies/{cid}/sent")
    def company_sent(req: Request):
        """The sign-up on the company's own site went out: every role there not sent yet gets the version."""
        r = guard(lambda: send_company(req.params["cid"], "window"))
        return {**r, **company_view(req.params["cid"]), **studio.state()}

    @app.route("GET", "/api/hunt/apps/{aid}")
    def get_app(req: Request):
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("PUT", "/api/hunt/apps/{aid}")
    def edit_app(req: Request):
        guard(lambda: hunt.update_application(req.params["aid"], body(req)))
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("POST", "/api/hunt/apps/{aid}/status")
    def app_status(req: Request):
        b = body(req)
        guard(lambda: hunt.set_status(req.params["aid"], b.get("status"), note=str(b.get("note") or ""), on=str(b.get("on") or "")))
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("POST", "/api/hunt/apps/{aid}/note")
    def app_note(req: Request):
        b = body(req)
        guard(lambda: hunt.add_note(req.params["aid"], str(b.get("note") or ""), on=str(b.get("on") or "")))
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("POST", "/api/hunt/apps/{aid}/sent")
    def app_sent(req: Request):
        b = body(req)
        r = guard(lambda: send_app(req.params["aid"], str(b.get("subset") or "") or None, "window"))
        return {**r, **app_view(req.params["aid"]), **studio.state()}

    @app.route("DELETE", "/api/hunt/apps/{aid}")
    def delete_app(req: Request):
        guard(lambda: hunt.delete_application(req.params["aid"]))
        return {"ok": True}

    # ---- scripts/hunt.py: one command, an answer in words for Claude --------------
    @app.route("POST", "/api/hunt/do")
    def do(req: Request):
        b = body(req)
        cmd = str(b.get("cmd") or "")
        a = b.get("args") or {}
        a = a if isinstance(a, dict) else {}
        src = "chat"

        def run() -> Any:
            if cmd == "list":
                return {"list": hunt.table(), "status": a.get("status"), "company": a.get("company")}
            if cmd == "show":
                ref = str(a.get("ref") or "")
                if "/" in ref or (ref[:1] == "A" and ref[1:].isdigit()):
                    return {"app": app_view(ref)}
                return {"company": company_view(ref)}
            if cmd == "add-company":
                if a.get("subset"):
                    subset_to_send(studio, str(a["subset"]))
                cid = hunt.add_company(a)["id"]
                want_logos(cid)
                return {"company": company_view(cid), "text": "added"}
            if cmd == "edit-company":
                ref = str(a.pop("ref", "") or "")
                if a.get("subset"):
                    subset_to_send(studio, str(a["subset"]))
                cid = hunt.update_company(ref, a, source=src)["id"]
                want_logos(cid)
                return {"company": company_view(cid), "text": "changed"}
            if cmd == "add-role":
                ref = str(a.pop("company", "") or "")
                return {"app": app_view(hunt.add_application(ref, a, source=src)["id"]), "text": "added"}
            if cmd == "edit-role":
                ref = str(a.pop("ref", "") or "")
                return {"app": app_view(hunt.update_application(ref, a)["id"]), "text": "changed"}
            if cmd == "status":
                x = hunt.set_status(str(a.get("ref") or ""), a.get("status"), note=str(a.get("note") or ""), source=src,
                                    on=str(a.get("on") or ""))
                return {"app": app_view(x["id"]), "text": "status"}
            if cmd == "note":
                x = hunt.application(str(a.get("ref") or ""))
                hunt.add_note(x["id"], str(a.get("note") or ""), source=src, on=str(a.get("on") or ""))
                return {"app": app_view(x["id"]), "text": "noted"}
            if cmd == "next":
                x = hunt.update_application(str(a.get("ref") or ""), {"next": a.get("next") or "", "due": a.get("due") or ""})
                return {"app": app_view(x["id"]), "text": "changed"}
            if cmd == "about":
                x = hunt.update_company(str(a.get("ref") or ""), {"about": a.get("about")}, source=src)
                return {"company": company_view(x["id"]), "text": "about"}
            if cmd == "fill":
                return fill(str(a.get("ref") or ""), a.get("fields") or {})
            if cmd == "scan":
                refs = a.get("companies") or []
                return {"scanned": scanner.scan_many(scan_ids({"companies": refs})), "text": "scanned"}
            if cmd == "scan-route":
                c = hunt.company(str(a.get("ref") or ""))
                r, errs = scanner.save_route(c["id"], a.get("route"))
                if errs:
                    raise HttpError(400, "the route has problems:\n- " + "\n- ".join(errs))
                res = scanner.scan(c["id"])
                return {"scanned": [res], "company": company_view(c["id"]), "text": "route saved and run"}
            if cmd == "postings":
                c = hunt.company(str(a.get("ref") or ""))
                res = posts.merge(c["id"], a.get("postings") or [], how="claude", complete=not a.get("partial"))
                posts.set_scan_state(c["id"], needs="")
                return {"scanned": [{"company": c["id"], "found": res["found"], "new": len(res["new"]), "closed": len(res["closed"]),
                                     "changed": len(res["changed"])}], "company": company_view(c["id"]), "text": "postings saved"}
            if cmd == "apply":
                x = posts.apply(str(a.get("ref") or ""), source=src)
                return {"app": app_view(x["id"]), "text": "added"}
            if cmd == "keywords":
                return {"settings": posts.settings(keywords=a.get("keywords")), "text": "keywords"}
            if cmd == "sent":
                ref = str(a.get("ref") or "")
                if "/" in ref or (ref[:1] == "A" and ref[1:].isdigit()):
                    r = send_app(ref, str(a.get("subset") or "") or None, src)
                else:
                    r = send_company(ref, src)
                return {"sent": r, "text": "sent"}
            raise HttpError(400, f"unknown command {cmd!r}; one of: list, show, add-company, edit-company, fill, add-role, "
                                 "edit-role, status, note, next, about, sent, scan, scan-route, postings, apply, keywords")

        out = guard(run)
        if cmd == "sent":
            app.bus.publish("hunt", {"versions": True})   # a version may have been kept: the window reloads its state
        return {**out, "states": STATE_IDS}

    return hunt
