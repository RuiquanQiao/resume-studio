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
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.browser import BrowserError
from core.i18n import t

from .postings import Postings
from .store import Hunt

BRIDGE = Path(__file__).resolve().parent.parent / "jobs" / "co_scan.mjs"


def validate_route(data: Any) -> tuple[dict, list[str]]:
    errs: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list) or not data["pages"]:
        return {}, ['the route must be {"pages": [{"url", "script"}, ...]}']
    pages = []
    for n, p in enumerate(data["pages"][:20]):
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


class RouteFailed(Exception):
    def __init__(self, message: str, signed_out: bool = False):
        super().__init__(message)
        self.signed_out = signed_out


class Scanner:
    def __init__(self, hunt: Hunt, postings: Postings, tool: Any, browser: Any, publish: Callable[[], None]):
        self.hunt, self.postings, self.tool, self.browser, self.publish = hunt, postings, tool, browser, publish
        self.running: dict[str, str] = {}      # company id -> what is reading it now
        self.lock = threading.Lock()
        self._providers: dict[str, str | None] = {}   # url -> career-ops provider id (or None), per run of the app
        self.last_run: dict = {}               # the last scan_many: {at, auto, companies, new, closed, failed}
        # the run started from the window (start): the companies still waiting, and how far it got, so the window
        # can say "3 of 12" from the click to the end, between one company and the next too
        self.queue: list[str] = []
        self.batch: dict | None = None         # {total, done, auto} while a run goes on

    # ---- which sites, read how ----
    @staticmethod
    def sites_of(c: dict) -> list[dict]:
        sites = list(c.get("sites") or [])
        if c.get("site") and not any(s["url"] == c["site"] for s in sites):
            sites.insert(0, {"url": c["site"], "kind": "campus", "system": ""})
        return sites

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
        unknown = [s for s in sites if s["url"] not in self._providers]
        if unknown:
            for r in self._bridge([{"key": s["url"], "name": c["name"], "url": s["url"]} for s in unknown], detect=True):
                if not r.get("error"):
                    self._providers[r["url"]] = r.get("provider")
        route = self.postings.route(c["id"])
        out = []
        for s in sites:
            prov = self._providers.get(s["url"])
            out.append({**s, "how": prov or ("route" if route else "claude")})
        return out

    def _hold(self):
        return self.browser.hold() if hasattr(self.browser, "hold") else contextlib.nullcontext()

    # ---- one company ----
    def run_route(self, route: dict) -> list[dict]:
        """Each page in a tab of its own, off the screen (core/browser.py read_page): a scan never takes the tab
        the user or Claude is working in. A browser the scan started closes itself once nothing uses it
        (core/browser.py close_if_idle); one the user opened stays."""
        with self._hold():
            try:
                self.browser.ensure()
            except BrowserError as e:
                raise RouteFailed(str(e))
            return self._read_route(route)

    def _read_route(self, route: dict) -> list[dict]:
        found: list[dict] = []
        for p in route.get("pages") or []:
            try:
                out = self.browser.read_page(p["url"], p["script"], timeout=120)
            except BrowserError as e:
                raise RouteFailed(t("the page {url} didn't read: {error}", url=p["url"], error=str(e)))
            if isinstance(out, dict) and out.get("signed_out"):
                raise RouteFailed(t("the site asks to sign in"), signed_out=True)
            rows = out.get("postings") if isinstance(out, dict) else out
            if not isinstance(rows, list):
                raise RouteFailed(t("the script on {url} gave no list of postings", url=p["url"]))
            found += rows
        if not found:
            raise RouteFailed(t("the route found no postings at all: the site has probably changed"))
        return found

    def scan(self, ref: str, *, allow_route: bool = True) -> dict:
        """Read every site of the company that can be read without Claude. Says what needs Claude
        (`needs`: "sites" when none is known, "claude" when a site has no route yet), and keeps that on the scan."""
        res = self._scan(ref, allow_route=allow_route)
        if not res.get("busy"):
            self.postings.set_scan_state(res["company"], needs=res.get("needs") or "")
        return res

    def _scan(self, ref: str, *, allow_route: bool = True) -> dict:
        c = self.hunt.company(ref)
        with self.lock:
            if c["id"] in self.running:
                return {"company": c["id"], "busy": True}
            self.running[c["id"]] = "scan"
        self.publish()
        try:
            plan = self.plan(c)
            if not plan:
                return {"company": c["id"], "needs": "sites"}
            found: list[dict] = []
            hows, errors, needs = [], [], []
            api = [s for s in plan if s["how"] not in ("route", "claude")]
            for r in self._bridge([{"key": s["url"], "name": c["name"], "url": s["url"]} for s in api]):
                if r.get("error"):
                    errors.append(f"{r['url']}: {r['error']}")
                else:
                    found += r.get("jobs") or []
                    hows.append(str(r.get("provider")))
            if any(s["how"] == "route" for s in plan):
                if allow_route:
                    self.running[c["id"]] = "route"
                    self.publish()
                    try:
                        found += self.run_route(self.postings.route(c["id"]) or {})
                        hows.append("route")
                    except RouteFailed as e:
                        if not found:
                            st = self.postings.failed(c["id"], str(e), how="route", signed_out=e.signed_out)
                            return {"company": c["id"], "error": str(e), "signed_out": e.signed_out, "scan": st, "route_broken": True}
                        errors.append(str(e))
                else:
                    needs.append("browser")
            if any(s["how"] == "claude" for s in plan):
                needs.append("claude")
            if not hows:
                if errors:
                    st = self.postings.failed(c["id"], "; ".join(errors), how=",".join(s["how"] for s in api) or "api")
                    return {"company": c["id"], "error": "; ".join(errors), "scan": st, "needs": needs[0] if needs else ""}
                return {"company": c["id"], "needs": needs[0] if needs else "claude"}
            complete = not errors and not needs
            res = self.postings.merge(c["id"], found, how="+".join(hows), complete=complete)
            if errors:
                self.postings.set_scan_state(c["id"], error="; ".join(errors))
            return {"company": c["id"], "found": res["found"], "new": len(res["new"]), "closed": len(res["closed"]),
                    "changed": len(res["changed"]), "needs": needs[0] if needs else "", "errors": errors}
        finally:
            with self.lock:
                self.running.pop(c["id"], None)
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
            self.last_run = {"at": time.time(), "auto": auto, "companies": len(out),
                             "new": sum(int(r.get("new") or 0) for r in out),
                             "closed": sum(int(r.get("closed") or 0) for r in out),
                             "failed": sum(1 for r in out if r.get("error"))}
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
                self.last_run = {"at": time.time(), "auto": auto, "companies": len(out),
                                 "new": sum(int(r.get("new") or 0) for r in out),
                                 "closed": sum(int(r.get("closed") or 0) for r in out),
                                 "failed": sum(1 for r in out if r.get("error"))}
        except BaseException:
            with self.lock:   # never left looking busy
                if not ended:
                    self.batch, self.queue = None, []
            raise
        finally:
            self.publish()

    def save_route(self, ref: str, data: Any) -> tuple[dict, list[str]]:
        r, errs = validate_route(data)
        if errs:
            return {}, errs
        c = self.hunt.company(ref)
        self.postings.set_scan_state(c["id"], route=r)
        return r, []
