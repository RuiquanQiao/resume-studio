"""Open postings: what each company's careers sites list now, found by scanning, never typed (docs/JOB_HUNT.md, 2026-10-18).

    resume-studio-data/hunt/postings.json   {"next": 9, "postings": [{id: "P1", company: "C1", key, title, url, location, category,
                                  batch, deadline, posted, first_seen, last_seen, closed, changed, app, source}]}
                                  one posting a line, its empty fields (and a key that is its link) left out
    resume-studio-data/hunt/scans.json      {"last_all": "...", "keywords": ["算法", "AI", ...], "daily": true,
                                  "companies": {"C1": {at, ok, error, how, found, new, closed, changed, route}}}

A posting is matched across scans by its link (or, without one, its title and location), after career-ops'
listing-fingerprint idea: a posting seen for the first time is new, one no longer listed is closed, a changed
deadline is kept as `changed`. Postings are not applications: "Apply" turns one into an application of its
company (and links them), so a company listing dozens of roles doesn't flood the progress table. A role added
another way is linked to its posting by its link (at once), or by its name (at the next scan). A linked role follows its posting: closed on the
site, a role not applied to yet becomes "posting closed" (one further on gets a timeline line); back on the site,
it is "want to apply" again (jobhunt/store.py).

`keywords` are the roles the user looks for (career-ops' title filter): postings whose title has none of them
are kept but folded away in the window, and don't count as news.

Still listed is not always still open: a company's job system takes a filled role down, but a university's or a
state employer's announcement list is an archive that keeps every one. So a posting whose closing date has passed,
or that has none and was posted over a year ago, is `stale`: kept and shown folded away, never counted as open.
"""
from __future__ import annotations

import functools
import json
import re
import threading
import unicodedata
import time
from typing import Any, Callable
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


_TRACKING = re.compile(r"^(utm_\w*|spm|from|source|share\w*|fbclid|gclid)$", re.I)


def same_link(url: str) -> str:
    """A link as two links to the same posting compare: tracking added when it was shared left out. Only for
    comparing; a posting's key keeps the whole link, so earlier scans still match."""
    k = _norm_url(url or "")
    try:
        p = urlsplit(k)
    except ValueError:
        return k
    q = "&".join(x for x in p.query.split("&") if x and not _TRACKING.match(x.split("=", 1)[0]))
    return urlunsplit((p.scheme, p.netloc, p.path, q, p.fragment))


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


def stale(p: dict, today: str = "") -> str:
    """Why a posting the site still lists is probably over: "deadline" (its closing date has passed) or "old" (no
    closing date, posted more than a year ago); "" while it looks open."""
    today = today or time.strftime("%Y-%m-%d")
    if p.get("deadline") and p["deadline"] < today:
        return "deadline"
    year_ago = f"{int(today[:4]) - 1}{today[4:10]}"
    if not p.get("deadline") and p.get("posted") and p["posted"] < year_ago:
        return "old"
    return ""


# A keyword is matched against a title the way career-ops' title filter matches one (title-keywords.mjs), so the two
# never disagree: case and accents folded; a 2-3 letter acronym ("AI", "ML") only as a whole word, where a Chinese,
# Japanese or Korean character next to it is a word's edge ("AI工程师" has AI, "Maintenance" and "Detail" don't);
# anything else as a part of the title. "AI+产品" asks for both.
_CJK = "぀-ヿ㐀-鿿가-힯豈-﫿"
_WORD = "[^\\W" + _CJK + "]"


def _fold(s: str) -> str:
    s = str(s or "")
    if s.isascii():   # nothing to take apart: most English titles, and the cost of the first look at 66,000 titles
        return " ".join(s.split()).casefold()
    s = unicodedata.normalize("NFD", s)   # split() below takes no-break spaces too
    return " ".join("".join(ch for ch in s if not unicodedata.combining(ch)).split()).casefold()


@functools.lru_cache(maxsize=4096)
def _keyword(keyword: str) -> tuple | None:
    """A keyword taken apart once: each part folded, a 2-3 letter acronym as its whole-word pattern. None: a part
    that folds to nothing, so the keyword hits no title."""
    out = []
    for x in [x for x in re.split(r"\s*\+\s*", keyword) if x.strip()] or [keyword]:
        w = _fold(x)
        if not w:
            return None
        out.append(re.compile("(?<!" + _WORD + ")" + re.escape(w) + "(?!" + _WORD + ")") if re.fullmatch(r"[a-z]{2,3}", w) else w)
    return tuple(out)


def _hits(folded: str, keyword: str) -> bool:
    parts = _keyword(keyword)
    return parts is not None and all((x in folded) if isinstance(x, str) else x.search(folded) is not None for x in parts)


def keyword_hits(title: str, keyword: str) -> bool:
    return _hits(_fold(title), keyword)


@functools.lru_cache(maxsize=256)
def _any_of(keywords: tuple) -> Callable[[str], bool]:
    """Whether a folded title hits any of these keywords, as one test: the one-part keywords joined into one pattern,
    searched once, and the "AI+产品" ones part by part. Testing each keyword alone made 1.5 million calls for 66,000
    titles and 3 tracks, 3 s every time the postings changed (2026-11-12)."""
    one, many = [], []
    for k in keywords:
        parts = _keyword(k) if k else None
        if parts is None:
            continue
        if len(parts) == 1:
            one.append(parts[0].pattern if not isinstance(parts[0], str) else re.escape(parts[0]))
        else:
            many.append(parts)
    rx = re.compile("|".join(one)) if one else None

    def test(folded: str) -> bool:
        if rx is not None and rx.search(folded):
            return True
        return any(all((x in folded) if isinstance(x, str) else x.search(folded) is not None for x in parts) for parts in many)
    return test


def _matches(folded: str, keywords: Any, exclude: Any) -> bool:
    if exclude and _any_of(tuple(exclude))(folded):
        return False
    if not keywords:
        return True
    return _any_of(tuple(keywords))(folded)


def matches(title: str, keywords: list[str], exclude: list[str] | None = None) -> bool:
    """Whether a title is one the user looks for: no keywords is every title; an excluded word rules it out."""
    return _matches(_fold(title), keywords, exclude)


def _words(v: Any, limit: int = 40) -> list[str]:
    xs = re.split(r"[,，、;；\n]", v) if isinstance(v, str) else v if isinstance(v, list) else []
    return [k for k in dict.fromkeys(_text(x, 40) for x in xs) if k][:limit]


def clean_tracks(v: Any) -> list[dict]:
    """The kinds of role the user goes for, each its own filter (2026-11-04: someone may go for an engineering and a
    product role at once): {id, name, keywords, exclude, subset}. `subset` is the resume that kind of role goes out with."""
    rows = [x for x in (v if isinstance(v, list) else []) if isinstance(x, dict)]
    taken = {str(x.get("id")) for x in rows if re.fullmatch(r"T\d+", str(x.get("id") or ""))}
    out, used = [], set()
    for x in rows:
        name, kws = _text(x.get("name"), 60), _words(x.get("keywords"))
        if not name and not kws:
            continue
        tid = str(x.get("id") or "")
        if not re.fullmatch(r"T\d+", tid) or tid in used:
            tid = next(f"T{i}" for i in range(1, 999) if f"T{i}" not in used and f"T{i}" not in taken)
            taken.add(tid)
        used.add(tid)
        out.append({"id": tid, "name": name, "keywords": kws, "exclude": _words(x.get("exclude")),
                    "subset": _text(x.get("subset"), 40)})
    return out[:40]   # was 12 (2026-11-13): a student weighing a dozen kinds of role hit the wall


# 53,000 postings are matched against the tracks on every look at the table (12 s in 2026-11 when each keyword folded
# the title again): a title's tracks are kept per set of tracks, the newest few sets only
_TRACKED: dict[tuple, dict[str, tuple[str, ...]]] = {}
_TRACKED_LOCK = threading.Lock()


_LAST_TRACKS: list = [None, None]   # [the list asked about last, its key]: the same list 53,000 times in a row


def _tracks_key(tracks: list[dict]) -> tuple:
    last = _LAST_TRACKS
    if last[0] is tracks:
        return last[1]
    key = tuple((tr["id"], tuple(tr["keywords"]), tuple(tr.get("exclude") or ())) for tr in tracks)
    _LAST_TRACKS[:] = [tracks, key]
    return key


def tracks_of(title: str, tracks: list[dict]) -> list[str]:
    """The tracks a title belongs to (their ids); a track with no keywords takes none."""
    key = _tracks_key(tracks)
    seen = _TRACKED.get(key)
    if seen is None:
        with _TRACKED_LOCK:
            seen = _TRACKED.get(key)
            if seen is None:
                while len(_TRACKED) >= 4:
                    _TRACKED.pop(next(iter(_TRACKED)))
                seen = _TRACKED[key] = {}
    got = seen.get(title)
    if got is None:
        folded = _fold(title)
        got = tuple(tr["id"] for tr in tracks if tr["keywords"] and _matches(folded, tr["keywords"], tr["exclude"]))
        if len(seen) < 200_000:
            seen[title] = got
    return list(got)


def wanted(title: str, tracks: list[dict]) -> bool:
    """No track has keywords yet: every title counts. Else only those in one."""
    return not any(tr["keywords"] for tr in tracks) or bool(tracks_of(title, tracks))


# A posting's fields that most postings leave empty: written only when they hold something, and the key only when
# it isn't the link (it is for 9 postings in 10). Read back, every posting has them all again.
DEFAULTS: dict[str, Any] = {"url": "", "location": "", "category": "", "batch": "", "deadline": "", "posted": "",
                            "closed": "", "changed": {}, "app": None, "source": ""}


def _full(p: dict) -> dict:
    for k, v in DEFAULTS.items():
        if k not in p:
            p[k] = {} if k == "changed" else v
    if "key" not in p:
        p["key"] = fingerprint(p) if not p["url"] else p["url"]
    return p


def _slim(p: dict) -> dict:
    return {k: v for k, v in p.items()
            if not (k in DEFAULTS and v == DEFAULTS[k]) and not (k == "key" and v == p.get("url") and v)}


class Postings:
    """postings.json holds every posting a scan ever found (53,000 for 80 companies in 2026-11): read once and kept
    while the file stays as it is, for what only reads it (the table, a company's page, the news); what writes
    reads it fresh, under the tracker's lock. Each read hands out copies, so nothing changes the kept one."""

    def __init__(self, hunt: Hunt):
        self.hunt = hunt
        self._kept: tuple[tuple[int, int], dict] | None = None
        self._scans_kept: tuple[tuple[int, int], dict] | None = None
        self._summary_kept: tuple | None = None
        self._kept_lock = threading.Lock()

    # ---- files ----
    def _sig(self) -> tuple[int, int]:
        try:
            st = self.hunt._file("postings.json").stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    def _data(self) -> dict:
        """Fresh, for a change that is written back (_save): a copy of the kept view while the file is as it was
        (each scan wrote back after parsing 24 MB again: a second a company), else read from the file."""
        sig = self._sig()
        with self._kept_lock:
            kept = self._kept if self._kept and self._kept[0] == sig and sig != (0, 0) else None
        if kept:
            v = kept[1]
            return {**{k: x for k, x in v.items() if k != "postings"},
                    "postings": [{**p, "changed": dict(p["changed"])} for p in v["postings"]]}
        data = self.hunt._read_rows("postings.json", "postings")
        for p in data["postings"]:
            _full(p)
        return data

    def _save(self, data: dict) -> None:
        rows = [_slim(p) for p in data["postings"]]
        self.hunt._write("postings.json", {"next": data["next"], "postings": rows}, rows="postings")
        # what was just written is the new view, without parsing 24 MB again (copies: the caller may go on with `data`)
        kept = {"next": data["next"], "postings": [_full({**x, "changed": dict(x["changed"])} if "changed" in x else dict(x)) for x in rows]}
        try:
            st = self.hunt._file("postings.json").stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        with self._kept_lock:
            self._kept = (sig, kept) if sig else None

    def _view(self) -> dict:
        """The postings as the file holds them now, parsed once per change of the file. Never changed by a caller."""
        sig = self._sig()
        with self._kept_lock:
            if self._kept and self._kept[0] == sig:
                return self._kept[1]
        data = self._data()
        with self._kept_lock:
            self._kept = (sig, data)
        return data

    def slim_file(self) -> bool:
        """A file written before 2026-11-04 (indented, every empty field) is written again the short way, once."""
        path = self.hunt._file("postings.json")
        try:
            with path.open("rb") as f:
                head = f.read(64)
        except OSError:
            return False
        if not head.strip() or b'"postings":[\n' in head:   # none yet, or written the short way already
            return False
        with self.hunt.lock:
            self._save(self._data())
        return True

    def scans(self) -> dict:
        """scans.json as it is now, parsed once per change of the file (the table asks for it once per company:
        200 parses of 700 KB a look). Shared: never changed by a caller; a change goes through _fresh_scans."""
        try:
            st = self.hunt._file("scans.json").stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = (0, 0)
        with self._kept_lock:
            if self._scans_kept and self._scans_kept[0] == sig:
                return self._scans_kept[1]
        d = self._fresh_scans()
        with self._kept_lock:
            self._scans_kept = (sig, d)
        return d

    def _fresh_scans(self) -> dict:
        """Read from the file, for a change that is written back (under the tracker's lock)."""
        try:
            d = json.loads(self.hunt._load("scans.json") or "{}")
        except ValueError:
            d = {}
        d = d if isinstance(d, dict) else {}
        d.setdefault("companies", {})
        d.setdefault("keywords", [])
        if "tracks" not in d:   # one list of keywords before tracks (2026-11-04): it becomes the first track
            d["tracks"] = [{"id": "T1", "keywords": d["keywords"]}] if d["keywords"] else []
        d["tracks"] = clean_tracks(d["tracks"])
        d.setdefault("daily", True)
        d.setdefault("last_all", "")
        return d

    def _save_scans(self, d: dict) -> None:
        self.hunt._write("scans.json", d)
        with self._kept_lock:
            self._scans_kept = None

    def settings(self, keywords: Any = None, daily: Any = None, tracks: Any = None) -> dict:
        """`tracks` sets the whole list; `keywords` (the one list before tracks) the first track's keywords."""
        with self.hunt.lock:
            d = self._fresh_scans()
            if tracks is not None:
                d["tracks"] = clean_tracks(tracks)
            elif keywords is not None:
                kws = _words(keywords)
                d["tracks"] = clean_tracks([{**d["tracks"][0], "keywords": kws}, *d["tracks"][1:]] if d["tracks"]
                                           else [{"keywords": kws}])
            # every track's keywords together: what older readers of `keywords` (the scan's news) still go by
            d["keywords"] = list(dict.fromkeys(k for tr in d["tracks"] for k in tr["keywords"]))
            if daily is not None:
                d["daily"] = bool(daily)
            self._save_scans(d)
        self.hunt._changed()
        return {"keywords": d["keywords"], "tracks": d["tracks"], "daily": d["daily"]}

    def tracks(self) -> list[dict]:
        return self.scans()["tracks"]

    def scan_state(self, cid: str) -> dict:
        return self.scans()["companies"].get(cid) or {}

    def set_scan_state(self, cid: str, **fields: Any) -> dict:
        with self.hunt.lock:
            d = self._fresh_scans()
            st = {**(d["companies"].get(cid) or {}), **fields}
            d["companies"][cid] = st
            self._save_scans(d)
        return st

    def mark_all_scanned(self) -> None:
        with self.hunt.lock:
            d = self._fresh_scans()
            d["last_all"] = _now()
            self._save_scans(d)

    def route(self, cid: str) -> dict | None:
        return self.scan_state(cid).get("route")

    # ---- postings ----
    def all(self, company: str | None = None) -> list[dict]:
        ps = self._view()["postings"]
        if company:
            cid = self.hunt.company(company)["id"]
            ps = [p for p in ps if p["company"] == cid]
        return [{**p, "changed": dict(p["changed"])} for p in ps]

    def get(self, ref: str) -> dict:
        p = next((x for x in self._view()["postings"] if x["id"] == str(ref).strip()), None)
        if not p:
            raise KeyError(t("No posting “{ref}”", ref=ref))
        return {**p, "changed": dict(p["changed"])}

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
            new, changed, reopened, keys = [], [], [], set()
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
                    p["closed"] = ""
                    reopened.append(p)
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
            # a posting the user already applies to (added by hand or by Claude) is linked to that application:
            # by its link, else by the same name
            mine_apps = self.hunt.applications(c["id"])
            by_url = {_norm_url(a["url"]): a["id"] for a in mine_apps if re.match(r"https?://", a.get("url") or "", re.I)}
            by_name = {_key(a["role"]): a["id"] for a in mine_apps}
            closed = []
            if complete:
                for k, p in mine.items():
                    if k not in keys and not p.get("closed"):
                        p["closed"] = seen_now
                        closed.append(p)
            linked = {p["app"] for p in data["postings"] if p.get("app")}
            gone = []   # linked only now to a posting this read found gone: the role follows it too
            for p in data["postings"]:
                if p["company"] != c["id"] or p.get("app"):
                    continue
                aid = by_url.get(p["key"]) or by_name.get(_key(p["title"]))
                if aid and aid not in linked:
                    p["app"] = aid
                    linked.add(aid)
                    if complete and p.get("closed") and p not in closed:
                        gone.append(p)
            self._save(data)
        st = self.set_scan_state(c["id"], at=seen_now, ok=True, error="", unread=None, missed=None, signed_out=False, how=how, found=len(keys),
                                 new=[p["id"] for p in new], closed=[p["id"] for p in closed], changed=[p["id"] for p in changed])
        follow = self._follow(c, closed + gone, reopened)
        self.hunt._changed()
        return {"found": len(keys), "new": new, "closed": closed, "changed": changed, "reopened": len(reopened),
                "roles_closed": follow, "scan": st}

    def _follow(self, c: dict, closed: list[dict], reopened: list[dict]) -> list[str]:
        """The roles linked to postings that just closed or came back follow them. Returns the roles set to closed."""
        out = []
        for p in closed:
            try:
                a = self.hunt.application(p["app"]) if p.get("app") else None
            except KeyError:
                a = None
            if not a or a["status"] in ("closed", "withdrawn", "rejected", "hired", "offer"):
                continue
            if a["status"] == "saved":
                self.hunt.set_status(a["id"], "closed", source="scan",
                                     note=t("The posting is no longer on {company}'s site", company=c["name"]))
                out.append(a["id"])
            else:   # applied or further: a posting taken down once it has enough applicants says nothing about this one
                self.hunt.add_note(a["id"], t("The posting is no longer on {company}'s site", company=c["name"]), source="scan")
        for p in reopened:
            try:
                a = self.hunt.application(p["app"]) if p.get("app") else None
            except KeyError:
                a = None
            if a and a["status"] == "closed":
                self.hunt.set_status(a["id"], "saved", source="scan",
                                     note=t("The posting is back on {company}'s site", company=c["name"]))
        return out

    def failed(self, company: str, error: str, *, how: str, signed_out: bool = False) -> dict:
        c = self.hunt.company(company)
        st = self.set_scan_state(c["id"], at=_now(), ok=False, error=_text(error, 400), unread=None, missed=None, signed_out=signed_out, how=how)
        self.hunt._changed()
        return st

    def by_url(self, url: str) -> dict | None:
        """The posting at this link, of any company: a posting's link is its identity (user, 2026-11-01)."""
        k = same_link(url)
        if not re.match(r"https?://", k, re.I):
            return None
        p = next((p for p in self._view()["postings"] if not p["key"].startswith("t:") and same_link(p["key"]) == k), None)
        return {**p, "changed": dict(p["changed"])} if p else None

    def link(self, a: dict) -> None:
        """A role just given a link that a scan already found is linked to that posting now, not at the next scan."""
        p = self.by_url(a.get("url") or "")
        if not p or p["company"] != a["company"] or p.get("app"):
            return
        with self.hunt.lock:
            data = self._data()
            if any(x.get("app") == a["id"] for x in data["postings"]):
                return
            me = next(x for x in data["postings"] if x["id"] == p["id"])
            me["app"] = a["id"]
            self._save(data)
        self.hunt._changed()

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
            self._save(data)
        self.hunt._changed()
        return a

    def summary(self) -> dict:
        """Per company: open postings, how many match the keywords, what the last scan brought (for the table).
        Stale ones (still listed, probably over) count apart, never as open. Counted again only when the postings,
        the scans or the day change; each company's row is the caller's to add to."""
        d = self.scans()
        view = self._view()
        sig = (id(view), id(d), time.strftime("%Y-%m-%d"))
        kept = self._summary_kept
        if not kept or kept[0] != sig:
            kept = self._summary_kept = (sig, self._summary(d, view["postings"]), (view, d))   # holds them: ids stay theirs
        return {cid: dict(s) for cid, s in kept[1].items()}

    def _summary(self, d: dict, ps: list[dict]) -> dict:
        trs = d["tracks"]
        out: dict[str, dict] = {}
        today = time.strftime("%Y-%m-%d")
        for p in ps:
            s = out.setdefault(p["company"], {"open": 0, "matching": 0, "stale": 0, "tracks": {}})
            if p.get("closed"):
                continue
            if stale(p, today):
                s["stale"] += 1
                continue
            s["open"] += 1
            mine = tracks_of(p["title"], trs)
            for tid in mine:
                s["tracks"][tid] = s["tracks"].get(tid, 0) + 1
            if mine or not any(tr["keywords"] for tr in trs):
                s["matching"] += 1
        news = {p["id"]: p for p in ps}
        for cid, st in d["companies"].items():
            s = out.setdefault(cid, {"open": 0, "matching": 0, "stale": 0, "tracks": {}})
            s["scan"] = {k: st.get(k) for k in ("at", "ok", "error", "signed_out", "how", "found", "short")}
            s["scan"]["recorded"] = bool(st.get("route"))
            # news counts like the open roles beside it: only the ones the user looks for, and a stale one isn't new
            s["news"] = {k: sum(1 for i in st.get(k) or [] if i in news and wanted(news[i]["title"], trs)
                                and not (k == "new" and stale(news[i], today))) for k in ("new", "closed", "changed")}
        return out

    def changes(self, since_hours: float = 24) -> list[dict]:
        """What the scans of the last day brought, per company: matching new, closed and changed postings."""
        d = self.scans()
        trs = d["tracks"]
        by_id = {p["id"]: p for p in self._view()["postings"]}
        cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - since_hours * 3600))
        out = []
        for cid, st in d["companies"].items():
            if not st.get("at") or st["at"] < cut:
                continue
            row = {"company": cid, "at": st["at"]}
            for k in ("new", "closed", "changed"):
                row[k] = [dict(by_id[i]) for i in st.get(k) or [] if i in by_id and wanted(by_id[i]["title"], trs)
                          and not (k == "new" and stale(by_id[i]))]
            if any(row[k] for k in ("new", "closed", "changed")):
                out.append(row)
        return out

    def matched(self, limit: int = 600) -> dict:
        """The open roles (not closed, not probably over) in the user's tracks, newest first, each with the tracks
        it is in; how many each track has in all. With no track set up, none: the window asks for tracks instead."""
        trs = [tr for tr in self.tracks() if tr["keywords"]]
        today = time.strftime("%Y-%m-%d")
        names = {c["id"]: c["name"] for c in self.hunt.companies()}
        out, counts = [], {tr["id"]: 0 for tr in trs}
        for p in self._view()["postings"]:
            if p.get("closed") or p["company"] not in names or stale(p, today):
                continue
            ts = tracks_of(p["title"], trs)
            if not ts:
                continue
            for x in ts:
                counts[x] += 1
            out.append({**p, "changed": dict(p["changed"]), "tracks": ts, "company_name": names[p["company"]]})
        out.sort(key=lambda p: (p.get("posted") or p.get("first_seen") or "", p.get("first_seen") or ""), reverse=True)
        return {"tracks": self.tracks(), "counts": counts, "total": len(out), "postings": out[:limit]}

    def drop_company(self, cid: str) -> None:
        with self.hunt.lock:
            data = self._data()
            data["postings"] = [p for p in data["postings"] if p["company"] != cid]
            self._save(data)
            d = self._fresh_scans()
            d["companies"].pop(cid, None)
            self._save_scans(d)

