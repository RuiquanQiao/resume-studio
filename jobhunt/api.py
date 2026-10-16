"""The tracker's routes: the window's tables and pages, and one command endpoint for scripts/hunt.py."""
from __future__ import annotations

from typing import Any, Callable

from core.i18n import t
from core.server import App, HttpError, Request
from jobs.base import JobTool, ToolError
from jobs.signups import Signups
from resume import model
from resume.service import Studio

from .store import STATE_IDS, Hunt

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


def register(app: App, studio: Studio, tool: JobTool, prefs: Any, *, platforms: Callable[[], list[dict]]) -> Hunt:
    hunt = Hunt(studio.store.state_dir)
    hunt.on_change = lambda: app.bus.publish("hunt", {})
    studio.on_subset_renamed.append(hunt.rename_subset)
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

    def company_view(ref: str) -> dict:
        c = hunt.company(ref)
        doc, _ = studio.store.load()
        apps = sorted(hunt.applications(c["id"]), key=lambda a: a.get("order") or 0)
        evs = [e for e in hunt.events() if e["app"] in {a["id"] for a in apps}]
        return {"company": {**c, "subset_label": subset_label(doc, c.get("subset") or ""),
                            "subset_exists": bool(c.get("subset")) and model.get_item(doc, "subset", c["subset"]) is not None},
                "applications": apps, "events": evs}

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
        return {**hunt.table(), "platforms": platforms(), "tracker": tracker(), "career_ops": tool.installed()}

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
        return company_view(c["id"])

    @app.route("GET", "/api/hunt/companies/{cid}")
    def get_company(req: Request):
        return guard(lambda: company_view(req.params["cid"]))

    @app.route("PUT", "/api/hunt/companies/{cid}")
    def edit_company(req: Request):
        b = body(req)
        if b.get("subset"):
            subset_to_send(studio, str(b["subset"]))
        guard(lambda: hunt.update_company(req.params["cid"], b))
        return guard(lambda: company_view(req.params["cid"]))

    @app.route("DELETE", "/api/hunt/companies/{cid}")
    def delete_company(req: Request):
        guard(lambda: hunt.delete_company(req.params["cid"]))
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
                return {"company": company_view(hunt.add_company(a)["id"]), "text": "added"}
            if cmd == "edit-company":
                ref = str(a.pop("ref", "") or "")
                if a.get("subset"):
                    subset_to_send(studio, str(a["subset"]))
                return {"company": company_view(hunt.update_company(ref, a)["id"]), "text": "changed"}
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
                x = hunt.update_company(str(a.get("ref") or ""), {"about": a.get("about")})
                return {"company": company_view(x["id"]), "text": "about"}
            if cmd == "sent":
                ref = str(a.get("ref") or "")
                if "/" in ref or (ref[:1] == "A" and ref[1:].isdigit()):
                    r = send_app(ref, str(a.get("subset") or "") or None, src)
                else:
                    r = send_company(ref, src)
                return {"sent": r, "text": "sent"}
            raise HttpError(400, f"unknown command {cmd!r}; one of: list, show, add-company, edit-company, add-role, "
                                 "edit-role, status, note, next, about, sent")

        out = guard(run)
        if cmd == "sent":
            app.bus.publish("hunt", {"versions": True})   # a version may have been kept: the window reloads its state
        return {**out, "states": STATE_IDS}

    return hunt
