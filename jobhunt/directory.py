"""The employer directory (公司库): every employer of the open campus-employers database, shipped with the app and
kept up to date while it runs (2026-11-20).

The database is its own public repository (github.com/RuiquanQiao/campus-employers). Its compact per-country files,
`docs/data/<cc>.json` ({"fields": [...], "rows": [[...], ...]}) and `docs/data/index.json` ({"updated", "countries":
[{code, employers, careers, system, sha256?}]}), are copied into jobhunt/data/directory/ by scripts/sync_directory.py,
so a fresh install has every employer offline. Once a day at most, in the background, the app reads index.json on
GitHub; when it is newer, the country files that changed go into the data folder's cache (<data>/.cache/directory/).
The newer of the two copies is the one read, one country at a time.

The directory is read-only and kept apart from the user's own company list: thousands of employers on that list
would bury the few the user applies to. A row is added to the list by the user (the window's 添加, `hunt.py
directory --add`), mapped by `to_company` into the fields Hunt.add_company takes.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import natures

BUNDLED = Path(__file__).resolve().parent / "data" / "directory"
REMOTE = "https://raw.githubusercontent.com/RuiquanQiao/campus-employers/main/docs/data/"
FIELDS = ["name", "full_name", "aliases", "nature", "industry", "sub", "parent", "hq", "cities", "listed", "scale",
          "website", "campus_site", "system", "hires", "roles", "notes"]
CHECK_EVERY = 24 * 3600   # seconds between two looks at GitHub
PAGE = 50                 # rows per page in the window


def valid_index(v: Any) -> bool:
    return (isinstance(v, dict) and isinstance(v.get("updated"), str) and isinstance(v.get("countries"), list)
            and all(isinstance(c, dict) and isinstance(c.get("code"), str) and c["code"].isalpha() and len(c["code"]) == 2
                    for c in v["countries"]))


def valid_country(v: Any) -> bool:
    return (isinstance(v, dict) and isinstance(v.get("fields"), list) and "name" in v["fields"]
            and isinstance(v.get("rows"), list) and all(isinstance(r, list) for r in v["rows"]))


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fetch(url: str) -> bytes:
    """GitHub raw, with the patience a far site gets (raw.githubusercontent.com is slow or cut off in mainland China)."""
    from . import patience

    def call(timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "ResumeStudio-directory"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    return patience.ask(url, call, patience.of(url, 30, 2, (5,)), net=(urllib.error.URLError, TimeoutError, OSError),
                        answered=(urllib.error.HTTPError,), answered_is_final=True)


# ---- one employer, as the user's company list keeps one -------------------------------------------------------

def nature_of(code: str, parent: str = "") -> str:
    """The database's nature as the app's own (jobhunt/natures.py). China's codes are the app's; another country's
    `<cc>.company` is a private company when it has no parent (a subsidiary may be a foreign group's arm, which is
    left for the AI to fill rather than guessed), `<cc>.state` a state-owned enterprise, `<cc>.government`
    government, `<cc>.research` and `<cc>.university` university / research, `<cc>.nonprofit` non-profit."""
    code = str(code or "").strip().lower()
    if code in natures.IDS:
        return code
    cc, _, kind = code.partition(".")
    if f"{cc}.{kind}" in natures.SUBTYPE:
        return f"{cc}.{kind}"
    return {"company": "" if str(parent or "").strip() else "private", "state": "soe", "government": "government",
            "research": "academic", "university": "academic", "nonprofit": "nonprofit"}.get(kind, "")


def to_company(rec: dict) -> dict:
    """The fields Hunt.add_company takes for a directory record: its name and other names (the registered name
    among them, so the list knows it by that too), industry, nature, headquarters and the cities it hires in, its
    careers site with the system behind it, its homepage, and its notes (who may apply: citizenship, work rights)."""
    name = str(rec.get("name") or "").strip()
    aliases = [str(x).strip() for x in [rec.get("full_name"), *(rec.get("aliases") or [])] if str(x or "").strip()]
    aliases = [a for a in dict.fromkeys(aliases) if a.casefold() != name.casefold()][:12]
    out: dict = {"name": name, "aliases": aliases}
    if rec.get("industry"):
        out["industry"] = str(rec["industry"])
    nat = nature_of(rec.get("nature") or "", rec.get("parent") or "")
    if nat:
        out["nature"] = nat
    if rec.get("hq"):
        out["hq"] = str(rec["hq"])
    cities = [str(x) for x in rec.get("cities") or [] if str(x or "").strip()]
    if cities:
        out["places"] = cities
    if rec.get("campus_site"):
        out["sites"] = [{"url": str(rec["campus_site"]), "kind": "campus", "system": str(rec.get("system") or "")}]
    if rec.get("website"):
        out["homepage"] = str(rec["website"])
    if rec.get("notes"):
        out["note"] = str(rec["notes"])
    return out


def add(hunt: Any, rec: dict, *, different: bool = False) -> dict:
    """Adds a directory record to the user's list. A headquarters or city the place list can't read is left out
    (the company still goes in); a name on the list already raises SameCompany, as any new company does."""
    fields = to_company(rec)
    if different:
        fields["different"] = True
    from .store import _place
    for k in ("hq", "places"):
        vals = [fields[k]] if k == "hq" and k in fields else fields.get(k) or []
        ok = []
        for v in vals:
            try:
                _place(v)
                ok.append(v)
            except ValueError:
                continue
        if k == "hq":
            if ok:
                fields["hq"] = ok[0]
            else:
                fields.pop("hq", None)
        elif ok:
            fields["places"] = ok
        else:
            fields.pop("places", None)
    return hunt.add_company(fields)


def _key(s: str) -> str:
    from .store import _key as k
    return k(s)


# ---- the copies: shipped, and downloaded ----------------------------------------------------------------------

class Directory:
    def __init__(self, cache_dir: Path | None, *, bundled: Path = BUNDLED, remote: str = REMOTE,
                 fetch: Callable[[str], bytes] = _fetch, log: Callable[[str], None] | None = None):
        self.bundled = Path(bundled)
        self.cache = Path(cache_dir) / "directory" if cache_dir else None
        self.remote = remote
        self.fetch = fetch
        self.log = log or self._log_file
        self.lock = threading.Lock()
        self._rows: dict[str, list[dict]] = {}   # code -> records, read once
        self._index: dict | None = None
        self.checking = False

    def _log_file(self, msg: str) -> None:
        """A failed update is written down quietly, in the cache beside the copy (the last 50 lines)."""
        if not self.cache:
            return
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            p = self.cache / "update.log"
            old = p.read_text(encoding="utf-8").splitlines()[-49:] if p.exists() else []
            p.write_text("\n".join([*old, time.strftime("%Y-%m-%d %H:%M:%S ") + msg]) + "\n", encoding="utf-8")
        except OSError:
            pass

    # which copy: the newer index, and for each country the file that copy has
    def _indexes(self) -> tuple[dict | None, dict | None]:
        b = _read_json(self.bundled / "index.json")
        c = _read_json(self.cache / "index.json") if self.cache else None
        return (b if valid_index(b) else None), (c if valid_index(c) else None)

    def index(self) -> dict:
        with self.lock:
            if self._index is None:
                b, c = self._indexes()
                self._index = c if c and (not b or c["updated"] > b["updated"]) else b or {"updated": "", "countries": []}
            return self._index

    def _file(self, code: str) -> Path | None:
        b, c = self._indexes()
        use_cache = c and (not b or c["updated"] > b["updated"])
        if use_cache and self.cache and (self.cache / f"{code}.json").exists():
            return self.cache / f"{code}.json"
        p = self.bundled / f"{code}.json"
        return p if p.exists() else None

    def rows(self, code: str) -> list[dict]:
        code = code.lower()
        with self.lock:
            if code in self._rows:
                return self._rows[code]
        p = self._file(code)
        raw = _read_json(p) if p else None
        out: list[dict] = []
        if valid_country(raw):
            fs = raw["fields"]
            for i, r in enumerate(raw["rows"]):
                rec = {f: (r[j] if j < len(r) else None) for j, f in enumerate(fs)}
                if not str(rec.get("name") or "").strip():
                    continue
                rec["country"], rec["id"] = code, f"{code}-{i}"
                names = [rec["name"], rec.get("full_name") or "", *(rec.get("aliases") or [])]
                rec["_find"] = " ".join(str(x) for x in [*names, rec.get("industry"), rec.get("sub"), rec.get("hq"),
                                                         *(rec.get("cities") or [])] if x).casefold()
                rec["_keys"] = {k for k in (_key(str(n)) for n in names if n) if k}
                out.append(rec)
        with self.lock:
            self._rows[code] = out
        return out

    def all_rows(self) -> list[dict]:
        return [r for c in self.index()["countries"] for r in self.rows(c["code"])]

    def get(self, rid: str) -> dict:
        code = str(rid or "").split("-")[0].lower()
        if len(code) == 2:
            for r in self.rows(code):
                if r["id"] == rid:
                    return r
        raise KeyError(rid)

    # ---- finding ----
    def search(self, q: str = "", *, country: str = "", industry: str = "", nature: str = "", careers: bool = False,
               offset: int = 0, limit: int = PAGE) -> dict:
        """Rows matching every filter, and the counts the filters offer for what the others leave."""
        codes = [c["code"] for c in self.index()["countries"]]
        country = country.lower() if country.lower() in codes else ""
        words = [w for w in str(q or "").casefold().split() if w]
        base = [r for code in ([country] if country else codes) for r in self.rows(code)
                if all(w in r["_find"] for w in words) and (not careers or r.get("campus_site"))]
        nat = lambda r: nature_of(r.get("nature") or "", r.get("parent") or "")  # noqa: E731
        hits = [r for r in base if (not industry or r.get("industry") == industry) and (not nature or nat(r) == nature)]
        inds: dict[str, int] = {}
        for r in base:
            if r.get("industry") and (not nature or nat(r) == nature):
                inds[r["industry"]] = inds.get(r["industry"], 0) + 1
        nats: dict[str, int] = {}
        for r in base:
            if not industry or r.get("industry") == industry:
                n = nat(r)
                nats[n] = nats.get(n, 0) + 1
        offset = max(0, int(offset or 0))
        return {"total": len(hits), "offset": offset, "rows": hits[offset:offset + max(1, min(int(limit or PAGE), 200))],
                "industries": sorted(inds.items(), key=lambda x: (-x[1], x[0])), "natures": nats}

    def matches(self, q: str, limit: int = 10) -> list[dict]:
        """For the AI's command: a whole name or alias first, then rows that contain every word."""
        k = _key(q)
        rows = self.all_rows()
        exact = [r for r in rows if k and k in r["_keys"]]
        return (exact or self.search(q, limit=limit)["rows"])[:limit]

    # ---- keeping up to date ----
    def _stamp(self) -> Path | None:
        return self.cache / "checked" if self.cache else None

    def due(self, now: float | None = None) -> bool:
        s = self._stamp()
        if not s:
            return False
        try:
            last = float(s.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            last = 0
        return (now or time.time()) - last >= CHECK_EVERY

    def check(self, *, force: bool = False) -> dict:
        """Reads index.json on GitHub (at most once a day unless `force`); when it is newer than the copy in use,
        downloads the country files that changed into the cache. Returns what happened; a failure is logged, never
        raised."""
        if not self.cache or (not force and not self.due()):
            return {"checked": False}
        self.checking = True
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            self._stamp().write_text(str(time.time()), encoding="utf-8")
            remote = json.loads(self.fetch(self.remote + "index.json").decode("utf-8"))
            if not valid_index(remote):
                raise ValueError("index.json is not in the expected shape")
            cur = self.index()
            if remote["updated"] <= (cur.get("updated") or ""):
                return {"checked": True, "updated": False}
            was = {c["code"]: c for c in cur.get("countries") or []}
            got: dict[str, bytes] = {}
            for c in remote["countries"]:
                old = was.get(c["code"])
                if old and c.get("sha256") and old.get("sha256") == c["sha256"] and self._file(c["code"]):
                    continue   # this country's file is the one in hand
                data = self.fetch(self.remote + f"{c['code']}.json")
                if c.get("sha256") and hashlib.sha256(data).hexdigest() != c["sha256"]:
                    raise ValueError(f"{c['code']}.json does not match its sha256")
                if not valid_country(json.loads(data.decode("utf-8"))):
                    raise ValueError(f"{c['code']}.json is not in the expected shape")
                got[c["code"]] = data
            # the files a newer index keeps from the bundle are copied too, so the cache is one whole copy
            for c in remote["countries"]:
                if c["code"] not in got:
                    p = self._file(c["code"])
                    if p and p.parent != self.cache:
                        got[c["code"]] = p.read_bytes()
            for code, data in got.items():
                tmp = self.cache / f"{code}.json.tmp"
                tmp.write_bytes(data)
                tmp.replace(self.cache / f"{code}.json")
            tmp = self.cache / "index.json.tmp"
            tmp.write_text(json.dumps(remote, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache / "index.json")   # last: the files it names are all in place
            with self.lock:
                self._rows.clear()
                self._index = None
            return {"checked": True, "updated": True, "countries": sorted(got)}
        except Exception as e:  # noqa: BLE001  (offline, GitHub cut off, a file half-written upstream)
            self.log(f"employer directory: no update ({type(e).__name__}: {e})")
            return {"checked": True, "error": str(e)}
        finally:
            self.checking = False

    def check_later(self, delay: float = 20.0, on_update: Callable[[], None] | None = None) -> None:
        """The day's check off the start, on a thread of its own: the window never waits for it."""
        import os
        if os.environ.get("STUDIO_DIRECTORY_UPDATE", "on") == "off" or not self.due():
            return

        def run() -> None:
            time.sleep(delay)
            r = self.check()
            if r.get("updated") and on_update:
                on_update()
        threading.Thread(target=run, daemon=True, name="hunt-directory-update").start()


def public(rec: dict) -> dict:
    """A record for the window or the AI: no search keys."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}
