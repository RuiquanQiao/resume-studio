"""The tracker's routes: the window's tables and pages, and one command endpoint for scripts/hunt.py."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.derived import Derived, code_key, stat_key
from core.i18n import t
from core.lost_text import HOW, first_lost, sample
from core.server import App, BytesResponse, FileResponse, HttpError, Request, guess_type
from core.watcher import SignatureWatcher
from jobs.base import JobTool, ToolError
from jobs.signups import Signups
from resume import model
from resume.langs import LANGS as RESUME_LANGS
from resume.skills import levels as skill_levels
from resume.service import Studio

from . import geo as place_list
from . import ics, logos, natures, reader, visas, writing
from .geo import Geo
from .history import History, describe
from .logos import fetch_logo, fetch_logo_in_browser
from .postings import held_back, tracks_of, Postings, same_link, stale
from .rank import Descriptions, fit
from . import skills as role_skills
from .scan import Scanner
from .store import LOGO_TYPES, STATE_IDS, Hunt, SameCompany
from .views import Views

TRACKERS = ["studio", "career-ops"]
# the chat's commands that change a company or an application (scan and apply name theirs by id or list)
CHANGES = {"edit-company", "fill", "about", "add-role", "edit-role", "status", "note", "book", "rebook", "unbook",
           "sent", "scan-route", "postings"}
# the ones kept in the history, each undoable (jobhunt/history.py); a scan's postings and a sent resume aren't
RECORDED = {"add-company", "edit-company", "fill", "about", "add-role", "edit-role", "status", "note", "book", "rebook", "unbook"}


def subset_to_send(studio: Studio, subset_id: str | None) -> str:
    doc = studio.store.peek()   # only read
    if not subset_id:
        raise HttpError(409, t("Pick the subset this goes out with first"))
    if subset_id == model.ALL_ID or model.get_item(doc, "subset", subset_id) is None:
        raise HttpError(404, t("Subset {id} doesn't exist", id=subset_id))
    return subset_id


def keep_version(studio: Studio, subset_id: str, where: str) -> int:
    """The version that goes out: what the subset says now, kept as one and marked sent, which numbers it (after
    going back to v1.1, it goes out as v1.1, not as a copy of it). The user never keeps or numbers one by hand."""
    r = studio.commit_subset(subset_id, t("Sent: {where}", where=where), sent=True)
    return int(r["made"]["n"])


def mark_sent_versions(studio: Studio, sends) -> None:
    """Versions that went out before a version knew it itself (2026-11-15): marked from the applications' records,
    so they get their numbers. Runs at start; marks only, never makes or removes a version."""
    by: dict[str, dict[int, str]] = {}
    for sub, n, at in sends:
        if sub and isinstance(n, int):
            by.setdefault(str(sub), {}).setdefault(n, str(at or ""))
    for sub, ns in by.items():
        try:
            studio.versions.mark_sent(sub, ns)
        except OSError:
            pass


def register(app: App, studio: Studio, tool: JobTool, prefs: Any, *, platforms: Callable[[], list[dict]],
             browser: Any = None) -> Hunt:
    hunt = Hunt(studio.store.state_dir)
    mark_sent_versions(studio, ((s.get("subset"), s.get("n"), s.get("at")) for a in hunt.applications() for s in a.get("sent") or []))
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
    posts = Postings(hunt, cache_dir=studio.store.cache_dir)

    def warm() -> None:
        """An older file written the short way once; then the postings parsed and every title matched against the
        tracks, so the window's first look at the table doesn't wait for 50,000 titles (3 s in 2026-11)."""
        # after the window is up and its first page has its answers: parsing holds Python's lock meanwhile, and
        # a fixed wait fell right while the window was being built once the start got quicker (2026-11-11)
        until = time.monotonic() + 30
        while time.monotonic() < until and not app.bus.listeners():
            time.sleep(0.1)
        time.sleep(1.5)
        try:
            posts.slim_file()
            posts.summary()
            if os.environ.get("STUDIO_WARM", "on") != "off":
                # what Applying shows next to the table: the latest changes, and the roles in the tracks (86,000 titles
                # took 10 s on the first look at either, with "Loading…" all along: 2026-11-13)
                posts.changes()
                matches()
                # the postings themselves, for a company's page: the counts above no longer need them
                posts._view()
        except Exception:  # noqa: BLE001 (a file being written: the first look reads it then)
            pass
    threading.Thread(target=warm, name="hunt-warm-postings", daemon=True).start()
    views = Views(hunt.dir)
    geo = Geo(hunt.dir, cache_dir=studio.store.cache_dir)
    scanner = Scanner(hunt, posts, tool, browser, lambda: app.bus.publish("hunt", {}))
    hunt.scanner = scanner   # the app (and tests) reach the scanner through the tracker
    descs = Descriptions(hunt, lambda: scanner.browser, lambda: app.bus.publish("hunt", {}))
    hunt.descriptions = descs
    # the roles whose descriptions were read before their skills were worked out get them, once, off the start
    def fill_skills() -> None:
        try:
            hunt.fill_skills([j.get("jd") or "" for j in descs.all().values()])
        except Exception:  # noqa: BLE001  (a file being written meanwhile: the next start does it)
            pass
    threading.Thread(target=fill_skills, daemon=True, name="hunt-fill-skills").start()
    # After a scan, the descriptions of the picked roles (in a track) of the companies it scanned are read in the
    # background and each is scored as it comes in (user, 2026-11-15: "能不让我烦神的就不让我烦神"). Only those
    # companies' roles: scanning one company never opens another's pages (user, 2026-11-07). The daily scan reads
    # them only when the browser is open already, as it reads routes.
    def read_after_scan(out: list[dict], auto: bool) -> int:
        if scanner.browser is None or (auto and not scanner.browser_open()):
            return 0
        ids = {r["company"] for r in out if not r.get("error")}
        return descs.start([p for p in to_read() if p["company"] in ids])

    def to_read() -> list[dict]:
        """Every matching role, newest first, but those whose application has its description already."""
        done = {a["id"] for a in hunt.applications() if a.get("jd")}
        return [p for p in posts.matched(limit=100000)["postings"] if p.get("app") not in done]
    scanner.after_run = read_after_scan
    history = History(hunt)
    hunt.history = history
    studio.on_subset_renamed.append(hunt.rename_subset)
    hunt.migrate()
    # the sign-ups the Applications tab kept before move in, once
    if not hunt.exists():
        old = Signups(studio.store.state_dir).all()
        if old:
            hunt.import_signups(old)
    threading.Thread(target=linker, name="hunt-places", daemon=True).start()
    to_link.set()   # what was written before: once the migrations above are done

    # The roles in the user's tracks (Matching roles) are worked out from every posting a scan kept (postings.json,
    # 29 MB for 229 companies), each company's nature, the tracks, the descriptions read and the resume subsets. The
    # result is kept, in memory and in the cache folder, with the state of everything it came from, and worked out
    # again only once one of those has changed: the first look after a start parsed 29 MB and took 11 s, every
    # look after it half a second (user, 2026-11-13: "难道你每次都扫一遍！难道不该存下来？").
    # the roles in the tracks, ranked: worked out once per change of what they come from, kept between starts
    matches_kept = Derived(studio.store.cache_dir / "matches.json")

    def matches_key() -> str:
        cs = hunt.companies()
        names = hashlib.blake2b(json.dumps([[c["id"], c["name"], c.get("nature") or ""] for c in cs], ensure_ascii=False)
                                .encode(), digest_size=12).hexdigest()
        return json.dumps([code_key(__name__, "jobhunt.postings", "jobhunt.rank", "jobhunt.natures", "resume.skills"), list(posts._sig()),
                           stat_key(hunt._file("jds.json")), stat_key(hunt._file("applications.json")),
                           stat_key(studio.store.path), posts.tracks(), names, time.strftime("%Y-%m-%d")],
                          ensure_ascii=False, sort_keys=True)

    def work_out_matches() -> dict:
        every = posts.matched(limit=100000)
        m = {**every, "postings": every["postings"][:600]}   # the newest 600 are listed; every one is counted
        jds = descs.all()
        doc, _ = studio.store.load()
        have: dict[str, str] = {}   # subset id -> what its resume says, in every resume language
        for tr in m["tracks"]:
            sid = tr.get("subset") or ""
            if sid and sid not in have and model.get_item(doc, "subset", sid) is not None:
                from jobs.fit import subset_text
                have[sid] = subset_text(doc, model.get_item(doc, "subset", sid))
        lex = hunt.lexicon()
        lv = skill_levels(doc)
        subset_of = {tr["id"]: tr.get("subset") for tr in m["tracks"]}
        apps = {a["id"]: a for a in hunt.applications()}
        for p in m["postings"]:
            j = jds.get(p["id"]) or {}
            a = apps.get(p.get("app") or "") or {}
            if not j.get("jd") and a.get("jd"):   # read when it was added (Want to apply): its fit comes from that
                j = {"jd": a["jd"]}
            p["jd"] = "read" if j.get("jd") else "failed" if j.get("error") else ""
            if not p.get("deadline") and j.get("deadline"):
                p["deadline"] = j["deadline"]
            best = None
            for tid in p["tracks"]:
                sid = subset_of.get(tid)
                f = fit(j["jd"], have[sid], lex, lv) if j.get("jd") and sid in have else None
                if f and (best is None or f["score"] > best["score"]):
                    best = {**f, "subset": sid}
            p["fit"] = best
        m["ranked"] = bool(have)
        # what the button reads: every matching role, listed or not, with no description from its page or its application
        app_jd = {a["id"] for a in apps.values() if a.get("jd")}
        m["unread"] = sum(1 for p in every["postings"] if p.get("app") not in app_jd
                          and not (jds.get(p["id"]) or {}).get("jd") and not (jds.get(p["id"]) or {}).get("error")
                          and re.match(r"https?://", p.get("url") or ""))
        return m

    def matches() -> dict:
        return matches_kept.get(matches_key(), work_out_matches)

    def every_place(tb: dict | None = None) -> list[dict]:
        """`tb`: the companies and applications already in hand (the overview), not read again."""
        apps, cs = (tb["applications"], tb["companies"]) if tb else (hunt.applications(), hunt.companies())
        return [p for x in apps + cs for p in x.get("places") or []] + [c["hq"] for c in cs if c.get("hq")]

    def located(places: list[dict] | None = None) -> dict:
        """Where every place written as text (none picked from the list) is, for the world maps:
        {area: {lat, lon, name, country} | None}."""
        return geo.lookup([p["area"] for p in (every_place() if places is None else places) if not p.get("id") and p.get("kind") != "remote"])

    def resume_langs(doc: Any = None) -> list[str]:
        """The languages the user's resume is written in (its own setting): what company research may be in."""
        doc = doc if doc is not None else studio.store.peek()
        return [str(x) for x in (doc.get("settings") or {}).get("languages") or ["en"]]

    def research(c: dict, apps: list[dict], langs: list[str]) -> dict:
        """{lang, why, place, rewrite}: the language this company's research is written in (jobhunt/writing.py),
        and the one it is in now when that is another."""
        r = writing.research_language(c, apps, langs)
        name = lambda code: (RESUME_LANGS.get(code) or {}).get("name") or code   # noqa: E731
        bad = writing.needs_rewrite(c, r)
        return {**r, "lang_name": name(r["lang"]), "place_name": place_list.english(r["place"]) if r["place"] else "",
                "rewrite": bad, "rewrite_name": name(bad) if bad else ""}

    def place_names(places: list[dict] | None = None) -> dict:
        """Every picked place: {id: {name, detail, lat, lon}}, in the interface's language."""
        return geo.places(every_place() if places is None else places)

    def co_on() -> bool:
        """career-ops is installed and turned on (Settings → Plugins): only then does it get rows."""
        return tool.installed() and (getattr(tool, "settings", dict)() or {}).get("on", True) is not False

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

    def logo_url(c: dict, files: dict[str, tuple[int, int]] | None = None) -> str:
        """Versioned by the picture itself, so a logo file changed from outside the app shows up too. `files`: the
        logos folder listed once (logo_files), for a whole table: a look at each file took 0.6 s for 5,000."""
        if not c.get("logo"):
            return ""
        if files is not None:
            st = files.get(c["logo"])
            return f"/api/hunt/logos/{c['id']}?v={st[0]}-{st[1]}" if st else ""
        try:
            st = (hunt.dir / "logos" / c["logo"]).stat()
        except OSError:
            return ""
        return f"/api/hunt/logos/{c['id']}?v={st.st_mtime_ns}-{st.st_size}"

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

    def lacks_logo(c: dict) -> bool:
        return not c.get("logo") and "logo" not in (c.get("kept") or []) and bool(logo_sites(c))

    def want_logos(*cids: str) -> None:
        def run() -> None:
            if not cids:
                # the pass over every company waits for the window: it is no hurry, and a window is what
                # looks at a logo before it is kept (inspect). Python work here also slows the window's start.
                until = time.monotonic() + hunt.logo_wait
                while time.monotonic() < until and not app.bus.listeners():
                    time.sleep(0.1)
            # one read of the companies picks those still without a logo; find_logo reads each again, fresh.
            # (It read the whole file once per company: 226 companies x 20 ms held up the window, 2026-11-11.)
            ids = list(cids) or [c["id"] for c in hunt.companies() if lacks_logo(c)]
            for i in ids:
                find_logo(i)
        threading.Thread(target=run, name="hunt-logos", daemon=True).start()

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
    hunt.logo_wait = 30.0   # seconds the startup pass waits for a window before it goes without one
    want_logos()   # the companies that have a site and no logo yet

    def logo_files() -> list[tuple[str, int, int]]:
        d = hunt.dir / "logos"
        if not d.is_dir():
            return []
        with os.scandir(d) as it:   # on Windows the listing carries each file's times and size: no call per file
            return sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in it)

    # a picture changed from outside the app (Claude, a file manager): the window shows it without a restart
    hunt.logo_watcher = SignatureWatcher(logo_files, hunt._changed)
    hunt.logo_watcher.start()

    def site_urls(ref: str) -> set[str]:
        try:
            return {s["url"] for s in scanner.sites_of(hunt.company(ref))}   # `site` (the form's) counts too
        except KeyError:
            return set()

    def sites_changed(cid: str, before: set[str]) -> None:
        """A company's careers sites changed, whoever changed them (the window, the AI's fill or edit, a new company):
        the new ones are checked and scanned at once, so its roles come in without another click on Scan; one no scan
        reads is marked for the AI to learn. One gone takes its warning with it."""
        now = [s["url"] for s in scanner.sites_of(hunt.company(cid))]
        added = [u for u in now if u not in before]
        if added or before - set(now):
            threading.Thread(target=scanner.check_sites, args=(cid, added), daemon=True, name="hunt-check-sites").start()

    def fill(ref: str, fields: dict) -> dict:
        """Claude's research on a company, every field at once, the logo downloaded into the data folder."""
        fields = fields if isinstance(fields, dict) else {}
        before = site_urls(ref)
        c, kept = hunt.fill(ref, fields)
        sites_changed(c["id"], before)
        r = research(c, hunt.applications(c["id"]), resume_langs())
        lang_error = (f"what you wrote is in {r['rewrite_name']}, but this company's research is written in {r['lang_name']} "
                      f"({r['lang']}): send industry, strength, size and about again, in {r['lang_name']}") if r["rewrite"] else ""
        logo_error = look = ""
        if fields.get("logo_url"):
            if "logo" in (c.get("kept") or []):
                kept = sorted({*kept, "logo"})
            else:
                try:
                    try:
                        data, ctype = fetch_logo(str(fields["logo_url"]))
                    except ValueError:
                        if scanner.browser is None:
                            raise
                        data, ctype = fetch_logo_in_browser(scanner.browser, str(fields["logo_url"]))
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

    # ---- what kind of employer, and whether the user's status lets them apply (jobhunt/natures.py) ----
    def work() -> dict:
        """The user's status in each country they look for work in: resume.yaml profile.work."""
        return natures.clean_work(((studio.store.peek() or {}).get("profile") or {}).get("work"))

    def employer(c: dict, w: dict) -> dict:
        """Its country (its nature's, else its headquarters') and what it asks that the user doesn't have."""
        hq = c.get("hq") or {}
        cc = natures.country(c.get("nature") or "") or (place_list.country(hq["id"]) if hq.get("id") else "")
        return {"country": cc, "country_name": (place_list.name("t:" + cc) or cc) if cc else "",
                "short_of": natures.short_of(c.get("nature") or "", cc, w)}

    def nature_catalog() -> dict:
        return {**natures.catalog(t, lambda cc: place_list.name("t:" + cc) or cc), "work": work()}

    @app.route("GET", "/api/hunt/countries")
    def countries(req: Request):
        """Every country, and the visas the profile's work records are picked from (jobhunt/visas.py)."""
        from core.i18n import current
        return {"countries": place_list.countries(), "visas": visas.catalog(current())}

    def company_view(ref: str) -> dict:
        c = hunt.company(ref)
        doc = studio.store.peek()   # only read
        apps = sorted(hunt.applications(c["id"]), key=lambda a: a.get("order") or 0)
        evs = [e for e in hunt.events() if e["app"] in {a["id"] for a in apps}]
        st = scanner.live_state(c)
        trs = posts.tracks()
        return {"company": {**c, **employer(c, work()), "logo_url": logo_url(c), "subset_label": subset_label(doc, c.get("subset") or ""),
                            "research": research(c, apps, resume_langs(doc)),
                            "subset_exists": bool(c.get("subset")) and model.get_item(doc, "subset", c["subset"]) is not None},
                "applications": apps, "events": evs,
                "postings": sorted(({**p, "stale": stale(p), "tracks": tracks_of(p["title"], trs, c.get("nature") or "")} for p in posts.all(c["id"])),
                                   key=lambda p: (bool(p.get("closed")), p.get("category") or "", p["title"])),
                "scan": {k: v for k, v in st.items() if k != "route"} | {"recorded": bool(st.get("route")),
                                                                       "running": scanner.running.get(c["id"]),
                                                                       "queued": c["id"] in scanner.queue,
                                                                       "sites": scanner.site_status(c)},
                "keywords": posts.scans()["keywords"], "tracks": posts.tracks(),
                # its nature not known yet: the tracks for some natures only can't say whether its roles are theirs
                "held_back": [tr["id"] for tr in held_back(trs, c.get("nature") or "")]}

    def with_track(a: dict, tracks: list[dict], nature: str) -> dict:
        """A role's track (the one the user put it in, else its title's) and, once its description is read, the
        track that description reads like when that is another one (jobhunt/skills.py, no AI)."""
        v = role_skills.view(a, tracks_of(a.get("role") or "", tracks, nature), tracks, nature)
        return {**v, **rule_fit(a, v["tracks"], tracks)}

    # a role's fit by the rules (jobhunt/rank.py): its description's skills against the resume it goes out with (its
    # own, else its track's). Worked out once per description, resume and data file change: the table paints it per row
    fits: dict[tuple, dict] = {}

    def rule_fit(a: dict, tids: list[str], tracks: list[dict]) -> dict:
        sid = a.get("subset") or next((tr.get("subset") for tr in tracks if tr["id"] in tids and tr.get("subset")), "")
        if not a.get("jd") or not sid:
            return {}
        key = (a["id"], hashlib.blake2b(a["jd"].encode(), digest_size=8).hexdigest(), sid, str(stat_key(studio.store.path)))
        if key not in fits:
            doc = studio.store.peek() or {}
            sub = model.get_item(doc, "subset", sid)
            f = None
            if sub is not None:
                from jobs.fit import subset_text
                f = fit(a["jd"], subset_text(doc, sub), hunt.lexicon(), skill_levels(doc))
            if len(fits) > 2000:
                fits.clear()
            fits[key] = {"fit": {**f, "subset": sid}} if f else {}
        return fits[key]

    def app_view(ref: str) -> dict:
        a = hunt.application(ref)
        c = hunt.company(a["company"])
        a = {**a, **with_track(a, posts.tracks(), c.get("nature") or "")}
        r = reading.get(a["id"]) or {}
        if r.get("error") and (a.get("jd") or "") != r.get("jd", ""):   # the JD came in since (the AI, the user): the failure is over
            reading.pop(a["id"], None)
            r = {}
        return {"application": a, "company": c, "events": hunt.events(a["id"]), "reading": r}

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
            try:
                jd = hunt.application(aid).get("jd") or ""
            except Exception:  # noqa: BLE001 - deleted meanwhile
                jd = ""
            reading[aid] = {"error": str(e), "jd": jd}   # the JD it failed on: a new one ends the failure (app_view)
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
    def copy_to_tracker(apps: list[dict], c: dict, label: str, n: str) -> int:
        if tracker() != "career-ops" or not co_on():
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
        name = studio.versions.name(sub, n)
        out = [hunt.mark_sent(a["id"], sub, n, source=source, name=name) for a in todo]
        doc = studio.store.peek()   # only read
        copied = guard(lambda: copy_to_tracker(out, c, subset_label(doc, sub), name))
        app.bus.publish("hunt", {"versions": True})   # a version was kept: every window reloads its state
        return {"n": n, "name": name, "subset": sub, "sent": [a["id"] for a in out], "copied": copied}

    def send_app(ref: str, subset: str | None, source: str, version: int | None = None) -> dict:
        """`version`: an older version the user says went out (sent before it was marked here); none: what the
        subset says now, kept as a version if it isn't one."""
        a = hunt.application(ref)
        c = hunt.company(a["company"])
        sub = subset_to_send(studio, subset or a.get("subset") or c.get("subset") or None)
        if version:
            if not studio.versions.get(sub, int(version)):
                raise HttpError(404, t("{subset} has no v{n}", subset=subset_label(studio.store.peek(), sub), n=studio.versions.name(sub, version)))
            n = int(version)
        else:
            n = keep_version(studio, sub, f"{c['name']} — {a['role']}")
        if subset and subset != (a.get("subset") or c.get("subset")):
            hunt.update_application(a["id"], {"subset": subset})   # what it went out with is what it goes out with
        name = studio.versions.name(sub, n)
        a = hunt.mark_sent(a["id"], sub, n, source=source, name=name)
        doc = studio.store.peek()   # only read
        copied = guard(lambda: copy_to_tracker([a], c, subset_label(doc, sub), name))
        app.bus.publish("hunt", {"versions": True})
        return {"n": n, "name": name, "subset": sub, "sent": [a["id"]], "copied": copied}

    # ---- the window ------------------------------------------------------------
    @app.route("GET", "/api/hunt")
    def overview(req: Request):
        # each file read once for the whole table, never once per company (5,000 companies: 5.6 s, 2026-11-13)
        tb = hunt.table()
        summ = posts.summary()
        langs = resume_langs()
        w = work()
        logos = {name: (ns, size) for name, ns, size in logo_files()}
        scans = posts.scans()
        apps_of: dict[str, list[dict]] = {}
        for a in tb["applications"]:
            apps_of.setdefault(a["company"], []).append(a)
        for c in tb["companies"]:
            c["logo_url"] = logo_url(c, logos)
            c.update(employer(c, w))
            c["research"] = research(c, apps_of.get(c["id"]) or [], langs)
            c["postings"] = summ.get(c["id"]) or {"open": 0, "matching": 0}
            st = scanner.live_state(c, scans)
            c["postings"]["needs"] = st.get("needs") or ""
            c["postings"]["unrouted"] = st.get("unrouted") or []
            c["postings"]["tried"] = st["tried"]
        sc = scans
        places = every_place(tb)
        nature_of = {c["id"]: c.get("nature") or "" for c in tb["companies"]}
        tb["applications"] = [{**a, **with_track(a, sc["tracks"], nature_of.get(a["company"], ""))} for a in tb["applications"]]
        return {**tb, "platforms": platforms(), "tracker": tracker(), "career_ops": co_on(),
                "scan": {"keywords": sc["keywords"], "tracks": sc["tracks"], "daily": sc["daily"], "last_all": sc["last_all"],
                         "running": dict(scanner.running), "providers": scanner.providers_ready(),
                         "last_run": scanner.last_run, "queued": list(scanner.queue), "reading": descs.status(), "read_last": descs.last,
                         "batch": dict(scanner.batch) if scanner.batch else None},
                "changes": posts.changes(), "geo": located(places), "places": place_names(places), "natures": nature_catalog()}

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
            due = not sc["last_all"] or sc["last_all"] < time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 86400))
            if not sc["daily"] or not due or not hunt.companies():
                return {"started": [], "skipped": True}
        ids = guard(lambda: scan_ids(b))
        scanner.start(ids, auto=bool(b.get("auto")))
        return {"started": ids}

    @app.route("GET", "/api/hunt/matches")
    def matches_view(req: Request):
        """Every open role, of every company, in one of the user's tracks: the roles picked for them (user,
        2026-11-04: "scanned, but nothing picked out the roles I'd want"). Worked out once per change (matches)."""
        return {**matches(), "reading": descs.status()}

    @app.route("POST", "/api/hunt/matches/read")
    def read_matches(req: Request):
        """Read the description of every picked role that has none yet, in the background (jobhunt/rank.py)."""
        if scanner.browser is None:
            raise HttpError(409, t("Resume Studio's browser isn't available here"))
        # newest first: stopped early, the newest roles have their scores
        return {"started": descs.start(to_read(), limit=None), "reading": descs.status()}

    @app.route("POST", "/api/hunt/matches/stop")
    def stop_reading(req: Request):
        descs.stop()
        app.bus.publish("hunt", {})
        return {"reading": descs.status()}

    @app.route("PUT", "/api/hunt/scan-settings")
    def scan_settings(req: Request):
        b = body(req)
        if b.get("tracks") is None and b.get("keywords") is None:
            return guard(lambda: posts.settings(daily=b.get("daily")))
        return set_tracks(lambda: posts.settings(keywords=b.get("keywords"), daily=b.get("daily"), tracks=b.get("tracks")))

    def set_tracks(save: Callable[[], Any]) -> Any:
        """Save a change of the tracks; the roles it takes in have their descriptions read and scored, as after a
        scan. The ones it kept need nothing: a score is worked out from the description, the tracks and the resume
        as they are now (matches), so changing a resume or a skill level scores every role again by itself."""
        before = {p["id"] for p in posts.matched(limit=100000)["postings"]}
        out = guard(save)
        if scanner.browser is not None:
            descs.start([p for p in to_read() if p["id"] not in before])
        return out

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
                ctype = guess_type(p.name) or ""
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
        sites_changed(c["id"], set())   # a careers site given with the name: its roles come in now
        return company_view(c["id"])

    # ---- what the AI changed, each undoable (jobhunt/history.py) ------------------------
    @app.route("GET", "/api/hunt/history")
    def get_history(req: Request):
        target = (req.query.get("target") or [""])[0]
        return {"history": history.entries(target=target)[:300]}

    @app.route("POST", "/api/hunt/history/{hid}/undo")
    def undo_change(req: Request):
        return guard(lambda: history.undo(req.params["hid"], by="window"))

    @app.route("POST", "/api/hunt/companies/{cid}/learn-tried")
    def learn_tried(req: Request):
        """The window's check after "Let AI learn": these sites are still unreadable though the AI tried."""
        urls = [str(u) for u in body(req).get("sites") or [] if u]
        guard(lambda: scanner.learn_tried(req.params["cid"], urls))
        return company_view(req.params["cid"])

    # systems still read by the AI's routes, for the user to report to the developer (jobhunt/report.py)
    @app.route("GET", "/api/hunt/report")
    def get_report(req: Request):
        return scanner.report_offers()

    @app.route("POST", "/api/hunt/report")
    def done_report(req: Request):
        b = body(req)
        how = str(b.get("how") or "")
        if how not in ("issue", "copied", "ignored"):
            raise HttpError(400, "how: issue, copied or ignored")
        scanner.mark_reported([str(k) for k in b.get("keys") or [] if k], how)
        return scanner.report_offers()

    @app.route("GET", "/api/hunt/companies/{cid}")
    def get_company(req: Request):
        return guard(lambda: company_view(req.params["cid"]))

    @app.route("PUT", "/api/hunt/companies/{cid}")
    def edit_company(req: Request):
        b = body(req)
        if b.get("subset"):
            subset_to_send(studio, str(b["subset"]))
        before = site_urls(guard(lambda: hunt.company(req.params["cid"]))["id"])
        c = guard(lambda: hunt.update_company(req.params["cid"], b))
        want_logos(c["id"])
        sites_changed(c["id"], before)
        return guard(lambda: company_view(req.params["cid"]))

    @app.route("DELETE", "/api/hunt/companies/{cid}")
    def delete_company(req: Request):
        cid = guard(lambda: hunt.company(req.params["cid"]))["id"]
        posts.drop_company(cid)
        guard(lambda: hunt.delete_company(cid))
        return {"ok": True}

    def app_at(url: str) -> dict | None:
        """The role already on the list at this link (links compared as a scan compares them)."""
        k = same_link(url)
        if not re.match(r"https?://", k, re.I):
            return None
        return next((a for a in hunt.applications() if a.get("url") and same_link(a["url"]) == k), None)

    def add_role(company: str, fields: dict, source: str) -> dict:
        a = hunt.add_application(company, fields, source=source)
        posts.link(a)
        return read_if_new(a)

    @app.route("POST", "/api/hunt/companies/{cid}/roles")
    def new_role(req: Request):
        a = guard(lambda: add_role(req.params["cid"], body(req), "window"))
        return app_view(a["id"])

    @app.route("POST", "/api/hunt/link")
    def from_link(req: Request):
        """A posting's link, before the AI reads it: a role on the list already, or a posting a scan found (it becomes
        a role the user wants to apply to, no AI needed), or neither ({found: null})."""
        url = str(body(req).get("url") or "").strip()
        a = app_at(url)
        if a:
            return {"found": "app", **app_view(a["id"])}
        p = posts.by_url(url)
        if p:
            a = read_if_new(guard(lambda: posts.apply(p["id"])))
            return {"found": "posting", **app_view(a["id"])}
        return {"found": None}

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
            posts.link(a)
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
        doc = studio.store.peek()   # only read
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
        lost = first_lost(a)
        if lost is not None:   # an agent's shell turned its Chinese into "?" on the way: keep nothing of it
            raise HttpError(400, f"nothing was saved: {sample(lost)!r} came with its non-ASCII letters (Chinese and "
                                 f"the like) turned into \"?\". {HOW}")
        src = "chat"
        if cmd in CHANGES:   # what a chat changes it names exactly: a part of a name may be another company's
            for key in ("ref", "company"):
                v = str(a.get(key) or "").strip()
                if v and not (v[:1] == "P" and v[1:].isdigit()):   # P12: a posting, by its id
                    find = hunt.application if "/" in v or (v[:1] == "A" and v[1:].isdigit()) else hunt.company
                    a[key] = guard(lambda: find(v, exact=True)["id"])
        chat = str(b.get("chat") or "")
        target = str(a.get("ref") or "") if cmd in RECORDED and cmd not in ("add-company", "add-role") else ""
        kind = "application" if target[:1] == "A" else "company"
        before = history.snapshot(kind, target) if target else None

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
                sites_changed(cid, set())
                return {"company": company_view(cid), "text": "added"}
            if cmd == "edit-company":
                ref = str(a.pop("ref", "") or "")
                if a.get("subset"):
                    subset_to_send(studio, str(a["subset"]))
                was = site_urls(ref)
                cid = hunt.update_company(ref, a, source=src)["id"]
                want_logos(cid)
                sites_changed(cid, was)
                return {"company": company_view(cid), "text": "changed"}
            if cmd == "add-role":
                ref = str(a.pop("company", "") or "")
                same = app_at(str(a.get("url") or ""))
                if same:   # a link is one posting: never a second role for it
                    return {"app": app_view(same["id"]), "text": "already on the list"}
                return {"app": app_view(add_role(ref, a, src)["id"]), "text": "added"}
            if cmd == "edit-role":
                ref = str(a.pop("ref", "") or "")
                x = hunt.update_application(ref, a)
                if "url" in a:
                    posts.link(x)
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
                # one being scanned already (the window starts a scan once a company's sites are in) is waited for:
                # the AI gets its result, never "still being scanned" to end its turn on
                out = [scanner.wait_scan(x["company"]) if x.get("busy") else x for x in scanner.scan_many(scan_ids({"companies": refs}))]
                return {"scanned": out, "text": "scanned"}
            if cmd == "scan-route":
                c = hunt.company(str(a.get("ref") or ""))
                r, errs = scanner.save_route(c["id"], a.get("route"))
                if errs:
                    raise HttpError(400, "the route has problems:\n- " + "\n- ".join(errs))
                res = scanner.scan(c["id"], just_tried=True)
                if res.get("busy"):   # a scan of it was running: its end is waited for, then the new route is run
                    scanner.wait_scan(c["id"])
                    res = scanner.scan(c["id"], just_tried=True)
                # a route that never says the site's count can't be checked: told, so the AI adds it
                if "found" in res and not any(re.search(r"\btotal\b", p.get("script") or "") for p in r.get("pages") or []):
                    res["no_total"] = True
                # the AI sees one site; the code sees every route: one calling the API of others is likely one system
                res["kin"] = scanner.kin(c["id"])
                return {"scanned": [res], "company": company_view(c["id"]), "text": "route saved and run"}
            if cmd == "systems":
                return {"systems": scanner.shared_systems(), "text": "systems"}
            if cmd == "postings":
                c = hunt.company(str(a.get("ref") or ""))
                res = posts.merge(c["id"], a.get("postings") or [], how="claude", complete=not a.get("partial"))
                # postings read by hand don't teach the scan anything: a site with no route still needs one
                missing = scanner.unrouted(scanner.plan(c))
                needs = "claude" if missing else ""
                posts.set_scan_state(c["id"], needs=needs, unrouted=missing)
                return {"scanned": [{"company": c["id"], "found": res["found"], "new": len(res["new"]), "closed": len(res["closed"]),
                                     "changed": len(res["changed"]), "needs": needs}], "company": company_view(c["id"]), "text": "postings saved"}
            if cmd == "apply":
                x = read_if_new(posts.apply(str(a.get("ref") or ""), source=src))
                return {"app": app_view(x["id"]), "text": "added"}
            if cmd == "keywords":
                return {"settings": posts.settings(keywords=a.get("keywords")), "text": "keywords"}
            if cmd == "show-tracks":
                return {"settings": {"tracks": posts.tracks()}, "text": "tracks"}
            if cmd == "tracks":
                return {"settings": set_tracks(lambda: posts.settings(tracks=a.get("tracks"))), "text": "tracks"}
            if cmd == "sent":
                ref = str(a.get("ref") or "")
                if "/" in ref or (ref[:1] == "A" and ref[1:].isdigit()):
                    r = send_app(ref, str(a.get("subset") or "") or None, src)
                else:
                    r = send_company(ref, src)
                return {"sent": r, "text": "sent"}
            if cmd == "history":
                ref = str(a.get("ref") or "").strip()
                rid = "" if not ref else (hunt.application(ref) if "/" in ref or (ref[:1] == "A" and ref[1:].isdigit())
                                          else hunt.company(ref))["id"]
                return {"lines": [describe(e) for e in history.entries(target=rid)[:int(a.get("limit") or 30)]], "text": "history"}
            if cmd == "undo":
                return {**history.undo(str(a.get("change") or ""), by="chat"), "text": "undone"}
            raise HttpError(400, f"unknown command {cmd!r}; one of: list, show, add-company, edit-company, fill, add-role, "
                                 "edit-role, status, note, book, rebook, unbook, ics, about, sent, scan, scan-route, systems, postings, "
                                 "apply, keywords, tracks, history, undo")

        out = guard(run)
        if cmd in RECORDED:
            made = out.get("app") or out.get("company") or {}
            rec = made.get("application") or made.get("company") or {}
            rid = target or (rec.get("id") if out.get("text") == "added" else "") or ""   # not one already on the list
            if rid:
                k = "application" if rid[:1] == "A" else "company"
                e = history.record(k, rid, before, history.snapshot(k, rid), cmd=cmd, chat=chat)
                if e:
                    out["change"] = e["id"]
        if cmd == "sent":
            app.bus.publish("hunt", {"versions": True})   # a version may have been kept: the window reloads its state
        return {**out, "states": STATE_IDS}

    return hunt
