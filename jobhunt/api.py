"""The tracker's routes: the window's tables and pages, and one command endpoint for scripts/hunt.py."""
from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.i18n import t
from core.server import App, BytesResponse, FileResponse, HttpError, Request
from core.watcher import SignatureWatcher
from jobs.base import JobTool, ToolError
from jobs.signups import Signups
from resume import model
from resume.langs import LANGS as RESUME_LANGS
from resume.service import Studio

from . import geo as place_list
from . import ics, logos, reader, writing
from .geo import Geo
from .logos import fetch_logo
from .postings import Postings
from .scan import Scanner
from .store import LOGO_TYPES, STATE_IDS, Hunt, SameCompany
from .views import Views

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
    # a city written as text (the list doesn't know it) is looked up on OpenStreetMap in the background, after
    # every change: one at a time, each name once (jobhunt/geo.py); a hit repaints the window by itself
    to_link = threading.Event()

    def linker() -> None:
        while True:
            to_link.wait()
            to_link.clear()
            try:
                hunt.link_places(place_list.look_up)
            except Exception:  # noqa: BLE001 (the network, a file being written: the next change tries again)
                pass

    def changed() -> None:
        app.bus.publish("hunt", {})
        to_link.set()

    hunt.on_change = changed
    posts = Postings(hunt)
    views = Views(hunt.dir)
    geo = Geo(hunt.dir)
    scanner = Scanner(hunt, posts, tool, browser, lambda: app.bus.publish("hunt", {}))
    hunt.scanner = scanner   # the app (and tests) reach the scanner through the tracker
    studio.on_subset_renamed.append(hunt.rename_subset)
    hunt.migrate()
    # the sign-ups the Applications tab kept before move in, once
    if not hunt.exists():
        old = Signups(studio.store.state_dir).all()
        if old:
            hunt.import_signups(old)
    threading.Thread(target=linker, name="hunt-places", daemon=True).start()
    to_link.set()   # what was written before: once the migrations above are done

    def every_place() -> list[dict]:
        return ([p for x in hunt.applications() + hunt.companies() for p in x.get("places") or []]
                + [c["hq"] for c in hunt.companies() if c.get("hq")])

    def located() -> dict:
        """Where every place written as text (none picked from the list) is, for the world maps:
        {area: {lat, lon, name, country} | None}."""
        return geo.lookup([p["area"] for p in every_place() if not p.get("id") and p.get("kind") != "remote"])

    def resume_langs(doc: Any = None) -> list[str]:
        """The languages the user's resume is written in (its own setting): what company research may be in."""
        doc = doc if doc is not None else studio.store.load()[0]
        return [str(x) for x in (doc.get("settings") or {}).get("languages") or ["en"]]

    def research(c: dict, apps: list[dict], langs: list[str]) -> dict:
        """{lang, why, place, rewrite}: the language this company's research is written in (jobhunt/writing.py),
        and the one it is in now when that is another."""
        r = writing.research_language(c, apps, langs)
        name = lambda code: (RESUME_LANGS.get(code) or {}).get("name") or code   # noqa: E731
        bad = writing.needs_rewrite(c, r)
        return {**r, "lang_name": name(r["lang"]), "place_name": place_list.english(r["place"]) if r["place"] else "",
                "rewrite": bad, "rewrite_name": name(bad) if bad else ""}

    def place_names() -> dict:
        """Every picked place: {id: {name, detail, lat, lon}}, in the interface's language."""
        return geo.places(every_place())

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
        """Versioned by the picture itself, so a logo file changed from outside the app shows up too."""
        try:
            st = (hunt.dir / "logos" / c["logo"]).stat() if c.get("logo") else None
        except OSError:
            st = None
        return f"/api/hunt/logos/{c['id']}?v={st.st_mtime_ns}-{st.st_size}" if st else ""

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
        got = finder(list(sites), lambda data, ctype: logos.verdict(inspect(data, ctype), data, ctype))
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

    hunt.find_logo = (lambda sites, check: logos.from_sites(sites, types=LOGO_TYPES, check=check)) \
        if os.environ.get("STUDIO_LOGOS") != "off" else None

    # ---- a logo is looked at before it is kept (user report, 2026-10-23: logos that came in blank) ----
    # The window draws the picture and says what shows; the drawing, on white, is also what Claude looks at.
    checks: dict[str, dict] = {}
    checks_lock = threading.Lock()

    def inspect(data: bytes, ctype: str, *, timeout: float = 8.0) -> dict | None:
        """{width, height, visible, png} as the window drew it, or None with no window open (or no answer)."""
        if not app.bus.listeners():
            return None
        token = os.urandom(8).hex()
        job = {"data": data, "type": ctype.split(";")[0].strip(), "done": threading.Event(), "stats": None}
        with checks_lock:
            checks[token] = job
        try:
            app.bus.publish("logo-check", {"token": token, "url": f"/api/hunt/logo-check/{token}"})
            job["done"].wait(timeout)
            return job["stats"]
        finally:
            with checks_lock:
                checks.pop(token, None)

    @app.route("GET", "/api/hunt/logo-check/{token}")
    def logo_check_picture(req: Request):
        job = checks.get(req.params["token"])
        if not job:
            raise HttpError(404, "no such check")
        return BytesResponse(job["data"], job["type"])

    @app.route("POST", "/api/hunt/logo-check/{token}")
    def logo_check_result(req: Request):
        job = checks.get(req.params["token"])
        if job and not job["done"].is_set():   # the first window to answer
            job["stats"] = body(req)
            job["done"].set()
        return {"ok": True}

    look_dir = studio.store.cache_dir / "logo-look"

    def write_look(cid: str, stats: dict | None) -> str:
        """The window's drawing of a company's logo, as a png for Claude to look at. Claude leaves it; the ones
        older than an hour go here, so Claude never has to delete (a delete asks the user every time)."""
        png = (stats or {}).get("png") or ""
        if not png.startswith("data:image/png;base64,"):
            return ""
        look_dir.mkdir(parents=True, exist_ok=True)
        for old in look_dir.glob("*.png"):   # left behind by an earlier look: an hour is long enough
            try:
                if time.time() - old.stat().st_mtime > 3600:
                    old.unlink()
            except OSError:
                pass
        p = look_dir / f"{cid}.png"
        p.write_bytes(base64.b64decode(png.split(",", 1)[1]))
        return str(p)
    hunt.want_logos = want_logos
    want_logos()   # the companies that have a site and no logo yet

    def logo_files() -> list[tuple[str, int, int]]:
        d = hunt.dir / "logos"
        return sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in d.iterdir()) if d.is_dir() else []

    # a picture changed from outside the app (Claude, a file manager): the window shows it without a restart
    hunt.logo_watcher = SignatureWatcher(logo_files, hunt._changed)
    hunt.logo_watcher.start()

    def fill(ref: str, fields: dict) -> dict:
        """Claude's research on a company, every field at once, the logo downloaded into the data folder."""
        fields = fields if isinstance(fields, dict) else {}
        c, kept = hunt.fill(ref, fields)
        r = research(c, hunt.applications(c["id"]), resume_langs())
        lang_error = (f"what you wrote is in {r['rewrite_name']}, but this company's research is written in {r['lang_name']} "
                      f"({r['lang']}): send industry, strength, size and about again, in {r['lang_name']}") if r["rewrite"] else ""
        logo_error = look = ""
        if fields.get("logo_url"):
            if "logo" in (c.get("kept") or []):
                kept = sorted({*kept, "logo"})
            else:
                try:
                    data, ctype = fetch_logo(str(fields["logo_url"]))
                    stats = inspect(data, ctype)
                    why = logos.verdict(stats, data, ctype)
                    if why:   # not kept: the logo it had (if any) stays
                        raise ValueError(t("Not saved, {why}. Find another picture of its logo.", why=why))
                    hunt.save_logo(c["id"], data, ctype)
                    look = write_look(c["id"], stats)
                except ValueError as e:
                    logo_error = str(e)
        want_logos(c["id"])   # no logo from Claude (or it failed): the site's own icon
        return {"company": company_view(c["id"]), "kept": kept, "logo_error": logo_error, "logo_look": look,
                "lang_error": lang_error, "text": "filled"}

    def company_view(ref: str) -> dict:
        c = hunt.company(ref)
        doc, _ = studio.store.load()
        apps = sorted(hunt.applications(c["id"]), key=lambda a: a.get("order") or 0)
        evs = [e for e in hunt.events() if e["app"] in {a["id"] for a in apps}]
        st = posts.scan_state(c["id"])
        return {"company": {**c, "logo_url": logo_url(c), "subset_label": subset_label(doc, c.get("subset") or ""),
                            "research": research(c, apps, resume_langs(doc)),
                            "subset_exists": bool(c.get("subset")) and model.get_item(doc, "subset", c["subset"]) is not None},
                "applications": apps, "events": evs,
                "postings": sorted(posts.all(c["id"]), key=lambda p: (bool(p.get("closed")), p.get("category") or "", p["title"])),
                "scan": {k: v for k, v in st.items() if k != "route"} | {"recorded": bool(st.get("route")),
                                                                       "running": scanner.running.get(c["id"]),
                                                                       "queued": c["id"] in scanner.queue},
                "keywords": posts.scans()["keywords"]}

    def app_view(ref: str) -> dict:
        a = hunt.application(ref)
        c = hunt.company(a["company"])
        return {"application": a, "company": c, "events": hunt.events(a["id"]), "reading": reading.get(a["id"]) or {}}

    # ---- reading a role's own page: its description, where and until when (jobhunt/reader.py), no Claude ----
    reading: dict[str, dict] = {}   # application id -> {"running": True} while it reads, {"error": ...} after it failed

    def read_app(aid: str, replace: bool) -> None:
        """Fill the role from its link. The description is replaced only when asked (`replace`) or when there is
        none; the location and the apply-by date are filled only where the role has none yet."""
        try:
            a = hunt.application(aid)
            if scanner.browser is None:   # the scanner's: the same browser, and the one tests swap
                raise RuntimeError(t("Resume Studio's browser isn't available here"))
            got = reader.read_posting(scanner.browser, a.get("url") or "")
            if not got["jd"]:
                raise RuntimeError(t("No job description was found on its page"))
            a = hunt.application(aid)   # it may have changed while the page was read
            fields: dict = {"jd_read": time.strftime("%Y-%m-%d")}
            if replace or not a.get("jd"):
                fields["jd"] = got["jd"]
            if got.get("places") and not a.get("places"):
                fields["places"] = got["places"]
            ev = next((e for e in a.get("schedule") or [] if e.get("kind") == "apply"), None)
            if got.get("deadline") and ev is not None and not ev.get("at"):
                fields["apply_by"] = got["deadline"]
            hunt.update_application(aid, fields)
            reading.pop(aid, None)
        except Exception as e:  # noqa: BLE001  (the browser, the page, a role deleted meanwhile)
            reading[aid] = {"error": str(e)}
        app.bus.publish("hunt", {})

    def start_read(aid: str, replace: bool = False) -> None:
        a = hunt.application(aid)
        if not re.match(r"https?://", a.get("url") or ""):
            raise ValueError(t("Give the role its link first"))
        if (reading.get(a["id"]) or {}).get("running"):
            return
        reading[a["id"]] = {"running": True}
        app.bus.publish("hunt", {})
        threading.Thread(target=read_app, args=(a["id"], replace), daemon=True, name=f"hunt-read-{a['id']}").start()

    def read_if_new(a: dict) -> dict:
        """A role that has a link and no description yet gets it from its page, whoever gave it the link."""
        if not a.get("jd") and re.match(r"https?://", a.get("url") or ""):
            start_read(a["id"])
        return a

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

    def send_app(ref: str, subset: str | None, source: str, version: int | None = None) -> dict:
        """`version`: an older version the user says went out (sent before it was marked here); none: what the
        subset says now, kept as a version if it isn't one."""
        a = hunt.application(ref)
        c = hunt.company(a["company"])
        sub = subset_to_send(studio, subset or a.get("subset") or c.get("subset") or None)
        if version:
            if not studio.versions.get(sub, int(version)):
                raise HttpError(404, t("{subset} has no v{n}", subset=subset_label(studio.store.load()[0], sub), n=version))
            n = int(version)
        else:
            n = keep_version(studio, sub, f"{c['name']} — {a['role']}")
        if subset and subset != (a.get("subset") or c.get("subset")):
            hunt.update_application(a["id"], {"subset": subset})   # what it went out with is what it goes out with
        a = hunt.mark_sent(a["id"], sub, n, source=source)
        doc, _ = studio.store.load()
        copied = guard(lambda: copy_to_tracker([a], c, subset_label(doc, sub), n))
        return {"n": n, "subset": sub, "sent": [a["id"]], "copied": copied}

    # ---- the window ------------------------------------------------------------
    @app.route("GET", "/api/hunt")
    def overview(req: Request):
        tb = hunt.table()
        summ = posts.summary()
        langs = resume_langs()
        for c in tb["companies"]:
            c["logo_url"] = logo_url(c)
            c["research"] = research(c, [a for a in tb["applications"] if a["company"] == c["id"]], langs)
            c["postings"] = summ.get(c["id"]) or {"open": 0, "matching": 0}
            c["postings"]["needs"] = posts.scan_state(c["id"]).get("needs") or ""
        sc = posts.scans()
        return {**tb, "platforms": platforms(), "tracker": tracker(), "career_ops": tool.installed(),
                "scan": {"keywords": sc["keywords"], "daily": sc["daily"], "last_all": sc["last_all"],
                         "running": dict(scanner.running), "providers": scanner.providers_ready(),
                         "last_run": scanner.last_run, "queued": list(scanner.queue),
                         "batch": dict(scanner.batch) if scanner.batch else None},
                "changes": posts.changes(), "geo": located(), "places": place_names()}

    @app.route("GET", "/api/hunt/places")
    def find_places(req: Request):
        """What a typed start finds in the list of places (?q=syd&kind=onsite|hybrid|remote), in the interface's language."""
        q = (req.query.get("q") or [""])[0]
        kind = (req.query.get("kind") or ["onsite"])[0]
        return {"places": place_list.search(q, kind=kind)}

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
        a = read_if_new(guard(lambda: posts.apply(req.params["pid"])))   # its description comes in from its page
        return app_view(a["id"])

    @app.route("POST", "/api/hunt/apps/{aid}/read")
    def read_role(req: Request):
        guard(lambda: start_read(req.params["aid"], replace=bool(body(req).get("replace", True))))
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("PUT", "/api/hunt/geo")
    def put_geo(req: Request):
        """Pin a place where the user put it on the map ({area, lat, lon}), or let it go ({area, lat: null})."""
        b = body(req)
        where = guard(lambda: geo.pin(str(b.get("area") or ""), b.get("lat"), b.get("lon")))
        app.bus.publish("hunt", {})
        return where

    @app.route("GET", "/api/hunt/views")
    def get_views(req: Request):
        return views.all()

    @app.route("PUT", "/api/hunt/views/{table}")
    def put_views(req: Request):
        """A table's views, as the window arranged them: the other windows (and Claude) see the same."""
        tb = guard(lambda: views.save(req.params["table"], body(req)))
        app.bus.publish("hunt", {"views": True})
        return tb

    @app.route("GET", "/api/hunt/logos/{cid}")
    def logo(req: Request):
        path = guard(lambda: hunt.logo_path(req.params["cid"]))
        if path is None:
            raise HttpError(404, "no logo")
        return FileResponse(path)

    @app.route("PUT", "/api/hunt/logos/{cid}")
    def put_logo(req: Request):
        """The user's own picture for the logo: a file they picked ({path}) or its bytes ({data: base64, type}).
        It stays theirs."""
        b = body(req)
        ctype = str(b.get("type") or "")
        try:
            if b.get("path"):
                p = Path(str(b["path"]))
                ctype = mimetypes.guess_type(p.name)[0] or ""
                if not ctype.startswith("image/") or not p.is_file() or p.stat().st_size > 2_000_000:
                    raise ValueError
                data = p.read_bytes()
            else:
                data = base64.b64decode(str(b.get("data") or ""), validate=True)
        except (binascii.Error, ValueError, OSError):
            raise HttpError(400, t("That isn't a picture Resume Studio can show ({type})", type=ctype or "?"))
        guard(lambda: hunt.save_logo(req.params["cid"], data, ctype, source="window"))
        return guard(lambda: company_view(req.params["cid"]))

    def look_at(ref: str) -> dict:
        """Claude looks at a company's logo as the window shows it: a png drawing of it, and what is wrong with it."""
        c = hunt.company(ref)
        path = hunt.logo_path(c["id"])
        if path is None:
            return {"company": c["id"], "logo": False, "look": "", "checked": False, "problem": t("it has no logo")}
        ctype = next((k for k, v in LOGO_TYPES.items() if v == path.suffix[1:]), "")
        data = path.read_bytes()
        stats = inspect(data, ctype)
        return {"company": c["id"], "logo": True, "file": str(path), "look": write_look(c["id"], stats),
                "checked": stats is not None, "problem": logos.verdict(stats, data, ctype)}

    @app.route("POST", "/api/hunt/logos/{cid}/look")
    def look_logo(req: Request):
        return guard(lambda: look_at(req.params["cid"]))

    @app.route("DELETE", "/api/hunt/logos/{cid}")
    def delete_logo(req: Request):
        guard(lambda: hunt.remove_logo(req.params["cid"]))
        return guard(lambda: company_view(req.params["cid"]))

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
        def add() -> dict:
            try:
                return hunt.add_company(b)
            except SameCompany as e:   # the window asks which: the one on the list, or a new one
                brief = lambda x: {"id": x["id"], "name": x["name"], "aliases": x.get("aliases") or [], "created": x.get("created") or ""}
                raise HttpError(409, str(e), {"same": e.same and brief(e.same), "close": [brief(x) for x in e.close]})
        c = guard(add)
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
        a = read_if_new(guard(lambda: hunt.add_application(req.params["cid"], body(req))))
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
        b = body(req)
        a = guard(lambda: hunt.update_application(req.params["aid"], b))
        if "url" in b:
            read_if_new(a)
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
        try:
            version = int(b.get("n") or 0) or None
        except (TypeError, ValueError):
            raise HttpError(400, t("The version must be a number"))
        r = guard(lambda: send_app(req.params["aid"], str(b.get("subset") or "") or None, "window", version))
        return {**r, **app_view(req.params["aid"]), **studio.state()}

    # what is booked for a role: a test, 一面, 二面…
    @app.route("POST", "/api/hunt/apps/{aid}/schedule")
    def add_booked(req: Request):
        guard(lambda: hunt.add_booked(req.params["aid"], body(req)))
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("PUT", "/api/hunt/apps/{aid}/schedule/{eid}")
    def edit_booked(req: Request):
        guard(lambda: hunt.update_booked(req.params["aid"], req.params["eid"], body(req)))
        return guard(lambda: app_view(req.params["aid"]))

    @app.route("DELETE", "/api/hunt/apps/{aid}/schedule/{eid}")
    def delete_booked(req: Request):
        guard(lambda: hunt.delete_booked(req.params["aid"], req.params["eid"]))
        return guard(lambda: app_view(req.params["aid"]))

    def export_ics(app_ref: str = "", eid: str = "") -> str:
        """The calendar file, in the export folder: every application's, or one role's, or one booked thing."""
        apps = hunt.applications()
        name = "applying"
        if app_ref:
            a = hunt.application(app_ref)
            apps = [{**a, "schedule": [e for e in a.get("schedule") or [] if not eid or e["id"] == eid]}]
            if eid and not apps[0]["schedule"]:
                raise KeyError(t("{role} has nothing booked called “{ref}”", role=a["role"], ref=eid))
            name = f"applying-{a['id']}" + (f"-{eid}" if eid else "")
        doc, _ = studio.store.load()
        out = studio.data_dir / str((doc.get("settings") or {}).get("export_dir") or "exports") / f"{name}.ics"
        out.parent.mkdir(parents=True, exist_ok=True)
        names = {c["id"]: c["name"] for c in hunt.companies()}
        out.write_text(ics.calendar(apps, names), encoding="utf-8", newline="")
        return str(out)

    @app.route("POST", "/api/hunt/ics")
    def make_ics(req: Request):
        """{app?, event?, open?}: written to the export folder; `open` hands it to the computer's calendar app."""
        b = body(req)
        path = guard(lambda: export_ics(str(b.get("app") or ""), str(b.get("event") or "")))
        if b.get("open") and os.name == "nt" and not os.environ.get("STUDIO_NO_SHELL"):
            os.startfile(path)   # noqa: S606 - our own file, in the data folder
        return {"path": path}

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
                return {"app": app_view(read_if_new(hunt.add_application(ref, a, source=src))["id"]), "text": "added"}
            if cmd == "edit-role":
                ref = str(a.pop("ref", "") or "")
                x = hunt.update_application(ref, a)
                return {"app": app_view((read_if_new(x) if "url" in a else x)["id"]), "text": "changed"}
            if cmd == "status":
                x = hunt.set_status(str(a.get("ref") or ""), a.get("status"), note=str(a.get("note") or ""), source=src,
                                    on=str(a.get("on") or ""))
                return {"app": app_view(x["id"]), "text": "status"}
            if cmd == "note":
                x = hunt.application(str(a.get("ref") or ""))
                hunt.add_note(x["id"], str(a.get("note") or ""), source=src, on=str(a.get("on") or ""))
                return {"app": app_view(x["id"]), "text": "noted"}
            if cmd == "book":
                x = hunt.application(str(a.pop("ref", "") or ""))
                hunt.add_booked(x["id"], a, source=src)
                return {"app": app_view(x["id"]), "text": "booked"}
            if cmd == "rebook":
                x = hunt.application(str(a.pop("ref", "") or ""))
                hunt.update_booked(x["id"], str(a.pop("event", "") or ""), a, source=src)
                return {"app": app_view(x["id"]), "text": "changed"}
            if cmd == "unbook":
                x = hunt.application(str(a.get("ref") or ""))
                hunt.delete_booked(x["id"], str(a.get("event") or ""))
                return {"app": app_view(x["id"]), "text": "removed"}
            if cmd == "pin":
                where = geo.pin(str(a.get("area") or ""), a.get("lat"), a.get("lon"))
                app.bus.publish("hunt", {})
                return {"where": where, "text": "pinned" if a.get("lat") is not None else "unpinned"}
            if cmd == "where":
                return {"geo": located(), "places": place_names(), "text": "where"}
            if cmd == "ics":
                return {"path": export_ics(str(a.get("ref") or "")), "text": "exported"}
            if cmd == "about":
                x = hunt.update_company(str(a.get("ref") or ""), {"about": a.get("about")}, source=src)
                return {"company": company_view(x["id"]), "text": "about"}
            if cmd == "fill":
                return fill(str(a.get("ref") or ""), a.get("fields") or {})
            if cmd == "logo":
                return {**look_at(str(a.get("ref") or "")), "text": "looked"}
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
                x = read_if_new(posts.apply(str(a.get("ref") or ""), source=src))
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
                                 "edit-role, status, note, book, rebook, unbook, ics, about, sent, scan, scan-route, postings, apply, keywords")

        out = guard(run)
        if cmd == "sent":
            app.bus.publish("hunt", {"versions": True})   # a version may have been kept: the window reloads its state
        return {**out, "states": STATE_IDS}

    return hunt
