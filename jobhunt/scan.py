"""Scanning companies' careers sites for open postings (docs/JOB_HUNT.md, 2026-10-18).

Each site is read the cheapest way that works, never by Claude once it has been set up:

1. **career-ops' providers**, when career-ops is installed: Moka, Feishu (ByteDance), Tencent, Meituan, Alibaba,
   Greenhouse, Lever, Ashby, Workday and the rest read a site's public job API with no browser
   (`jobs/co_scan.mjs` loads them from career-ops' own folder, so its updates keep coming).
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
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from core.browser import BrowserError, PageNotOpened
from core.cdp import CdpError
from core.i18n import t

from .postings import Postings
from .store import Hunt, site_host, site_key

BRIDGE = Path(__file__).resolve().parent.parent / "jobs" / "co_scan.mjs"
MAX_PAGES = 20   # of a recorded route, each with 2 minutes
# a scan has no one waiting on it, and a user abroad reads sites on the far side of the world (user, 2026-11-02):
# a page gets 90 s to open, and two more tries after a pause, before it counts as unread
OPEN_TIMEOUT = 90
RETRY_WAITS = (5, 15)


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
    """One role read twice, through two addresses of the same board (Feishu's /index/position/<id> and
    /xinghe/position/<id>): kept once. The same title with the same long id in its address is the same role."""
    seen, out = set(), []
    for p in found:
        ids = re.findall(r"\d{8,}", urlsplit(str(p.get("url") or "")).path)
        k = (str(p.get("title") or "").strip().casefold(), max(ids, key=len)) if ids else None
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(p)
    return out


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
        self.last_run: dict = {}               # the last scan_many: {at, auto, companies, new, closed, failed}
        # the run started from the window (start): the companies still waiting, and how far it got, so the window
        # can say "3 of 12" from the click to the end, between one company and the next too
        self.queue: list[str] = []
        self.batch: dict | None = None         # {total, done, auto} while a run goes on
        self.retry_waits = RETRY_WAITS         # seconds before each new try of a page that didn't open

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
        try:
            return bool(self.tool.installed()) and bool(shutil.which("node"))
        except Exception:  # noqa: BLE001
            return False

    def _bridge(self, entries: list[dict], detect: bool = False) -> list[dict]:
        if not entries or not self.providers_ready():
            return []
        tmp = self.hunt.dir / f".scan-{threading.get_ident()}.json"
        self.hunt.dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
        try:
            proc = self.tool.node(str(BRIDGE), str(self.tool.root), str(tmp), *(["--detect"] if detect else []), timeout=600)
        except Exception as e:  # noqa: BLE001  (ToolError: node missing, too slow)
            return [{"key": x["key"], "url": x["url"], "provider": None, "error": str(e)} for x in entries]
        finally:
            tmp.unlink(missing_ok=True)
        for line in reversed((proc.stdout or "").strip().splitlines()):
            if line.startswith("{"):
                try:
                    return json.loads(line).get("results") or []
                except ValueError:
                    break
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
            out.append({**s, "how": prov or ("route" if self.covers(route, s["url"]) else "claude")})
        return out

    def detect(self, urls: list[str], name: str = "") -> None:
        """Which provider reads each address (None: none does), asked once per address and kept."""
        unknown = list(dict.fromkeys(u for u in urls if u not in self._providers))
        for r in self._bridge([{"key": u, "name": name, "url": u} for u in unknown], detect=True):
            if not r.get("error"):
                self._providers[r["url"]] = r.get("provider")

    def live_state(self, c: dict) -> dict:
        """The company's scan state as its sites are now: a site that can't be scanned is warned about only while it
        is still one of the company's sites (the AI or the user may have replaced it since the last scan), and a
        company whose unscannable sites are all gone no longer needs the AI."""
        st = dict(self.postings.scan_state(c["id"]))
        was = st.get("unrouted") or []
        if was:
            now = {s["url"] for s in self.sites_of(c)}
            st["unrouted"] = [u for u in was if u in now]
            if not st["unrouted"] and st.get("needs") == "claude":
                st["needs"] = ""
        return st

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
            out[url] = ("no" if url in unrouted else "api" if self._providers.get(lst) else route_how if self.covers(route, url)
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
        c = self.hunt.company(ref)
        with self.lock:
            if c["id"] in self.running:
                return []
            self.running[c["id"]] = "check"
            self.started[c["id"]] = time.time()
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
    def run_route(self, route: dict, errors: list[str] | None = None, unread: list[int] | None = None) -> list[dict]:
        """Each address in a tab of its own, off the screen (core/browser.py read_pages): a scan never takes the
        tab the user or Claude is working in. A browser the scan started closes itself once nothing uses it
        (core/browser.py close_if_idle); one the user opened stays."""
        with self._hold():
            try:
                self.browser.ensure()
            except BrowserError as e:
                raise RouteFailed(str(e))
            return self._read_route(route, errors, unread)

    def _read_route(self, route: dict, errors: list[str] | None = None, unread: list[int] | None = None) -> list[dict]:
        """One page that doesn't read (a slow site timing out) never throws away the pages that did: its trouble
        goes into `errors`, how many of the route's pages didn't read into `unread` ([unread, all]), and the rest
        is kept. Only a route that read nothing at all fails. Pages in a row on one address share one opened page."""
        pages = route.get("pages") or []
        groups: list[tuple[str, list[str]]] = []
        for p in pages:
            if groups and groups[-1][0] == p["url"]:
                groups[-1][1].append(p["script"])
            else:
                groups.append((p["url"], [p["script"]]))
        found: list[dict] = []
        problems: list[str] = []
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
                if not isinstance(rows, list):
                    missed += 1
                    problems.append(t("the script on {url} gave no list of postings", url=url))
                    continue
                found += rows
        if not found:
            raise RouteFailed("; ".join(problems) or t("the route found no postings at all: the site has probably changed"))
        if errors is not None:
            errors += problems
        elif problems:
            raise RouteFailed("; ".join(problems))
        if unread is not None and missed:
            unread[:] = [missed, len(pages)]
        return found

    def _read_pages(self, url: str, scripts: list[str]) -> list[Any]:
        try:
            return self.browser.read_pages(url, scripts, timeout=120, open_timeout=OPEN_TIMEOUT)
        except (BrowserError, CdpError) as e:   # the tab itself didn't open: every script on it is unread
            return [e] * len(scripts)

    def scan(self, ref: str, *, allow_route: bool = True) -> dict:
        """Read every site of the company that can be read without Claude. Says what needs Claude
        (`needs`: "sites" when none is known, "claude" when a site has no route yet), and keeps that on the scan."""
        res = self._scan(ref, allow_route=allow_route)
        if not res.get("busy"):
            self.postings.set_scan_state(res["company"], needs=res.get("needs") or "", unrouted=res.get("unrouted") or [])
        return res

    def _scan(self, ref: str, *, allow_route: bool = True) -> dict:
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
            api = [s for s in plan if s["how"] not in ("route", "claude")]
            # a route page on a list a provider reads (a Moka or Workday board the AI recorded a script for) is read
            # through the provider: the AI's script only for what no provider reads (8 companies, 2026-11-03)
            route = self.postings.route(c["id"]) or {}
            scripts = (route.get("pages") or []) if any(s["how"] == "route" for s in plan) else []
            self.detect([p["url"] for p in scripts], c["name"])
            via_api = [p["url"] for p in scripts if self._providers.get(p["url"])]
            scripts = [p for p in scripts if not self._providers.get(p["url"])]
            # one list read once: two sites fronting the same board (its own page and the board itself) are one read
            lists = list(dict.fromkeys([*(self.list_of(s) for s in api), *via_api]))
            for r in self._bridge([{"key": u, "name": c["name"], "url": u} for u in lists]):
                if r.get("error"):
                    errors.append(f"{r['url']}: {r['error']}")
                else:
                    found += r.get("jobs") or []
                    hows.append(str(r.get("provider")))
            if scripts:
                if allow_route:
                    self.running[c["id"]] = "route"
                    self.publish()
                    try:
                        found += self.run_route({**route, "pages": scripts}, errors, unread)
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
                    return {"company": c["id"], "error": "; ".join(errors), "scan": st, "needs": needs[0] if needs else "",
                            "unrouted": missing}
                return {"company": c["id"], "needs": needs[0] if needs else "claude", "unrouted": missing}
            complete = not errors and not needs
            res = self.postings.merge(c["id"], found, how="+".join(dict.fromkeys(hows)), complete=complete)
            if errors:   # unread: how many of the route's pages didn't read, of how many ([17, 20]), for the window to say
                self.postings.set_scan_state(c["id"], error="; ".join(errors), unread=unread or None)
            return {"company": c["id"], "found": res["found"], "new": len(res["new"]), "closed": len(res["closed"]),
                    "changed": len(res["changed"]), "needs": needs[0] if needs else "", "errors": errors, "unrouted": missing}
        finally:
            with self.lock:
                self.running.pop(c["id"], None)
                self.started.pop(c["id"], None)
            self.publish()

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

    def save_route(self, ref: str, data: Any) -> tuple[dict, list[str]]:
        """The route records the sites it was made for: every site of the company a provider doesn't read, unless it
        names them (`sites`). With `add`, its pages join the route the company has (a site added later)."""
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
        self.postings.set_scan_state(c["id"], route=r)
        return r, []
