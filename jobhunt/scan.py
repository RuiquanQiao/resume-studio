"""Scanning companies' careers sites for open postings (docs/JOB_HUNT.md, 2026-10-18).

Each site is read the cheapest way that works, never by Claude once it has been set up:

1. **career-ops' providers**, kept in this repository (`jobs/vendor/career-ops/`, MIT, its LICENSE beside them;
   `scripts/update_career_ops.py` brings in a newer copy): Moka, Feishu (ByteDance), Tencent, Meituan, Alibaba,
   Greenhouse, Lever, Ashby, Workday and some 150 more read a site's public job API with no browser and no AI.
   They need only Node.js, never a career-ops install (2026-11-12: career-ops is to be removed).
2. **A recorded route**, for any other site (Xinghe's own site, a site behind a sign-in): Claude finds the job list
   once in Resume Studio's browser and records read-only page scripts, as for profiles (autofill/route.py);
   scans replay them with no Claude:

       {"pages": [{"url": "https://careers.example.com/campus/jobs",
                   "script": "async (h) => ({postings: [...]})"}]}

   A script returns `{"postings": [{title, url, location, category, batch, deadline, posted}]}` (or
   `{"signed_out": true}` when the page asks to sign in). It may call the site's own JSON API with `fetch`
   (same origin, the user's sign-in) to page through every posting.
3. Neither yet: the site **needs Claude** to record its route; the window offers that (an automatic daily
   scan never starts Claude).

北森 (jobhunt/beisen.py), Feishu on any domain (jobhunt/feishu.py), HotJob (jobhunt/hotjob.py), 阿里's talent
sites (jobhunt/alitalent.py) and 国聘's company pages (jobhunt/iguopin.py) are read by the scan itself, career-ops or not. Routes of several companies that call one API are found by jobhunt/systems.py, so a
system the AI keeps learning one site at a time gets a built-in scan of its own.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from core.browser import BrowserError, PageNotOpened
from core.cdp import CdpError
from core.i18n import t

from . import alitalent, beisen, feishu, hotjob, iguopin, patience, report, systems
from .postings import Postings
from .store import Hunt, site_host, site_key

BRIDGE = Path(__file__).resolve().parent.parent / "jobs" / "co_scan.mjs"
PROVIDERS = BRIDGE.parent / "vendor" / "career-ops"   # career-ops' providers, copied in (MIT)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# the job APIs the scan reads itself, whatever the site: a route calling one predates that scan
BUILT_IN_APIS = {beisen.API.lower(), feishu.API.lower(), hotjob.API.lower().rstrip("/"),
                 alitalent.API.lower(), alitalent.BATCHES.lower(), iguopin.API.lower(), iguopin.INFO.lower()}
# career-ops' providers (jobs/co_scan.mjs): a request's wait and tries as a near site gets them (patience.py adds
# for a far one), and the pages a provider reads of one list at most
BRIDGE_TIMEOUT = 20
BRIDGE_TRIES = 2
BRIDGE_WAITS = (3,)
CO_MAX_PAGES = 40
# the providers for systems that run in mainland China, whatever the company's own domain (careers.tencent.com)
CN_PROVIDERS = {"tencent", "meituan", "alibaba", "mokahr", "feishu-jobs", "bytedance", "zhiye", "hotjob"}
KNOWN = "providers.json"   # hunt/providers.json  {"<list address>": "beisen"}: the systems found reading each
MAX_PAGES = 20   # of a recorded route, each with 2 minutes
# a scan has no one waiting on it, and a user abroad reads sites on the far side of the world (user, 2026-11-02):
# a page gets 90 s to open, and two more tries after a pause, before it counts as unread
OPEN_TIMEOUT = 90
RETRY_WAITS = (5, 15)
CHECK_WAIT = 20 * 60   # longest a check of new sites waits for a scan of the same company to end
EMPTY_WAIT = 8         # a page that read no roles is read once more after this: its list may still be loading


def validate_route(data: Any) -> tuple[dict, list[str]]:
    errs: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list) or not data["pages"]:
        return {}, ['the route must be {"pages": [{"url", "script"}, ...]}']
    if len(data["pages"]) > MAX_PAGES:   # said, never cut: the pages past the limit would just go unread
        errs.append(f"a route has at most {MAX_PAGES} pages; this one has {len(data['pages'])}: read more postings per page")
    pages = []
    for n, p in enumerate(data["pages"][:MAX_PAGES]):
        if not isinstance(p, dict):
            errs.append(f"pages[{n}] must be an object")
            continue
        url, script = str(p.get("url") or ""), str(p.get("script") or "")
        if not url.startswith(("http://", "https://")):
            errs.append(f"pages[{n}].url must be an http(s) address")
        if "=>" not in script and "function" not in script:
            errs.append(f"pages[{n}].script must be a function: async (h) => ({{postings: [...]}})")
        pages.append({"url": url, "script": script})
    return ({"pages": pages} if not errs else {}), errs



def run_summary(out: list[dict], auto: bool) -> dict:
    """What a run did, for its toast: a company stopped before reading anything (its site needs the AI, or none is
    known) is counted apart, so "0 new" never stands for a site nobody could read."""
    unread = [r for r in out if not r.get("error") and "found" not in r]
    return {"at": time.time(), "auto": auto, "companies": len(out),
            "read": sum(1 for r in out if "found" in r),
            "new": sum(int(r.get("new") or 0) for r in out),
            "closed": sum(int(r.get("closed") or 0) for r in out),
            "failed": sum(1 for r in out if r.get("error")),
            # counted by site, never by company: a company whose API site reads and whose own site doesn't is one
            # site the AI has to learn, named, not "a company" (user, 2026-11-03)
            "unrouted": [u for r in out if not r.get("error") for u in r.get("unrouted") or []],
            "no_site": sum(1 for r in unread if r.get("needs") == "sites")}

def same_once(found: list[dict]) -> list[dict]:
    """A role's link is its key. One exception: a board with several sections (Feishu's /index/position/<id> and
    /xinghe/position/<id>) lists one role under each, so two links that differ only in their first path segment,
    with the same title, are one role read twice: kept once. Anything else that differs is another role (2026-11-04:
    an earlier rule guessed role ids from long numbers in the link, took a company number for one, and dropped 67)."""
    def key(p: dict):
        try:
            u = urlsplit(str(p.get("url") or "").strip())
        except ValueError:
            return None
        segs = [x for x in u.path.split("/") if x]
        if len(segs) < 3:
            return None
        return (u.netloc.lower(), "/".join(segs[1:]), u.query, u.fragment, str(p.get("title") or "").strip().casefold())
    seen, out = set(), []
    for p in found:
        k = key(p)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(p)
    return out


def short_of(rows: list[dict], totals: dict[str, int]) -> list[int] | None:
    """[read, the site says] when a route read fewer different roles than its site says it lists, else None.
    The check that doesn't trust the script, or the agent that wrote it: a site (2026-11-04) said 448 and 381 came in,
    and only the agent's own last line said so. A role counts once (same_once, then by its link or title + place).
    A few short (a role taken down between one page and the next) is no gap: 2% or 2 roles, whichever is more."""
    if not totals:
        return None
    want = sum(totals.values())
    from .postings import clean, fingerprint
    got = len({fingerprint(x) for x in (clean(p) for p in same_once(rows)) if x})
    return [got, want] if want - got > max(2, want // 50) else None


class RouteFailed(Exception):
    def __init__(self, message: str, signed_out: bool = False):
        super().__init__(message)
        self.signed_out = signed_out


class Scanner:
    def __init__(self, hunt: Hunt, postings: Postings, tool: Any, browser: Any, publish: Callable[[], None]):
        self.hunt, self.postings, self.tool, self.browser, self.publish = hunt, postings, tool, browser, publish
        self.running: dict[str, str] = {}      # company id -> what is reading it now
        self.started: dict[str, float] = {}    # company id -> when that reading began
        self.lock = threading.Lock()
        self._providers: dict[str, str | None] = {}   # url -> career-ops provider id (or None), per run of the app
        self.providers_dir = PROVIDERS
        self.last_run: dict = {}               # the last scan_many: {at, auto, companies, new, closed, failed}
        # the run started from the window (start): the companies still waiting, and how far it got, so the window
        # can say "3 of 12" from the click to the end, between one company and the next too
        self.queue: list[str] = []
        self.batch: dict | None = None         # {total, done, auto} while a run goes on
        self.retry_waits = RETRY_WAITS         # seconds before each new try of a page that didn't open
        self.empty_wait = EMPTY_WAIT           # seconds before a page whose script read no roles is read again
        # 北森 sites, read by the scan itself (jobhunt/beisen.py); tests hand in their own
        self.beisen_detect: Callable[[str], bool] = beisen.detect
        self.beisen_read: Callable[[str], dict] = beisen.read
        # Feishu tenants on any domain (jobhunt/feishu.py): career-ops' provider knows only *.jobs.feishu.cn
        self.feishu_detect: Callable[[str], bool | None] = feishu.detect
        self.feishu_read: Callable[[str], dict] = feishu.read
        self._not_beisen: set[str] = set()     # asked once per run of the app
        self._not_feishu: set[str] = set()
        # HotJob portals on any domain (jobhunt/hotjob.py): an address under a portal id, `/SU<24 hex>/`
        self.hotjob_detect: Callable[[str], bool | None] = hotjob.detect
        self.hotjob_read: Callable[[str], dict] = hotjob.read
        self._not_hotjob: set[str] = set()
        # 国聘's exclusive company pages (jobhunt/iguopin.py): `<x>.iguopin.com`, told by the address alone
        self.iguopin_read: Callable[[str], dict] = iguopin.read
        # 阿里's talent sites (jobhunt/alitalent.py): a company's talent.<x> domain, or its part of the group's campus site
        self.alitalent_detect: Callable[[str], bool | None] = alitalent.detect
        self.alitalent_read: Callable[[str], dict] = alitalent.read
        self._not_alitalent: set[str] = set()
        # asked, and the site didn't answer: nobody knows yet whether a system reads it. Never "the AI has to learn
        # it" (join.huolala.cn after every restart, 2026-11-11): said as "didn't connect", asked again next scan
        self._unreached: set[str] = set()
        # how long to wait for each site, by how far it is and how it answered before, kept across restarts
        patience.use(hunt.dir / "net.json")
        # the systems found reading each address, kept across restarts: a slow site found on 北森 once stays on it
        try:
            known = json.loads(self.hunt._load(KNOWN) or "{}")
        except ValueError:
            known = {}
        self._providers.update({u: p for u, p in known.items() if isinstance(p, str) and p})
        self._fresh: dict[str, dict] = {}      # company id -> what a route just tried (try_route) read, for its scan

    # ---- which sites, read how ----
    @staticmethod
    def sites_of(c: dict) -> list[dict]:
        sites = list(c.get("sites") or [])
        if c.get("site") and not any(s["url"] == c["site"] for s in sites):
            sites.insert(0, {"url": c["site"], "kind": "", "system": ""})
        return sites

    @staticmethod
    def list_of(s: dict) -> str:
        """Where a site's job list is read: its `list` (the Feishu or Moka board its own page fronts), else itself."""
        return s.get("list") or s["url"]

    @staticmethod
    def covers(route: dict | None, url: str) -> bool:
        """Whether a recorded route reads this site. A route names the sites it was recorded for; one recorded before
        it did (2026-11-03) reads the sites its pages are on. A site added after the route was recorded isn't read by
        it: the route is the company's, but a new address is a list nobody has looked at yet."""
        if not route:
            return False
        if isinstance(route.get("sites"), list):
            return site_key(url) in {site_key(u) for u in route["sites"]}
        return site_host(url) in {site_host(p.get("url") or "") for p in route.get("pages") or []}

    def providers_ready(self) -> bool:
        # STUDIO_PROVIDERS=off: tests' made-up sites are never asked of the copied providers (each ask starts node);
        # a test that wants them hands in its own folder (providers_dir), or asks the copy itself
        if self.providers_dir == PROVIDERS and os.environ.get("STUDIO_PROVIDERS") == "off":
            return False
        return (Path(self.providers_dir) / "providers" / "_registry.mjs").is_file() and bool(shutil.which("node"))

    def _bridge(self, entries: list[dict], detect: bool = False) -> list[dict]:
        if not entries or not self.providers_ready():
            return []
        # each site's wait (jobhunt/patience.py), never the providers' own 10 s: careers.tencent.com's pages take 9 s
        # from Australia, and one that took 11 stopped the read at 192 of about 2,000 roles (2026-11-14)
        waits = {}
        for x in entries:
            p = patience.of(x["url"], BRIDGE_TIMEOUT, BRIDGE_TRIES, BRIDGE_WAITS,
                            cn=self._providers.get(x["url"]) in CN_PROVIDERS)
            waits[x["url"]] = p
            x.update({"timeout_ms": int(p.timeout * 1000), "tries": p.tries, "waits_ms": [int(w * 1000) for w in p.waits]})
        # the whole run: every site read to the provider's page cap at its own wait, never a fixed 10 minutes
        limit = 60 if detect else 120 + sum(p.timeout * (CO_MAX_PAGES + 1) for p in waits.values())
        tmp = self.hunt.dir / f".scan-{threading.get_ident()}.json"
        self.hunt.dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
        try:
            proc = subprocess.run([shutil.which("node") or "node", str(BRIDGE), str(self.providers_dir), str(tmp),
                                   *(["--detect"] if detect else [])], cwd=str(BRIDGE.parent), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=limit, creationflags=_NO_WINDOW)
        except Exception as e:  # noqa: BLE001  (node missing, too slow)
            return [{"key": x["key"], "url": x["url"], "provider": None, "error": str(e)} for x in entries]
        finally:
            tmp.unlink(missing_ok=True)
        for line in reversed((proc.stdout or "").strip().splitlines()):
            if line.startswith("{"):
                try:
                    results = json.loads(line).get("results") or []
                except ValueError:
                    break
                for r in results:   # how each site answered, for its next wait
                    if r.get("took"):
                        patience.took(r["url"], float(r["took"]))
                    if r.get("missed"):
                        patience.missed(r["url"])
                return results
        err = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no answer"]
        return [{"key": x["key"], "url": x["url"], "provider": None, "error": err[0][:300]} for x in entries]

    def plan(self, c: dict) -> list[dict]:
        """Each site of the company with how it is read: provider id, "route" or "claude"."""
        sites = self.sites_of(c)
        self.detect([self.list_of(s) for s in sites], c["name"])
        route = self.postings.route(c["id"])
        out = []
        for s in sites:
            prov = self._providers.get(self.list_of(s))
            # "unreached": asked whether a system reads it and the site didn't answer: read next scan, never the AI's
            out.append({**s, "how": prov or ("route" if self.covers(route, s["url"])
                                             else "unreached" if self.list_of(s) in self._unreached else "claude")})
        return out

    def detect(self, urls: list[str], name: str = "") -> None:
        """Which provider reads each address (None: none does), asked once per address and kept."""
        had = {u: p for u, p in self._providers.items() if p}
        silent: set[str] = set()   # a built-in reader asked and no answer came: nobody knows yet
        unknown = list(dict.fromkeys(u for u in urls if u not in self._providers))
        for r in self._bridge([{"key": u, "name": name, "url": u} for u in unknown], detect=True):
            if not r.get("error"):
                self._providers[r["url"]] = r.get("provider")
        # 国聘 first: its company pages are told by their address, so none of the checks below ever asks one
        for u in dict.fromkeys(urls):
            if not self._providers.get(u) and iguopin.detect(u):
                self._providers[u] = iguopin.PROVIDER
        # HotJob next: only an address under a portal id is asked, so the others' checks (a page fetched for
        # 北森's marks, a POST for Feishu's list) never go to a HotJob site
        for u in dict.fromkeys(urls):
            if self._providers.get(u) or u in self._not_hotjob:
                continue
            got = self.hotjob_detect(u)
            if got:
                self._providers[u] = hotjob.PROVIDER
            elif got is False:
                self._not_hotjob.add(u)
            else:
                silent.add(u)
        # 北森 is read by the scan itself (jobhunt/beisen.py), career-ops or not: asked of what career-ops left
        # every address, not only the new ones: career-ops keeps "none reads it" (None) for an address, and one 北森
        # couldn't be asked about (the site didn't answer) is asked again
        for u in dict.fromkeys(urls):
            if self._providers.get(u) or u in self._not_beisen:
                continue
            got = self.beisen_detect(u)
            if got:
                self._providers[u] = beisen.PROVIDER
            elif got is False:   # None: the site didn't answer; asked again at the next scan
                self._not_beisen.add(u)
            else:
                silent.add(u)
        # then Feishu under the company's own domain (小米集团's xiaomi.jobs.f.mioffice.cn, 2026-11-09), the same way
        # one portal of a site found on Feishu makes them all: its API is the site's, whichever portal is asked
        # (小米's campus and social portals didn't answer the check in 30 s from Australia, its intern one did)
        on_feishu = lambda u: any(feishu.origin(k) == feishu.origin(u) for k, v in self._providers.items() if v == feishu.PROVIDER)
        for u in dict.fromkeys(urls):
            if self._providers.get(u) or u in self._not_feishu:
                continue
            got = True if on_feishu(u) else self.feishu_detect(u)
            if got:
                self._providers[u] = feishu.PROVIDER
            elif got is False:
                self._not_feishu.add(u)
            else:
                silent.add(u)
        for u in dict.fromkeys(urls):   # the ones asked before a later portal of their site answered
            if not self._providers.get(u) and u not in self._not_feishu and on_feishu(u):
                self._providers[u] = feishu.PROVIDER
        # last, 阿里's talent sites: asked of what nothing else reads (one POST, as for Feishu)
        for u in dict.fromkeys(urls):
            if self._providers.get(u) or u in self._not_alitalent:
                continue
            got = self.alitalent_detect(u)
            if got:
                self._providers[u] = alitalent.PROVIDER
                silent.discard(u)
            elif got is False:
                self._not_alitalent.add(u)
            else:
                silent.add(u)
        for u in dict.fromkeys(urls):
            if self._providers.get(u) or u not in silent:
                self._unreached.discard(u)
            else:
                self._unreached.add(u)
        now = {u: p for u, p in self._providers.items() if p}
        if now != had:   # kept for the next run of the app: what was found, never "none" (that is asked again)
            with self.hunt.lock:
                try:
                    known = json.loads(self.hunt._load(KNOWN) or "{}")
                except ValueError:
                    known = {}
                self.hunt._write(KNOWN, {**known, **now})

    def live_state(self, c: dict, scans: dict | None = None) -> dict:
        """The company's scan state as its sites are now: a site that can't be scanned is warned about only while it
        is still one of the company's sites (the AI or the user may have replaced it since the last scan), and a
        company whose unscannable sites are all gone no longer needs the AI."""
        st = dict(self.postings.scan_state(c["id"], scans))
        was = st.get("unrouted") or []
        if was:
            now = {s["url"] for s in self.sites_of(c)}
            st["unrouted"] = [u for u in was if u in now]
            if not st["unrouted"] and st.get("needs") == "claude":
                st["needs"] = ""
        # `tried`: when the AI was asked to learn a site and the scan still couldn't read it after: only while it can't
        st["tried"] = {u: at for u, at in (st.get("tried") or {}).items() if u in (st.get("unrouted") or [])}
        if st.get("missed"):   # only the sites the company still has
            now = {s["url"] for s in self.sites_of(c)}
            st["missed"] = {u: w for u, w in st["missed"].items() if u in now} or None
        return st

    def learn_tried(self, ref: str, urls: list[str]) -> dict[str, str]:
        """The AI was asked to learn these sites and the scan still can't read them: kept, so the window says "the AI
        tried" instead of "the AI has to learn it first", as if nothing had been tried (user, 2026-11-04)."""
        c = self.hunt.company(ref)
        st = self.live_state(c)
        with self.hunt.lock:
            tried = {**st["tried"], **{u: time.strftime("%Y-%m-%dT%H:%M:%S") for u in urls if u in (st.get("unrouted") or [])}}
            self.postings.set_scan_state(c["id"], tried=tried)
        self.publish()
        return tried

    def site_status(self, c: dict) -> dict[str, str]:
        """Whether each site is scanned automatically, for the window to say on its row, from what is known without
        asking anything: "api" (a provider reads it), "route" (a recorded route reads it), "no" (a scan can't read
        it: the AI has to learn it), "" (not checked yet: no scan has looked at it)."""
        st = self.live_state(c)
        route, unrouted = st.get("route"), set(st.get("unrouted") or [])
        # a route whose every page a provider reads is read through the provider (_scan): its sites say "api"
        if route and all(self._providers.get(p["url"]) for p in route.get("pages") or [{"url": ""}]):
            route_how = "api"
        else:
            route_how = "route"
        out = {}
        for s in self.sites_of(c):
            url, lst = s["url"], self.list_of(s)
            # a site that didn't answer the check: not known yet, never "can't be scanned" (its row says it didn't connect)
            out[url] = ("no" if url in unrouted else "api" if self._providers.get(lst) else route_how if self.covers(route, url)
                        else "" if lst in self._unreached
                        else "" if lst in self._providers or not st.get("at") else "api")
        return out

    @staticmethod
    def unrouted(plan: list[dict]) -> list[str]:
        """The sites a scan can't read: no provider, and no recorded route reads them."""
        return [s["url"] for s in plan if s["how"] == "claude"]

    def check_sites(self, ref: str, urls: list[str]) -> list[str]:
        """Sites were just added to a company: find out at once whether a scan can read them. One that can (a provider,
        or the route already reads it) is scanned now; one that can't is marked on the company for the AI to learn,
        so the window says so instead of the next scan quietly reading only the old ones. Returns those."""
        cid = self.hunt.company(ref)["id"]
        # a scan of it going on read the sites it had when it began: wait for it, then check the new ones
        # (a site the AI adds while the first scan of a new company runs would otherwise never be read)
        until = time.time() + CHECK_WAIT
        while True:
            with self.lock:
                if cid not in self.running:
                    self.running[cid] = "check"
                    self.started[cid] = time.time()
                    break
            if time.time() > until:
                return []
            time.sleep(0.5)
        try:
            c = self.hunt.company(cid)
        except KeyError:   # removed meanwhile
            with self.lock:
                self.running.pop(cid, None)
                self.started.pop(cid, None)
            return []
        self.publish()
        try:
            plan = self.plan(c)
            missing = self.unrouted(plan)
            was = self.postings.scan_state(c["id"]).get("needs") or ""   # "browser" still waits for a scan
            needs = "claude" if missing else "sites" if not plan else "" if was in ("claude", "sites") else was
            self.postings.set_scan_state(c["id"], needs=needs, unrouted=missing)
        finally:
            with self.lock:
                self.running.pop(c["id"], None)
                self.started.pop(c["id"], None)
            self.publish()
        if any(s["how"] != "claude" for s in plan if s["url"] in urls):
            self.start([c["id"]])
        return [u for u in urls if u in missing]

    def _hold(self):
        return self.browser.hold() if hasattr(self.browser, "hold") else contextlib.nullcontext()

    # ---- one company ----
    def run_route(self, route: dict, errors: list[str] | None = None, unread: list[int] | None = None,
                  totals: dict[str, int] | None = None) -> list[dict]:
        """Each address in a tab of its own, off the screen (core/browser.py read_pages): a scan never takes the
        tab the user or Claude is working in. A browser the scan started closes itself once nothing uses it
        (core/browser.py close_if_idle); one the user opened stays."""
        with self._hold():
            try:
                self.browser.ensure()
            except BrowserError as e:
                raise RouteFailed(str(e))
            return self._read_route(route, errors, unread, totals)

    def _read_route(self, route: dict, errors: list[str] | None = None, unread: list[int] | None = None,
                    totals: dict[str, int] | None = None) -> list[dict]:
        """One page that doesn't read (a slow site timing out) never throws away the pages that did: its trouble
        goes into `errors`, how many of the route's pages didn't read into `unread` ([unread, all]), and the rest
        is kept. Only a route that read nothing at all fails. Pages in a row on one address share one opened page.
        A script that says how many roles its list has (`total`, as the site says it: "448 个职位") puts it in
        `totals`, one number per list (its `list` name, else its address): the pages of one long list split
        across several scripts each say the whole list's number, so the biggest counts, not their sum."""
        pages = route.get("pages") or []
        groups: list[tuple[str, list[str]]] = []
        for p in pages:
            if groups and groups[-1][0] == p["url"]:
                groups[-1][1].append(p["script"])
            else:
                groups.append((p["url"], [p["script"]]))
        found: list[dict] = []
        problems: list[str] = []
        empty: list[str] = []
        missed = 0
        for url, scripts in groups:
            outs: list[Any] = [None] * len(scripts)
            todo = list(range(len(scripts)))
            for attempt in range(len(RETRY_WAITS) + 1):
                if attempt:
                    time.sleep(self.retry_waits[attempt - 1])
                got = self._read_pages(url, [scripts[i] for i in todo])
                for i, out in zip(todo, got):
                    outs[i] = out
                todo = [i for i in todo if isinstance(outs[i], PageNotOpened)]
                if not todo:
                    break
            if todo:
                missed += len(todo)
                problems.append(t("the page {url} didn't open in {tries} tries: {error}", url=url, tries=len(RETRY_WAITS) + 1,
                                  error=str(outs[todo[0]])))
            for i, out in enumerate(outs):
                if isinstance(out, PageNotOpened):
                    continue
                if isinstance(out, Exception):
                    missed += 1
                    problems.append(t("the page {url} didn't read: {error}", url=url, error=str(out)))
                    continue
                if isinstance(out, dict) and out.get("signed_out"):
                    raise RouteFailed(t("the site asks to sign in"), signed_out=True)
                rows = out.get("postings") if isinstance(out, dict) else out
                if isinstance(rows, list) and not rows:
                    # no roles on a page that answered: a list that draws itself after loading may not have been
                    # there yet (鹏芯微, 2026-11-05: 20 roles one scan, none the next). Read once more, a while later
                    time.sleep(self.empty_wait)
                    again = self._read_pages(url, [scripts[i]])[0]
                    if not isinstance(again, Exception) and not (isinstance(again, dict) and again.get("signed_out")):
                        out = again
                        rows = out.get("postings") if isinstance(out, dict) else out
                if not isinstance(rows, list):
                    missed += 1
                    problems.append(t("the script on {url} gave no list of postings", url=url))
                    continue
                if not rows:
                    empty.append(url)
                found += rows
                if totals is not None and isinstance(out, dict) and str(out.get("total") or "").strip().isdigit():
                    k = str(out.get("list") or url)
                    totals[k] = max(totals.get(k, 0), int(str(out["total"]).strip()))
        if not found:
            # said as it is: the pages opened and the scripts ran, and read no roles twice over. Never "the site has
            # changed", which nobody checked (鹏芯微's route read none on a site with 24 roles, 2026-11-05)
            raise RouteFailed("; ".join(problems) or t("the route's pages opened but its scripts read no roles on {pages}, twice, "
                                                       "a while apart: the list may not show yet when they read it, or they "
                                                       "read the wrong part of the page", pages=", ".join(dict.fromkeys(empty))))
        if errors is not None:
            errors += problems
        elif problems:
            raise RouteFailed("; ".join(problems))
        if unread is not None and missed:
            unread[:] = [missed, len(pages)]
        return found

    def _read_pages(self, url: str, scripts: list[str]) -> list[Any]:
        try:
            # 90 s for a page near the user; longer across the Great Firewall or for a site slow before (patience.py)
            wait = patience.of(url, OPEN_TIMEOUT, 3).timeout
            got = self.browser.read_pages(url, scripts, timeout=max(120, wait), open_timeout=wait)
            # a page that didn't open counts toward more tries next time; its time, all its scripts' together, isn't
            # how long the site took to answer, so isn't kept
            if any(isinstance(x, PageNotOpened) for x in got or []):
                patience.missed(url)
            return got
        except (BrowserError, CdpError) as e:   # the tab itself didn't open: every script on it is unread
            return [e] * len(scripts)

    def scan(self, ref: str, *, allow_route: bool = True, just_tried: bool = False) -> dict:
        """Read every site of the company that can be read without Claude. Says what needs Claude
        (`needs`: "sites" when none is known, "claude" when a site has no route yet), and keeps that on the scan."""
        res = self._scan(ref, allow_route=allow_route, just_tried=just_tried)
        if not res.get("busy"):
            self.postings.set_scan_state(res["company"], needs=res.get("needs") or "", unrouted=res.get("unrouted") or [])
        return res

    def wait_scan(self, cid: str, timeout: float = 3300) -> dict:
        """A scan that was already running when one was asked for: waits for it to end and answers with its result,
        as if this scan had read it. Told "still being scanned, wait", the AI ended its turn there and called the
        company done before the scan said a site couldn't be read (货拉拉, 2026-11-10)."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if cid not in self.running:
                    break
            time.sleep(1)
        else:
            return {"company": cid, "busy": True, "since": round(time.time() - self.started.get(cid, time.time()))}
        st = self.live_state(self.hunt.company(cid))
        if not st.get("ok", True) and st.get("error"):
            return {"company": cid, "error": st["error"], "signed_out": st.get("signed_out", False)}
        # the scan waited for read nothing (its only site needs the AI): said as _scan says it, never "0 open", which
        # reads as a company with no roles (seen when node made the first scan slow enough to be waited for, 2026-11-12)
        if st.get("needs") and "found" not in st:
            return {"company": cid, "needs": st["needs"], "unrouted": st.get("unrouted") or []}
        return {"company": cid, "found": st.get("found", 0), "new": len(st.get("new") or []), "closed": len(st.get("closed") or []),
                "changed": len(st.get("changed") or []), "needs": st.get("needs") or "", "unrouted": st.get("unrouted") or [],
                "short": st.get("short") or None}

    def _scan(self, ref: str, *, allow_route: bool = True, just_tried: bool = False) -> dict:
        c = self.hunt.company(ref)
        with self.lock:
            if c["id"] in self.running:
                return {"company": c["id"], "busy": True, "since": round(time.time() - self.started.get(c["id"], time.time()))}
            self.running[c["id"]] = "scan"
            self.started[c["id"]] = time.time()
        self.publish()
        try:
            plan = self.plan(c)
            if not plan:
                return {"company": c["id"], "needs": "sites"}
            found: list[dict] = []
            hows, errors, needs, unread = [], [], [], []
            totals: dict[str, int] = {}
            routed: list[dict] = []
            api = [s for s in plan if s["how"] not in ("route", "claude", "unreached")]
            # a route page on a list a provider reads (a Moka or Workday board the AI recorded a script for) is read
            # through the provider: the AI's script only for what no provider reads (8 companies, 2026-11-03)
            route = self.postings.route(c["id"]) or {}
            scripts = (route.get("pages") or []) if any(s["how"] == "route" for s in plan) else []
            self.detect([p["url"] for p in scripts], c["name"])
            via_api = [p["url"] for p in scripts if self._providers.get(p["url"])]
            scripts = [p for p in scripts if not self._providers.get(p["url"])]
            # one list read once: two sites fronting the same board (its own page and the board itself) are one read
            lists = list(dict.fromkeys([*(self.list_of(s) for s in api), *via_api]))
            # the lists the scan reads itself (北森, Feishu on any domain); the rest through career-ops' providers
            readers = {beisen.PROVIDER: (beisen.lists, self.beisen_read), feishu.PROVIDER: (feishu.lists, self.feishu_read),
                       hotjob.PROVIDER: (hotjob.lists, self.hotjob_read),
                       alitalent.PROVIDER: (alitalent.lists, self.alitalent_read),
                       iguopin.PROVIDER: (iguopin.lists, self.iguopin_read)}
            ours = [(prov, u) for prov, (each, _) in readers.items()
                    for u in each([x for x in lists if self._providers.get(x) == prov])]
            # which of the company's sites a list stands for: 北森 reads a whole domain as one list, Feishu a portal
            same = {beisen.PROVIDER: beisen.origin, feishu.PROVIDER: lambda u: (feishu.origin(u), feishu.portal(u)),
                    hotjob.PROVIDER: hotjob.key, alitalent.PROVIDER: alitalent.key, iguopin.PROVIDER: iguopin.key}
            missed: dict[str, str] = {}   # site -> "none" (nothing read off it this time) or "part" (some pages)

            def miss(u: str, what: str) -> None:
                key = same.get(self._providers.get(u) or "", lambda x: x)
                for s in plan:
                    if key(self.list_of(s)) == key(u) and missed.get(s["url"]) != "none":
                        missed[s["url"]] = what
            # a site that didn't answer whether a system reads it: not read, said, nothing of it closed
            for s in plan:
                if s["how"] == "unreached":
                    missed[s["url"]] = "unreached"
                    errors.append(f"{s['url']}: didn't answer when asked which recruiting system it runs on; "
                                  "asked again at the next scan")
            for r in self._bridge([{"key": u, "name": c["name"], "url": u} for u in lists
                                   if self._providers.get(u) not in readers]):
                if r.get("error"):
                    errors.append(f"{r['url']}: {r['error']}")
                    miss(r["url"], "none")
                else:
                    found += r.get("jobs") or []
                    hows.append(str(r.get("provider")))
                    if r.get("partial"):   # read in part (a page failed, a cap hit): what came is kept, nothing closed
                        errors.append(f"{r['url']}: {r['partial']}")
                        miss(r["url"], "part")
            counted: list[dict] = []   # rows read from a list that says its own count: checked against it
            if ours:
                keep = self._known_links(c["id"])
                portals: set[str] = set()
                # two lists at a time: 小米's four Feishu portals took 4 to 12 minutes each from Australia (2026-11-09)
                def one(pu: tuple[str, str]) -> dict | Exception:
                    try:
                        return readers[pu[0]][1](pu[1])
                    except Exception as e:  # noqa: BLE001  (ReadFailed, or anything else the site does)
                        return e
                with ThreadPoolExecutor(2) as pool:
                    got = list(pool.map(one, ours))
                for (prov, u), r in zip(ours, got):
                    if isinstance(r, Exception):
                        errors.append(f"{u}: {r}")
                        miss(u, "none")
                        continue
                    # one portal under two addresses (job.imou.com and imou.zhiye.com) is one list: read once
                    if r.get("portal") and r["portal"] in portals:
                        continue
                    portals.add(r.get("portal") or u)
                    if r.get("failed"):   # read in part: what came is kept, the error said, nothing closed
                        errors.append(f"{u}: {r['failed']}")
                        miss(u, "part")
                    # a role already kept under another spelling of its link (a route the AI wrote linked it
                    # /campus/jobs?jobAdId=<number>) keeps that link: a role's link is its key
                    jobs = [{**j, "url": keep.get(j.get("id") or "") or keep.get(j.get("number") or "") or j["url"]}
                            for j in r.get("jobs") or []]
                    found += jobs
                    counted += jobs
                    if isinstance(r.get("total"), int):
                        totals[u] = max(totals.get(u, 0), r["total"])
                    hows.append(prov)
            if scripts:
                if allow_route:
                    self.running[c["id"]] = "route"
                    self.publish()
                    try:
                        fresh = self._fresh.pop(c["id"], None) if just_tried else None
                        if fresh and fresh["pages"] == scripts:   # the route was tried just now: what it read
                            routed = fresh["rows"]
                            errors += fresh["errors"]
                            unread[:] = fresh["unread"]
                            totals.update(fresh["totals"])
                        else:
                            routed = self.run_route({**route, "pages": scripts}, errors, unread, totals)
                        found += routed
                        hows.append("route")
                    except RouteFailed as e:
                        if not found:
                            st = self.postings.failed(c["id"], str(e), how="route", signed_out=e.signed_out)
                            return {"company": c["id"], "error": str(e), "signed_out": e.signed_out, "scan": st, "route_broken": True}
                        errors.append(str(e))
                else:
                    needs.append("browser")
            found = same_once(found)
            missing = self.unrouted(plan)
            if missing:   # said first: waiting for the browser fixes itself on a scan the user starts, this doesn't
                needs.insert(0, "claude")
            if not hows:
                if errors:
                    st = self.postings.failed(c["id"], "; ".join(errors), how=",".join(s["how"] for s in api) or "api")
                    st = self.postings.set_scan_state(c["id"], missed=missed or None)
                    return {"company": c["id"], "error": "; ".join(errors), "scan": st, "needs": needs[0] if needs else "",
                            "unrouted": missing}
                return {"company": c["id"], "needs": needs[0] if needs else "claude", "unrouted": missing}
            short = short_of(routed + counted, totals)
            # read in part, by the site's own count: what wasn't read isn't closed (a cheaper model's route that
            # stopped at page 38 of 45 must never close the roles on pages 39 to 45)
            complete = not errors and not needs and not short
            res = self.postings.merge(c["id"], found, how="+".join(dict.fromkeys(hows)), complete=complete)
            # short: [read, the site says] — kept until a scan reads them all, said wherever the count is shown
            # missed: the sites this scan read nothing or only some pages of, for their rows and the status to say
            self.postings.set_scan_state(c["id"], short=short or None, missed=missed or None)
            if errors:   # unread: how many of the route's pages didn't read, of how many ([17, 20]), for the window to say
                self.postings.set_scan_state(c["id"], error="; ".join(errors), unread=unread or None)
            return {"company": c["id"], "found": res["found"], "new": len(res["new"]), "closed": len(res["closed"]),
                    "changed": len(res["changed"]), "needs": needs[0] if needs else "", "errors": errors, "unrouted": missing,
                    "short": short or None}
        finally:
            with self.lock:
                self.running.pop(c["id"], None)
                self.started.pop(c["id"], None)
            self.publish()

    def _known_links(self, cid: str) -> dict[str, str]:
        """The links the company's postings are kept under, by the ids in them (a 北森 role's uuid or its number, a
        HotJob role's 24-hex postId: the routes the AI wrote linked them each its own way, postType=2 or =social)."""
        out: dict[str, str] = {}
        for p in self.postings.all(cid):
            for tok in re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                                  r"|(?<![0-9a-zA-Z])[0-9a-fA-F]{24}(?![0-9a-fA-F])|\d{6,}", p.get("url") or ""):
                out.setdefault(tok.lower(), p["url"])
        return out

    def browser_open(self) -> bool:
        try:
            return (self.browser.status() or {}).get("state") == "running"
        except Exception:  # noqa: BLE001
            return False

    def scan_many(self, ids: list[str], *, auto: bool = False) -> list[dict]:
        """`auto` (the daily scan when the window opens) never opens the browser by itself: sites read by a route
        wait for a scan the user starts, unless the browser is open already."""
        out = []
        with self._hold():                  # not closed between one company and the next
            routes = not auto or self.browser_open()
            for cid in ids:
                try:
                    out.append(self.scan(cid, allow_route=routes))
                except Exception as e:  # noqa: BLE001  one company's trouble never stops the rest
                    out.append({"company": cid, "error": str(e)})
            if not auto or out:
                self.postings.mark_all_scanned()
            self.last_run = run_summary(out, auto)
        self.publish()
        return out

    def start(self, ids: list[str], *, auto: bool = False) -> None:
        """Scan in the background. While a run goes on, more companies join its queue instead of a second run;
        one the user starts makes the whole run theirs (routes may open the browser, and it ends with a toast)."""
        with self.lock:
            new = [i for i in dict.fromkeys(ids) if i not in self.queue and i not in self.running]
            if self.batch is not None:
                self.queue += new
                self.batch["total"] += len(new)
                self.batch["auto"] = self.batch["auto"] and auto
                joined = True
            elif new:
                self.queue, self.batch, joined = new, {"total": len(new), "done": 0, "auto": auto}, False
            else:
                return
        self.publish()   # the window shows the run at once, before the first company is read
        if not joined:
            threading.Thread(target=self._run, daemon=True, name="hunt-scan").start()

    def _run(self) -> None:
        out: list[dict] = []
        auto, ended = True, False
        try:
            with self._hold():
                while True:
                    with self.lock:
                        if not self.queue:
                            auto = self.batch["auto"]
                            self.batch, ended = None, True   # in the same lock as the check: a later start begins a new run
                            break
                        cid = self.queue.pop(0)
                        auto = self.batch["auto"]
                    try:
                        out.append(self.scan(cid, allow_route=not auto or self.browser_open()))
                    except Exception as e:  # noqa: BLE001  one company's trouble never stops the rest
                        out.append({"company": cid, "error": str(e)})
                    with self.lock:
                        if self.batch is not None:
                            self.batch["done"] += 1
                if not auto or out:
                    self.postings.mark_all_scanned()
                self.last_run = run_summary(out, auto)
        except BaseException:
            with self.lock:   # never left looking busy
                if not ended:
                    self.batch, self.queue = None, []
            raise
        finally:
            self.publish()

    # ---- routes that look like one recruiting system (jobhunt/systems.py) ----
    def _read_built_in(self, url: str, path: str) -> bool:
        """A page or API path a built-in scan reads already: its route is old (written before that scan), not a
        system still to be built."""
        return bool(self._providers.get(url)) or beisen.is_beisen(url) or path in BUILT_IN_APIS

    def _route_pages(self) -> list[tuple[str, str, str]]:
        names = {c["id"]: c["name"] for c in self.hunt.companies()}
        return [(names.get(cid, cid), p["url"], p["script"])
                for cid, st in self.postings.scans()["companies"].items() if cid in names
                for p in (st.get("route") or {}).get("pages") or []]

    def shared_systems(self) -> list[dict]:
        """Every API the routes of several companies call that no built-in scan reads yet."""
        return systems.groups(self._route_pages(), self._read_built_in)

    # ---- telling the developer which systems still need the AI (jobhunt/report.py) ----
    def report_offers(self) -> dict:
        """The systems worth reporting, not yet reported or ignored: {"items", "text", "issue_url"}."""
        pages = self._route_pages()
        done = report.load(self.hunt._load(report.FILE))
        items = [x for x in report.offers(pages, self._read_built_in) if x["key"] not in done]
        body = report.text(items) if items else ""
        return {"items": items, "text": body, "issue_url": report.issue_url(items, body) if items else ""}

    def mark_reported(self, keys: list[str], how: str) -> None:
        """Kept so the same systems aren't offered again: sent as an issue, copied, or ignored by the user."""
        with self.hunt.lock:
            done = report.load(self.hunt._load(report.FILE))
            done.update({k: {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "how": how} for k in keys})
            self.hunt._write(report.FILE, done)

    def kin(self, ref: str) -> list[dict]:
        """The other companies whose routes call an API this company's route calls (no built-in scan reads it)."""
        c = self.hunt.company(ref)
        mine = [(c["name"], p["url"], p["script"]) for p in (self.postings.route(c["id"]) or {}).get("pages") or []]
        return systems.kin(c["name"], mine, self._route_pages(), self._read_built_in)

    def save_route(self, ref: str, data: Any, *, check: bool = True) -> tuple[dict, list[str]]:
        """The route records the sites it was made for: every site of the company a provider doesn't read, unless it
        names them (`sites`). With `add`, its pages join the route the company has (a site added later). `check`:
        it is run once first, and kept only when it reads every role its site says it has (try_route)."""
        r, errs = validate_route(data)
        if errs:
            return {}, errs
        c = self.hunt.company(ref)
        # the AI reads only what no provider can: a page a provider reads goes on the site as its list instead, or
        # the scan reads its roles twice, once each way (星河科技, 2026-11-03: 67 roles listed twice)
        self.detect([p["url"] for p in r["pages"]], c["name"])
        api = [(p["url"], self._providers[p["url"]]) for p in r["pages"] if self._providers.get(p["url"])]
        if api:
            return {}, [f"{u}: {prov} reads this list with no route. Put it on the company as the list of the site it "
                        f"belongs to (`fill` with every site, that one as {{\"url\": \"<that site>\", \"list\": \"{u}\"}}) and scan; "
                        f"record route pages only for lists no provider reads" for u, prov in api]
        named = data.get("sites") if isinstance(data.get("sites"), list) else None
        sites = [str(u) for u in named] if named is not None else [s["url"] for s in self.plan(c) if s["how"] in ("route", "claude")]
        old = self.postings.route(c["id"]) if data.get("add") else None
        if old:
            have = {(p["url"], p["script"]) for p in r["pages"]}
            r["pages"] = [p for p in old.get("pages") or [] if (p["url"], p["script"]) not in have] + r["pages"]
            if len(r["pages"]) > MAX_PAGES:
                return {}, [f"with the {len(old.get('pages') or [])} pages the route has, this makes {len(r['pages'])}; "
                            f"a route has at most {MAX_PAGES}: read more postings per page"]
            kept = old["sites"] if isinstance(old.get("sites"), list) else \
                [s["url"] for s in self.sites_of(c) if self.covers(old, s["url"])]
            sites = list(dict.fromkeys([*kept, *sites]))
        r["sites"] = sites
        # tried before it is kept (鹏芯微, 2026-11-05: a route that read 20 of the site's 24 roles was kept, and the
        # next scan read none): one that reads no roles, or fewer than the site says it lists, goes back to the AI
        # with what went wrong, and the company keeps the route it had
        errs = self.try_route(c, r) if check else []
        if errs:
            return {}, errs
        self.postings.set_scan_state(c["id"], route=r)
        return r, []

    def try_route(self, c: dict, route: dict) -> list[str]:
        """What is wrong with a route, by running it once now: [] when it reads every role its site says it has.
        What it read is kept for the scan that follows (`_scan`), so a slow site isn't read twice in a row."""
        totals: dict[str, int] = {}
        errors: list[str] = []
        unread: list[int] = []
        self._fresh.pop(c["id"], None)
        try:
            rows = self.run_route(route, errors, unread, totals)
        except RouteFailed as e:
            if e.signed_out:   # nothing wrong with the route: the user signs in, and the scan reads it then
                return []
            return [f"tried before keeping it, the route read no roles: {e}. Nothing was kept; fix the script "
                    f"(wait for the list to show, or better, call the API the page itself calls) and send it again"]
        short = short_of(rows, totals)
        if short:
            return [f"tried before keeping it, the route read {short[0]} different roles and the site says it lists "
                    f"{short[1]}. Nothing was kept. Find where the rest go missing (a page that shows only the "
                    f"first part of the list, pages it stops before, a filter, two roles kept as one because they "
                    f"share a title), fix the route and send it again"]
        self._fresh[c["id"]] = {"pages": route["pages"], "rows": rows, "errors": errors, "unread": unread, "totals": totals}
        return []
