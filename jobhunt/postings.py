"""Open postings: what each company's careers sites list now, found by scanning, never typed (docs/JOB_HUNT.md, 2026-10-18).

    resume-studio-data/hunt/postings.json   {"next": 9, "postings": [{id: "P1", company: "C1", key, title, url, location, category,
                                  batch, deadline, posted, first_seen, last_seen, closed, changed, app, source}]}
    resume-studio-data/hunt/scans.json      {"last_all": "...", "keywords": ["算法", "AI", ...], "daily": true,
                                  "companies": {"C1": {at, ok, error, how, found, new, closed, changed, route}}}

A posting is matched across scans by its link (or, without one, its title and location), after career-ops'
listing-fingerprint idea: a posting seen for the first time is new, one no longer listed is closed, a changed
deadline is kept as `changed`. Postings are not applications: "Apply" turns one into an application of its
company (and links them), so a company listing dozens of roles doesn't flood the progress table.

`keywords` are the roles the user looks for (career-ops' title filter): postings whose title has none of them
are kept but folded away in the window, and don't count as news.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from core.i18n import t

from .store import Hunt, _date, _key, _now, _places, _text

BATCHES = ["campus", "intern", "social", "other"]


def _norm_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    # a hash router's route is the posting itself (Moka: …/10001#/job/<id>); any other fragment is not
    route = p.fragment.rstrip("/") if p.fragment.startswith("/") else ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, route))


def fingerprint(p: dict) -> str:
    url = _norm_url(p.get("url") or "")
    return url if url else "t:" + _key(p.get("title")) + "|" + _key(p.get("location"))


def clean(p: Any) -> dict | None:
    """One posting from a provider, a route's script or Claude, in our shape (None when it has no title)."""
    if not isinstance(p, dict):
        return None
    title = _text(p.get("title"), 200)
    if not title:
        return None
    url = _text(p.get("url"), 800)
    if url and not re.match(r"https?://", url):
        url = ""
    batch = _text(p.get("batch") or p.get("kind"), 20).lower()
    batch = batch if batch in BATCHES else _guess_batch(" ".join([title, batch, _text(p.get("category"), 80)]))
    try:
        deadline = _date(p.get("deadline"))[:10]
    except ValueError:
        deadline = ""
    try:
        posted = _date(p.get("posted"))[:10]
    except ValueError:
        posted = ""
    return {"title": title, "url": url, "location": _text(p.get("location"), 120), "category": _text(p.get("category"), 80),
            "batch": batch, "deadline": deadline, "posted": posted}


def _guess_batch(text: str) -> str:
    s = text.casefold()
    if re.search(r"实习|intern", s):
        return "intern"
    if re.search(r"校招|校园|应届|graduate|campus|new grad|管培", s):
        return "campus"
    return ""


def matches(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    s = title.casefold()
    return any(k.casefold() in s for k in keywords if k)


class Postings:
    def __init__(self, hunt: Hunt):
        self.hunt = hunt

    # ---- files ----
    def _data(self) -> dict:
        return self.hunt._read("postings.json", "postings")

    def scans(self) -> dict:
        try:
            d = json.loads(self.hunt._load("scans.json") or "{}")
        except ValueError:
            d = {}
        d = d if isinstance(d, dict) else {}
        d.setdefault("companies", {})
        d.setdefault("keywords", [])
        d.setdefault("daily", True)
        d.setdefault("last_all", "")
        return d

    def _save_scans(self, d: dict) -> None:
        self.hunt._write("scans.json", d)

    def settings(self, keywords: Any = None, daily: Any = None) -> dict:
        with self.hunt.lock:
            d = self.scans()
            if keywords is not None:
                xs = re.split(r"[,，、;；\n]", keywords) if isinstance(keywords, str) else keywords or []
                d["keywords"] = [k for k in dict.fromkeys(_text(x, 40) for x in xs) if k][:40]
            if daily is not None:
                d["daily"] = bool(daily)
            self._save_scans(d)
        self.hunt._changed()
        return {"keywords": d["keywords"], "daily": d["daily"]}

    def scan_state(self, cid: str) -> dict:
        return self.scans()["companies"].get(cid) or {}

    def set_scan_state(self, cid: str, **fields: Any) -> dict:
        with self.hunt.lock:
            d = self.scans()
            st = {**(d["companies"].get(cid) or {}), **fields}
            d["companies"][cid] = st
            self._save_scans(d)
        return st

    def mark_all_scanned(self) -> None:
        with self.hunt.lock:
            d = self.scans()
            d["last_all"] = _now()
            self._save_scans(d)

    def route(self, cid: str) -> dict | None:
        return self.scan_state(cid).get("route")

    # ---- postings ----
    def all(self, company: str | None = None) -> list[dict]:
        ps = self._data()["postings"]
        if company:
            cid = self.hunt.company(company)["id"]
            ps = [p for p in ps if p["company"] == cid]
        return ps

    def get(self, ref: str) -> dict:
        p = next((x for x in self._data()["postings"] if x["id"] == str(ref).strip()), None)
        if not p:
            raise KeyError(t("No posting “{ref}”", ref=ref))
        return p

    def merge(self, company: str, found: list[Any], *, how: str, complete: bool = True) -> dict:
        """What a scan found: new ones added, gone ones closed (only when the scan read every list: `complete`),
        changed deadlines noted. Returns the counts and the new ones."""
        c = self.hunt.company(company)
        seen_now = _now()
        rows = [x for x in (clean(p) for p in found or []) if x]
        with self.hunt.lock:
            data = self._data()
            mine = {p["key"]: p for p in data["postings"] if p["company"] == c["id"]}
            linkless: dict[str, list[str]] = {}   # title -> the keys of the postings kept without a link
            for key, p in mine.items():
                if key.startswith("t:"):
                    linkless.setdefault(_key(p["title"]), []).append(key)
            new, changed, reopened, keys = [], [], 0, set()
            for r in rows:
                k = fingerprint(r)
                if k in keys:
                    continue
                keys.add(k)
                p = mine.get(k)
                if p is None and r["url"]:
                    # a posting kept without a link (a route that gave none) now has one: the same posting, never
                    # one closed and a new one opened (it keeps its id and the application linked to it). By title
                    # and place, or by title alone when one such posting has it (an old route read the place wrong)
                    bare = fingerprint({**r, "url": ""})
                    if bare not in mine:
                        same = linkless.get(_key(r["title"])) or []
                        bare = same[0] if len(same) == 1 and same[0] in mine else ""
                    if bare in mine:
                        p = mine.pop(bare)
                        p["key"], mine[k] = k, p
                if p is None:
                    p = {"id": f"P{data['next']}", "company": c["id"], "key": k, **r, "first_seen": seen_now, "last_seen": seen_now,
                         "closed": "", "changed": {}, "app": None, "source": how}
                    data["next"] += 1
                    data["postings"].append(p)
                    new.append(p)
                    continue
                if p.get("closed"):
                    p["closed"], reopened = "", reopened + 1
                for f in ("title", "location", "category", "batch", "posted"):
                    if r[f]:
                        p[f] = r[f]
                if r["deadline"] and r["deadline"] != p.get("deadline"):
                    if p.get("deadline"):
                        p["changed"] = {"deadline": [p["deadline"], r["deadline"]], "at": seen_now}
                        changed.append(p)
                    p["deadline"] = r["deadline"]
                if r["url"]:
                    p["url"] = r["url"]
                p["last_seen"], p["source"] = seen_now, how
            # a posting the user already applies to (added by hand, by the same name) is linked to that application
            apps = {_key(a["role"]): a["id"] for a in self.hunt.applications(c["id"])}
            for p in data["postings"]:
                if p["company"] == c["id"] and not p.get("app") and _key(p["title"]) in apps:
                    p["app"] = apps[_key(p["title"])]
            closed = []
            if complete:
                for k, p in mine.items():
                    if k not in keys and not p.get("closed"):
                        p["closed"] = seen_now
                        closed.append(p)
            self.hunt._write("postings.json", data)
        st = self.set_scan_state(c["id"], at=seen_now, ok=True, error="", signed_out=False, how=how, found=len(keys),
                                 new=[p["id"] for p in new], closed=[p["id"] for p in closed], changed=[p["id"] for p in changed])
        self.hunt._changed()
        return {"found": len(keys), "new": new, "closed": closed, "changed": changed, "reopened": reopened, "scan": st}

    def failed(self, company: str, error: str, *, how: str, signed_out: bool = False) -> dict:
        c = self.hunt.company(company)
        st = self.set_scan_state(c["id"], at=_now(), ok=False, error=_text(error, 400), signed_out=signed_out, how=how)
        self.hunt._changed()
        return st

    def apply(self, ref: str, *, source: str = "window") -> dict:
        """The user goes for this posting: it becomes an application of its company, linked to it."""
        p = self.get(ref)
        if p.get("app"):
            try:
                return self.hunt.application(p["app"])
            except KeyError:
                pass
        # its location becomes the role's places ("北京/上海" is two); what can't be read so stays in the note
        loc = p.get("location") or ""
        try:
            places = [x for x in re.split(r"\s*[/,，、;；|]\s*", loc) if x]
            places = [] if not places else _places(places)
        except ValueError:
            places = []
        note = " · ".join(x for x in ("" if places else loc, p.get("category")) if x)
        try:
            # its closing date only when the site gives one: none given is none known, not 招满即止
            a = self.hunt.add_application(p["company"], {"role": p["title"], "url": p.get("url") or "", "note": note, "places": places,
                                                         **({"apply_by": p["deadline"]} if p.get("deadline") else {})}, source=source)
        except ValueError:   # the same role is on the list already: link to it
            a = next(x for x in self.hunt.applications(p["company"]) if _key(x["role"]) == _key(p["title"]))
        with self.hunt.lock:
            data = self._data()
            me = next(x for x in data["postings"] if x["id"] == p["id"])
            me["app"] = a["id"]
            self.hunt._write("postings.json", data)
        self.hunt._changed()
        return a

    def summary(self) -> dict:
        """Per company: open postings, how many match the keywords, what the last scan brought (for the table)."""
        d = self.scans()
        kw = d["keywords"]
        out: dict[str, dict] = {}
        for p in self._data()["postings"]:
            s = out.setdefault(p["company"], {"open": 0, "matching": 0})
            if not p.get("closed"):
                s["open"] += 1
                if matches(p["title"], kw):
                    s["matching"] += 1
        titles = {p["id"]: p["title"] for p in self._data()["postings"]}
        for cid, st in d["companies"].items():
            s = out.setdefault(cid, {"open": 0, "matching": 0})
            s["scan"] = {k: st.get(k) for k in ("at", "ok", "error", "signed_out", "how", "found")}
            s["scan"]["recorded"] = bool(st.get("route"))
            # news counts like the open roles beside it: only the ones the user looks for
            s["news"] = {k: sum(1 for i in st.get(k) or [] if i in titles and matches(titles[i], kw)) for k in ("new", "closed", "changed")}
        return out

    def changes(self, since_hours: float = 24) -> list[dict]:
        """What the scans of the last day brought, per company: matching new, closed and changed postings."""
        d = self.scans()
        kw = d["keywords"]
        by_id = {p["id"]: p for p in self._data()["postings"]}
        cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - since_hours * 3600))
        out = []
        for cid, st in d["companies"].items():
            if not st.get("at") or st["at"] < cut:
                continue
            row = {"company": cid, "at": st["at"]}
            for k in ("new", "closed", "changed"):
                row[k] = [by_id[i] for i in st.get(k) or [] if i in by_id and matches(by_id[i]["title"], kw)]
            if any(row[k] for k in ("new", "closed", "changed")):
                out.append(row)
        return out

    def drop_company(self, cid: str) -> None:
        with self.hunt.lock:
            data = self._data()
            data["postings"] = [p for p in data["postings"] if p["company"] != cid]
            self.hunt._write("postings.json", data)
            d = self.scans()
            d["companies"].pop(cid, None)
            self._save_scans(d)

