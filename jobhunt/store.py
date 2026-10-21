"""The tracker's data: companies, applications and what happened to them (docs/JOB_HUNT.md).

Plain files next to the data file, in our own schema, so switching trackers loses nothing:

    .studio/hunt/companies.json     {"next": 3, "companies": [{id: "C1", name, aliases, site, sites, industry,
                                     strength, location, places, size, logo, sources, kept, note, account,
                                     subset, about, created, updated}]}
    .studio/hunt/applications.json  {"next": 5, "applications": [{id: "A1", company: "C1", role, url, channel,
                                     platform, order, status, applied, batch, note, subset,
                                     places: [{kind: "onsite"|"hybrid"|"remote", area}],
                                     sent: [{at, subset, n}], schedule: [{id, kind?, title, at, rolling?, minutes,
                                     where, note}],
                                     co_app, created, updated}]}
    .studio/hunt/logos/C1.png       a company's logo, downloaded once (docs/JOB_HUNT.md, 2026-10-18)

A company fills itself: Claude researches it and sends every field at once (`fill`), each with its source.
A field the user edits in the window goes into `kept`, and Claude's later fills leave it alone. Deadlines
belong to roles, not to companies; a company deadline from before moves to its roles.
    .studio/hunt/events.tsv         at  app  kind  from  to  source  note      (append-only)

The events are a ledger, like career-ops' status-log.tsv: every status change, note and send is a line,
never edited; a correction is a new line. An application's `status` is the last status line's `to`
(kept on the application too, so the table needs no replay). Ids are never reused.

An application's `channel` is how it goes in: "site" (the company's own careers site, the 网申 sign-up:
one profile there, several roles in order of preference, `order` 1, 2, …), "platform" (`platform` names
the Autofill site: LinkedIn, BOSS直聘…), "referral" or "other". `referral` is the 内推码 the user got for
it, entered on the sign-up: empty means none.

An application's `places` say where the role is (docs/JOB_HUNT.md, 2026-10-21): one or more, since a
role is often open in several cities. A company's `places` are where it hires, the same way (Claude finds them
when it fills the company in). Both are drawn on a world map (jobhunt/geo.py). Each is on site, hybrid or remote; `area` is the city, or for
remote the region it is open to (APAC, 中国大陆…), which must be said: "不限" when it is anywhere.

An application's `schedule` holds what is booked for it (docs/JOB_HUNT.md, 2026-10-21): a test, 一面, 二面,
HR 面…, each with its date and time. The window shows them on a calendar and a Gantt chart, and
exports them as an .ics file for the user's own calendar (jobhunt/ics.py). Unlike the ledger, they
are edited and deleted freely: a rescheduled interview moves.

A role's deadline is one of them (docs/JOB_HUNT.md, 2026-10-21: the deadline is an event): the one of
kind "apply", which every new role starts with. Its `at` is when applications close, empty when no date
is known; `rolling` says it closes when the role is filled (滚动招聘, 招满即止), with no date. The
`deadline` field the window and Claude send ("2026-10-31", "rolling", "") sets it.

An application's `batch` is the user's own name for the hiring round it belongs to: 27秋招, 2027 Graduate
Program, Summer Internship 2027… Free text: what rounds there are differs by country, so none is built in.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.i18n import t

# id, label (English; the window translates it), career-ops' state id (for copying rows there)
STATES = [
    ("saved", "Want to apply", "evaluated"),
    ("applied", "Applied", "applied"),
    ("assessment", "Tests", "assessment"),        # 测评, 笔试, online assessment
    ("interview", "Interviewing", "interview"),   # rounds are events: 一面, 二面, HR 面
    ("offer", "Offer", "offer"),
    ("hired", "Hired", "hired"),
    ("rejected", "Not successful", "rejected"),
    ("withdrawn", "Withdrawn", "discarded"),
]
STATE_IDS = [s[0] for s in STATES]
OPEN = {"saved", "applied", "assessment", "interview"}
TO_CAREER_OPS = {s[0]: s[2] for s in STATES}
# what people (and Claude) call them: any of these is understood
ALIASES = {
    "saved": ["want", "wishlist", "todo", "to apply", "想投", "待投递", "收藏", "未投递"],
    "applied": ["sent", "submitted", "已投递", "投递", "已投", "已申请", "网申"],
    "assessment": ["test", "tests", "oa", "online assessment", "exam", "测评", "笔试", "测试", "机试"],
    "interview": ["interviews", "interviewing", "面试", "一面", "二面", "三面", "hr面", "终面"],
    "offer": ["offer", "录用", "意向书", "oc"],
    "hired": ["accepted", "joined", "入职", "已入职", "签约", "三方"],
    "rejected": ["rejection", "declined", "fail", "failed", "拒", "被拒", "挂", "未通过", "感谢信"],
    "withdrawn": ["withdraw", "discarded", "gave up", "放弃", "不去", "撤回"],
}
CHANNELS = ["site", "platform", "referral", "other"]
# a deadline that is no date: the role closes when it is filled
ROLLING = {"rolling", "滚动", "滚动招聘", "招满即止", "招满为止", "until filled", "open until filled"}
PLACE_KINDS = ["onsite", "hybrid", "remote"]
# what people (and Claude) write before the area: "remote: APAC", "远程：中国大陆", "hybrid 悉尼"
PLACE_WORDS = {"remote": "remote", "远程": "remote", "wfh": "remote", "居家": "remote",
               "hybrid": "hybrid", "混合": "hybrid", "混合办公": "hybrid",
               "onsite": "onsite", "on-site": "onsite", "office": "onsite", "现场": "onsite", "坐班": "onsite"}
# what Claude fills on a company (the name stays the user's: an official name goes into the aliases)
FILLABLE = ["aliases", "industry", "strength", "location", "places", "size", "site", "sites", "logo", "about"]
SITE_KINDS = ["campus", "intern", "social", "other"]
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
              "image/svg+xml": "svg", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico"}
EVENT_KINDS = ["created", "status", "note", "sent"]
COLUMNS = ["at", "app", "kind", "from", "to", "source", "note"]


def _text(v: Any, limit: int = 200) -> str:
    return " ".join(str(v or "").split())[:limit]


def _long(v: Any, limit: int = 4000) -> str:
    """Text that may keep its line breaks (notes, about)."""
    lines = [" ".join(line.split()) for line in str(v or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()[:limit]


def _date(v: Any) -> str:
    """YYYY-MM-DD (or YYYY-MM-DD HH:MM), or ""."""
    s = _text(v, 20)
    if not s:
        return ""
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", s)
    if not m:
        raise ValueError(t("“{value}” isn't a date: write it as 2026-10-25 (or 2026-10-25 14:00)", value=s))
    y, mo, d, hh, mm = m.groups()
    out = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return out + (f" {int(hh):02d}:{mm}" if hh else "")


def _minutes(v: Any) -> int:
    """How long something booked takes, in minutes (0: not said)."""
    try:
        n = int(float(str(v or 0).strip() or 0))
    except ValueError:
        raise ValueError(t("“{value}” isn't a number of minutes", value=_text(v, 20)))
    return max(0, min(n, 24 * 60))


def _deadline(v: Any) -> tuple[str, bool]:
    """(date, rolling) from what someone wrote: "2026-10-31", "rolling", "招满即止", ""."""
    s = _text(v, 40)
    if s.casefold() in ROLLING:
        return "", True
    return _date(s), False


def apply_event(a: dict) -> dict | None:
    """The role's deadline: the booked thing of kind "apply" (None once the user deleted it)."""
    return next((e for e in a.get("schedule") or [] if e.get("kind") == "apply"), None)


def _next_eid(a: dict) -> str:
    return "E" + str(max((int(e["id"][1:]) for e in a.get("schedule") or [] if str(e.get("id", ""))[1:].isdigit()), default=0) + 1)


def _set_deadline(a: dict, v: Any) -> None:
    """The role's apply event gets this deadline (and is made when the role has none)."""
    at, rolling = _deadline(v)
    e = apply_event(a)
    if not e:
        e = {"id": _next_eid(a), "kind": "apply", "title": t("Apply by"), "at": "", "rolling": False, "minutes": 0, "where": "", "note": ""}
        a.setdefault("schedule", []).append(e)
    e["at"], e["rolling"] = at, rolling
    a["schedule"].sort(key=lambda x: x["at"])


def _place(v: Any) -> dict:
    """{kind, area} from a dict or from what someone wrote: "南京", "remote: APAC", "远程（中国大陆）"."""
    if isinstance(v, dict):
        kind, area = _text(v.get("kind"), 20).casefold() or "onsite", _text(v.get("area"), 80)
    else:
        s, kind, area = _text(v, 100), "onsite", _text(v, 80)
        m = re.match(r"^\s*(" + "|".join(sorted(map(re.escape, PLACE_WORDS), key=len, reverse=True)) + r")(?![a-z])\s*[:：·,，\-(（]?\s*(.*?)[)）]?\s*$",
                     s, re.IGNORECASE)
        if m:
            kind, area = PLACE_WORDS[m.group(1).casefold()], _text(m.group(2), 80)
    if kind not in PLACE_KINDS:
        raise ValueError(t("Unknown kind of place “{value}”: use one of {kinds}", value=kind, kinds=", ".join(PLACE_KINDS)))
    if not area:
        raise ValueError(t("Say which region a remote role is open to (不限 if anywhere)") if kind == "remote" else t("Say which city"))
    return {"kind": kind, "area": area}


def _places(v: Any) -> list[dict]:
    out: list[dict] = []
    for x in v if isinstance(v, list) else [v] if v else []:
        p = _place(x)
        if not any(o["kind"] == p["kind"] and _key(o["area"]) == _key(p["area"]) for o in out):
            out.append(p)
    return out[:40]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _key(s: str) -> str:
    return re.sub(r"[\s·・\-_/()（）]+", "", str(s or "")).casefold()


def state_of(word: Any) -> str:
    """A status id from what someone wrote ("一面" → interview); raises with the list when unknown."""
    w = _text(word, 40).casefold()
    if w in STATE_IDS:
        return w
    for sid, names in ALIASES.items():
        if w in names or _key(w) in {_key(n) for n in names}:
            return sid
    raise ValueError(t("Unknown status “{value}”: use one of {states}", value=_text(word, 40), states=", ".join(STATE_IDS)))


def _esc(v: Any) -> str:
    return str(v or "").replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "").replace("\n", "\\n")


def _unesc(v: str) -> str:
    out, i = [], 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            out.append({"t": "\t", "n": "\n", "\\": "\\"}.get(v[i + 1], v[i + 1]))
            i += 2
        else:
            out.append(v[i])
            i += 1
    return "".join(out)


class Hunt:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "hunt"
        self.lock = threading.RLock()
        self.on_change: Callable[[], None] | None = None
        self.scanner: Any = None   # set by jobhunt/api.py: scanning the companies' sites (jobhunt/scan.py)
        # set by jobhunt/api.py: finding a logo on a company's own sites (jobhunt/logos.py), and asking for it
        self.find_logo: Callable[[list[str]], tuple[bytes, str, str] | None] | None = None
        self.want_logos: Callable[..., None] = lambda *cids: None

    # ---- the files ----------------------------------------------------------------
    def _file(self, name: str) -> Path:
        return self.dir / name

    def _read(self, name: str, key: str) -> dict:
        try:
            data = json.loads(self._file(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("next", 1)
        data[key] = [x for x in data.get(key) or [] if isinstance(x, dict) and x.get("id")]
        return data

    def _write(self, name: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._file(name)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        for attempt in range(20):   # Windows: a reader may hold the file for a moment
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def _companies(self) -> dict:
        return self._read("companies.json", "companies")

    def _apps(self) -> dict:
        return self._read("applications.json", "applications")

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    def exists(self) -> bool:
        return self._file("companies.json").exists() or self._file("applications.json").exists()

    # ---- the ledger ----------------------------------------------------------------
    def _log(self, app: str, kind: str, *, frm: str = "", to: str = "", source: str = "window", note: str = "",
             at: str | None = None) -> dict:
        ev = {"at": at or _now(), "app": app, "kind": kind, "from": frm, "to": to, "source": _text(source, 30) or "window",
              "note": _long(note, 2000)}
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._file("events.tsv")
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write("\t".join(COLUMNS) + "\n")
            f.write("\t".join(_esc(ev[c]) for c in COLUMNS) + "\n")
        return ev

    def events(self, app: str | None = None) -> list[dict]:
        """Oldest first, by when they happened."""
        try:
            lines = self._file("events.tsv").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[1:]:
            cells = line.split("\t")
            if len(cells) < len(COLUMNS):
                cells += [""] * (len(COLUMNS) - len(cells))
            ev = {c: _unesc(v) for c, v in zip(COLUMNS, cells)}
            if app is None or ev["app"] == app:
                out.append(ev)
        out.sort(key=lambda e: e["at"])   # by when it happened (a note can be dated back); the file keeps the order written
        return out

    # ---- companies -----------------------------------------------------------------
    def companies(self) -> list[dict]:
        return self._companies()["companies"]

    def company(self, ref: str) -> dict:
        """By id ("C2") or by name or alias, any case and spacing."""
        cs = self.companies()
        r = str(ref or "").strip()
        hit = next((c for c in cs if c["id"] == r), None)
        if hit:
            return hit
        k = _key(r)
        hits = [c for c in cs if k and (k == _key(c["name"]) or k in {_key(a) for a in c.get("aliases") or []})]
        if len(hits) == 1:
            return hits[0]
        hits = [c for c in cs if k and (k in _key(c["name"]) or any(k in _key(a) for a in c.get("aliases") or []))]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise KeyError(t("“{ref}” could be {names}: say which", ref=r, names=", ".join(f"{c['name']} ({c['id']})" for c in hits)))
        raise KeyError(t("No company “{ref}”", ref=r))

    @staticmethod
    def _company_fields(c: dict, fields: dict) -> None:
        for k, limit in (("name", 80), ("site", 500), ("industry", 80), ("strength", 160), ("location", 80), ("size", 60),
                         ("account", 120), ("subset", 80)):
            if k in fields:
                c[k] = _text(fields[k], limit)
        if "note" in fields:
            c["note"] = _long(fields["note"], 4000)
        if "sites" in fields:
            c["sites"] = Hunt._sites(fields["sites"])
            if c["sites"] and not c.get("site"):
                c["site"] = next((x["url"] for x in c["sites"] if x["kind"] == "campus"), c["sites"][0]["url"])
        if "sources" in fields and isinstance(fields["sources"], dict):
            src = dict(c.get("sources") or {})
            for k, v in fields["sources"].items():
                if k in FILLABLE and re.match(r"https?://", _text(v, 500)):
                    src[k] = _text(v, 500)
            c["sources"] = src
        if "aliases" in fields:
            xs = fields["aliases"]
            xs = re.split(r"[,，、;；]", xs) if isinstance(xs, str) else xs or []
            c["aliases"] = [a for a in dict.fromkeys(_text(x, 60) for x in xs) if a][:12]
        if "about" in fields:
            c["about"] = Hunt._about(fields["about"])
        if "places" in fields:
            c["places"] = _places(fields["places"])
        if not c.get("name"):
            raise ValueError(t("Name the company first"))
        site = c.get("site") or ""
        if site and not re.match(r"https?://", site):
            c["site"] = "https://" + site

    @staticmethod
    def _sites(v: Any) -> list[dict]:
        """A company's careers sites: [{url, kind (campus | intern | social | other), system (Moka, 北森, own site…)}]."""
        out = []
        for x in v if isinstance(v, list) else []:
            x = x if isinstance(x, dict) else {"url": x}
            url = _text(x.get("url"), 500)
            if not url:
                continue
            if not re.match(r"https?://", url):
                url = "https://" + url
            kind = _text(x.get("kind"), 20).lower()
            out.append({"url": url, "kind": kind if kind in SITE_KINDS else "other", "system": _text(x.get("system"), 40)})
        return out[:8]

    @staticmethod
    def _about(v: Any) -> dict:
        """What Claude found about a company: a summary and points, each with its sources, and when it read them."""
        if not isinstance(v, dict):
            v = {"summary": v}
        points = []
        for p in v.get("points") or []:
            if not isinstance(p, dict):
                continue
            src = [_text(s, 500) for s in (p.get("sources") or []) if _text(s, 500)]
            text = _long(p.get("text"), 2000)
            if text:
                points.append({"topic": _text(p.get("topic"), 60), "text": text, "sources": src[:6]})
        return {"summary": _long(v.get("summary"), 3000), "points": points[:30],
                "sources": [_text(s, 500) for s in (v.get("sources") or []) if _text(s, 500)][:20],
                "read_at": _text(v.get("read_at"), 30) or time.strftime("%Y-%m-%d")}

    def add_company(self, fields: dict) -> dict:
        with self.lock:
            data = self._companies()
            name = _text(fields.get("name"), 80)
            if name and any(_key(c["name"]) == _key(name) for c in data["companies"]):
                raise ValueError(t("{name} is already on the list", name=name))
            c = {"id": f"C{data['next']}", "name": "", "aliases": [], "site": "", "sites": [], "industry": "", "strength": "",
                 "location": "", "places": [], "size": "", "logo": "", "sources": {}, "kept": [], "note": "", "account": "",
                 "subset": "", "about": None, "created": _now(), "updated": _now()}
            self._company_fields(c, fields)
            data["next"] += 1
            data["companies"].insert(0, c)
            self._write("companies.json", data)
        self._changed()
        return c

    def update_company(self, ref: str, fields: dict, *, source: str = "window") -> dict:
        """An edit. Made in the window, the fields it changes become the user's (`kept`): Claude's fills skip them."""
        with self.lock:
            cid = self.company(ref)["id"]
            data = self._companies()
            c = next(x for x in data["companies"] if x["id"] == cid)
            name = _text(fields.get("name"), 80) if "name" in fields else ""
            if name and any(_key(x["name"]) == _key(name) and x["id"] != cid for x in data["companies"]):
                raise ValueError(t("{name} is already on the list", name=name))
            before = {k: json.dumps(c.get(k), ensure_ascii=False) for k in FILLABLE}
            self._company_fields(c, fields)
            if source == "window":
                mine = [k for k in FILLABLE if k in fields and json.dumps(c.get(k), ensure_ascii=False) != before[k]]
                c["kept"] = sorted(set(c.get("kept") or []) | set(mine))
            if "kept" in fields and isinstance(fields["kept"], list):   # the user hands fields back to Claude
                c["kept"] = sorted({k for k in fields["kept"] if k in FILLABLE})
            c["updated"] = _now()
            self._write("companies.json", data)
        self._changed()
        return c

    def fill(self, ref: str, fields: dict) -> tuple[dict, list[str]]:
        """Claude's research, every field at once. Fields the user edited stay theirs; an official name that
        differs from the user's goes into the aliases. Returns the company and the fields it left alone."""
        with self.lock:
            c = self.company(ref)
            kept = set(c.get("kept") or [])
            take = {k: v for k, v in fields.items() if k in FILLABLE and k != "logo" and k not in kept}
            skipped = sorted(k for k in fields if k in FILLABLE and k in kept)
            official = _text(fields.get("name"), 80)
            if official and _key(official) != _key(c["name"]) and "aliases" not in kept:
                al = take.get("aliases", c.get("aliases") or [])
                al = re.split(r"[,，、;；]", al) if isinstance(al, str) else list(al)
                take["aliases"] = [official, *al]
            if "sources" in fields:
                take["sources"] = {k: v for k, v in (fields.get("sources") or {}).items() if k not in kept}
            if "site" in kept:
                take.pop("site", None)
            elif "sites" in take and "site" not in take:   # the campus site leads unless the user picked one
                sites = self._sites(take["sites"])
                if sites:
                    take["site"] = next((x["url"] for x in sites if x["kind"] == "campus"), sites[0]["url"])
            c = self.update_company(c["id"], take, source="chat")
        return c, skipped

    def logo_path(self, ref: str) -> Path | None:
        c = self.company(ref)
        p = self.dir / "logos" / c["logo"] if c.get("logo") else None
        return p if p and p.is_file() else None

    def save_logo(self, ref: str, data: bytes, content_type: str, *, source: str = "chat") -> dict:
        """A company's logo, kept in the data folder (never hot-linked)."""
        ext = LOGO_TYPES.get(content_type.split(";")[0].strip().lower())
        if not ext:
            raise ValueError(t("That isn't a picture Resume Studio can show ({type})", type=content_type or "?"))
        if not data or len(data) > 2_000_000:
            raise ValueError(t("The logo must be a picture under 2 MB"))
        c = self.company(ref)
        if source != "window" and "logo" in (c.get("kept") or []):
            return c
        d = self.dir / "logos"
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob(c["id"] + ".*"):
            old.unlink(missing_ok=True)
        (d / f"{c['id']}.{ext}").write_bytes(data)
        with self.lock:
            cs = self._companies()
            me = next(x for x in cs["companies"] if x["id"] == c["id"])
            me["logo"] = f"{c['id']}.{ext}"
            if source == "window":
                me["kept"] = sorted(set(me.get("kept") or []) | {"logo"})
            me["updated"] = _now()
            self._write("companies.json", cs)
        self._changed()
        return me

    def migrate(self) -> None:
        """Deadlines belong to roles (2026-10-18): a company's deadline moves to its roles that have none.
        A role's next step and its date (until 2026-10-21) become something booked when they had a date,
        else a line in its notes. A role's deadline field (until 2026-10-21) becomes its apply event."""
        with self.lock:
            cs = self._companies()
            moving = {c["id"]: c.pop("deadline") for c in cs["companies"] if "deadline" in c}
            apps = self._apps()
            old = [a for a in apps["applications"] if "next" in a or "due" in a]
            fields = [a for a in apps["applications"] if "deadline" in a]
            if not moving and not old and not fields:
                return
            for a in apps["applications"]:
                if moving.get(a["company"]) and not a.get("deadline"):
                    a["deadline"] = moving[a["company"]]
                if "deadline" in a:
                    day = str(a.pop("deadline") or "")
                    e = apply_event(a)
                    if not e or (day and not e.get("at")):
                        _set_deadline(a, day)
            for a in old:
                nxt, due = _text(a.pop("next", "")), str(a.pop("due", "") or "")
                if nxt and due:
                    a.setdefault("schedule", []).append({"id": _next_eid(a), "title": nxt, "at": due, "minutes": 0, "where": "", "note": ""})
                elif nxt or due:
                    a["note"] = ((a.get("note") or "") + "\n" + " ".join(x for x in (nxt, due) if x)).strip()
            self._write("applications.json", apps)
            if moving:
                self._write("companies.json", cs)

    def delete_company(self, ref: str) -> None:
        """The company and its applications go; the ledger keeps their lines."""
        with self.lock:
            cid = self.company(ref)["id"]
            data = self._companies()
            data["companies"] = [x for x in data["companies"] if x["id"] != cid]
            self._write("companies.json", data)
            apps = self._apps()
            apps["applications"] = [a for a in apps["applications"] if a["company"] != cid]
            self._write("applications.json", apps)
        self._changed()

    # ---- applications --------------------------------------------------------------
    def applications(self, company: str | None = None) -> list[dict]:
        apps = self._apps()["applications"]
        if company:
            cid = self.company(company)["id"]
            apps = [a for a in apps if a["company"] == cid]
        return apps

    def application(self, ref: str) -> dict:
        """By id ("A3"), or "Company/Role" (any case; a unique part of the role is enough)."""
        r = str(ref or "").strip()
        apps = self.applications()
        hit = next((a for a in apps if a["id"] == r), None)
        if hit:
            return hit
        if "/" not in r:
            raise KeyError(t("No application “{ref}”: give its id (A3) or Company/Role", ref=r))
        cref, role = r.split("/", 1)
        c = self.company(cref)
        mine = [a for a in apps if a["company"] == c["id"]]
        k = _key(role)
        hits = [a for a in mine if _key(a["role"]) == k] or [a for a in mine if k and k in _key(a["role"])]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise KeyError(t("“{ref}” could be {names}: say which", ref=r, names=", ".join(f"{a['role']} ({a['id']})" for a in hits)))
        raise KeyError(t("{company} has no role “{role}”", company=c["name"], role=role.strip()))

    def _app_fields(self, a: dict, fields: dict) -> None:
        for k, limit in (("role", 120), ("url", 500), ("referral", 80), ("subset", 80), ("batch", 40)):
            if k in fields:
                a[k] = _text(fields[k], limit)
        if "note" in fields:
            a["note"] = _long(fields["note"], 4000)
        if "jd" in fields:   # the posting's own text, as it was read: kept apart from the user's notes
            a["jd"] = _long(fields["jd"], 20000)
        if "deadline" in fields:
            _set_deadline(a, fields["deadline"])
        if "places" in fields:
            a["places"] = _places(fields["places"])
        if "channel" in fields:
            ch = _text(fields["channel"], 20) or "site"
            if ch not in CHANNELS:
                raise ValueError(t("Unknown channel “{value}”: use one of {channels}", value=ch, channels=", ".join(CHANNELS)))
            a["channel"] = ch
        if "platform" in fields:
            a["platform"] = _text(fields["platform"], 40)
        if a.get("channel") != "platform":
            a["platform"] = ""
        if not a.get("role"):
            raise ValueError(t("Name the role first"))

    def add_application(self, company: str, fields: dict, *, source: str = "window", at: str | None = None) -> dict:
        with self.lock:
            c = self.company(company)
            data = self._apps()
            role = _text(fields.get("role"), 120)
            if role and any(x["company"] == c["id"] and _key(x["role"]) == _key(role) for x in data["applications"]):
                raise ValueError(t("{company} already has {role}", company=c["name"], role=role))
            status = state_of(fields.get("status") or "saved")
            a = {"id": f"A{data['next']}", "company": c["id"], "role": "", "url": "", "referral": "", "channel": "site", "platform": "",
                 "order": 0, "status": status, "applied": "", "batch": "", "note": "", "jd": "", "subset": "",
                 "places": [], "sent": [], "schedule": [], "co_app": None, "created": _now(), "updated": _now()}
            # every role starts with its apply event, its deadline still unknown
            self._app_fields(a, {"channel": "site", "deadline": "", **fields})
            mine = [x for x in data["applications"] if x["company"] == c["id"]]
            order = fields.get("order")
            a["order"] = max((x.get("order") or 0 for x in mine), default=0) + 1
            if status not in ("saved",):
                a["applied"] = _date(fields.get("applied")) or time.strftime("%Y-%m-%d")
            data["next"] += 1
            data["applications"].append(a)
            if order:
                self._reorder(data, c["id"], a["id"], int(order))
            self._write("applications.json", data)
            self._log(a["id"], "created", to=status, source=source, note=_text(fields.get("event_note"), 300), at=at)
        self._changed()
        return a

    @staticmethod
    def _reorder(data: dict, cid: str, aid: str, place: int) -> None:
        mine = sorted((x for x in data["applications"] if x["company"] == cid), key=lambda x: x.get("order") or 0)
        me = next(x for x in mine if x["id"] == aid)
        mine.remove(me)
        mine.insert(max(0, min(len(mine), place - 1)), me)
        for i, x in enumerate(mine, 1):
            x["order"] = i

    def update_application(self, ref: str, fields: dict) -> dict:
        with self.lock:
            aid = self.application(ref)["id"]
            data = self._apps()
            a = next(x for x in data["applications"] if x["id"] == aid)
            if "role" in fields:
                role = _text(fields["role"], 120)
                if any(x["company"] == a["company"] and x["id"] != aid and _key(x["role"]) == _key(role) for x in data["applications"]):
                    raise ValueError(t("{role} is already on this company's list", role=role))
            self._app_fields(a, fields)
            if fields.get("order"):
                self._reorder(data, a["company"], aid, int(fields["order"]))
            a["updated"] = _now()
            self._write("applications.json", data)
        self._changed()
        return a

    def set_status(self, ref: str, status: Any, *, note: str = "", source: str = "window", on: str = "") -> dict:
        with self.lock:
            aid = self.application(ref)["id"]
            to = state_of(status)
            data = self._apps()
            a = next(x for x in data["applications"] if x["id"] == aid)
            frm = a["status"]
            day = _date(on)
            if to == frm and not note:
                return a
            a["status"] = to
            if to not in ("saved", "withdrawn") and not a.get("applied"):
                a["applied"] = day[:10] or time.strftime("%Y-%m-%d")
            a["updated"] = _now()
            self._write("applications.json", data)
            self._log(aid, "status", frm=frm, to=to, source=source, note=note,
                      at=(day[:10] + "T" + (day[11:] or "00:00") + ":00") if day else None)
        self._changed()
        return a

    def add_note(self, ref: str, note: str, *, source: str = "window", on: str = "") -> dict:
        text = _long(note, 2000)
        if not text:
            raise ValueError(t("Write the note first"))
        with self.lock:
            a = self.application(ref)
            day = _date(on)
            ev = self._log(a["id"], "note", source=source, note=text,
                           at=(day[:10] + "T" + (day[11:] or "00:00") + ":00") if day else None)
        self._changed()
        return ev

    def mark_sent(self, ref: str, subset: str, n: int, *, source: str = "window", where: str = "") -> dict:
        """This application went out with version n of `subset`: pinned on it, and Applied if it was only saved."""
        with self.lock:
            aid = self.application(ref)["id"]
            data = self._apps()
            a = next(x for x in data["applications"] if x["id"] == aid)
            a["sent"].append({"at": _now(), "subset": subset, "n": int(n)})
            frm = a["status"]
            if frm == "saved":
                a["status"] = "applied"
                a["applied"] = a.get("applied") or time.strftime("%Y-%m-%d")
            a["updated"] = _now()
            self._write("applications.json", data)
            self._log(aid, "sent", frm=frm, to=a["status"], source=source, note=f"{subset} v{n}" + (f" · {where}" if where else ""))
        self._changed()
        return a

    # ---- what is booked: tests, interviews ------------------------------------------
    @staticmethod
    def _booked_fields(e: dict, fields: dict) -> None:
        if "title" in fields:
            e["title"] = _text(fields["title"], 60)
        if "at" in fields:
            e["at"] = _date(fields["at"])
        if e.get("kind") == "apply":
            # the deadline: a date, no date yet, or none at all (it closes when the role is filled)
            if "rolling" in fields:
                e["rolling"] = bool(fields["rolling"])
            if e["at"]:
                e["rolling"] = False
        if "minutes" in fields:
            e["minutes"] = _minutes(fields["minutes"])
        if "where" in fields:
            e["where"] = _text(fields["where"], 300)
        if "note" in fields:
            e["note"] = _long(fields["note"], 1000)
        if not e.get("title"):
            raise ValueError(t("Say what it is first (一面, 笔试…)"))
        if not e.get("at") and e.get("kind") != "apply":
            raise ValueError(t("Give it a date"))

    def _booked(self, ref: str, fn: Callable[[dict], Any]) -> dict:
        with self.lock:
            aid = self.application(ref)["id"]
            data = self._apps()
            a = next(x for x in data["applications"] if x["id"] == aid)
            a.setdefault("schedule", [])
            out = fn(a)
            a["schedule"].sort(key=lambda e: e["at"])
            a["updated"] = _now()
            self._write("applications.json", data)
        self._changed()
        return out

    def add_booked(self, ref: str, fields: dict) -> dict:
        """Something booked for this application: a test, 一面, 二面… with its date (and time)."""
        def add(a: dict) -> dict:
            e = {"id": _next_eid(a), "title": "", "at": "", "minutes": 0, "where": "", "note": ""}
            if fields.get("kind") == "apply":
                if apply_event(a):
                    raise ValueError(t("{role} has its deadline already: change that one", role=a["role"]))
                e.update(kind="apply", title=t("Apply by"), rolling=False)
            self._booked_fields(e, fields)
            a["schedule"].append(e)
            return e
        return self._booked(ref, add)

    def update_booked(self, ref: str, eid: str, fields: dict) -> dict:
        def edit(a: dict) -> dict:
            e = self._booked_one(a, eid)
            self._booked_fields(e, fields)
            return e
        return self._booked(ref, edit)

    def delete_booked(self, ref: str, eid: str) -> dict:
        def drop(a: dict) -> dict:
            e = self._booked_one(a, eid)
            a["schedule"].remove(e)
            return e
        return self._booked(ref, drop)

    @staticmethod
    def _booked_one(a: dict, eid: str) -> dict:
        r = str(eid or "").strip()
        hit = next((e for e in a["schedule"] if e["id"] == r), None) \
            or next((e for e in a["schedule"] if _key(e["title"]) == _key(r)), None)
        if not hit:
            raise KeyError(t("{role} has nothing booked called “{ref}”", role=a["role"], ref=r))
        return hit

    def link_tracker(self, ref: str, co_app: str) -> None:
        """The row this application has in career-ops' tracker (when it is copied there)."""
        with self.lock:
            aid = self.application(ref)["id"]
            data = self._apps()
            a = next(x for x in data["applications"] if x["id"] == aid)
            a["co_app"] = str(co_app)
            self._write("applications.json", data)

    def delete_application(self, ref: str) -> None:
        with self.lock:
            a = self.application(ref)
            data = self._apps()
            data["applications"] = [x for x in data["applications"] if x["id"] != a["id"]]
            mine = sorted((x for x in data["applications"] if x["company"] == a["company"]), key=lambda x: x.get("order") or 0)
            for i, x in enumerate(mine, 1):
                x["order"] = i
            self._write("applications.json", data)
        self._changed()

    def rename_subset(self, old: str, new: str) -> None:
        """A renamed subset keeps what was sent with it, and the companies filled from it."""
        with self.lock:
            cs, hit = self._companies(), False
            for c in cs["companies"]:
                if c.get("subset") == old:
                    c["subset"], hit = new, True
            if hit:
                self._write("companies.json", cs)
            apps, hit = self._apps(), False
            for a in apps["applications"]:
                if a.get("subset") == old:
                    a["subset"], hit = new, True
                for s in a.get("sent") or []:
                    if s.get("subset") == old:
                        s["subset"], hit = new, True
            if hit:
                self._write("applications.json", apps)

    # ---- moving in what Resume Studio kept before ----------------------------------
    def import_signups(self, signups: list[dict]) -> int:
        """The sign-ups the Applications tab kept (jobs/signups.py) become companies and their roles,
        once: their order, the versions sent and their career-ops rows come along."""
        n = 0
        with self.lock:
            for s in signups:
                name = _text(s.get("company"), 80)
                if not name:
                    continue
                try:
                    c = self.company(name)
                except KeyError:
                    c = self.add_company({"name": name, "site": s.get("site") or "", "note": s.get("note") or "",
                                          "subset": s.get("subset") or ""})
                for r in s.get("roles") or []:
                    sent = [x for x in s.get("sent") or [] if r.get("key") in (x.get("roles") or [])]
                    try:
                        a = self.add_application(c["id"], {"role": r.get("role"), "url": r.get("url") or "", "channel": "site",
                                                           "status": "saved"}, source="import", at=s.get("created") or None)
                    except ValueError:
                        continue
                    data = self._apps()
                    me = next(x for x in data["applications"] if x["id"] == a["id"])
                    me["sent"] = [{"at": x.get("at") or _now(), "subset": x.get("subset") or "", "n": int(x.get("n") or 0)} for x in sent]
                    if sent:
                        me["applied"] = str(sent[0].get("at") or "")[:10]
                    me["co_app"] = str(r["app"]) if r.get("app") else None
                    if sent:
                        me["status"] = "applied"
                    self._write("applications.json", data)
                    for x in sent:   # the sends keep their own dates in the timeline
                        self._log(a["id"], "sent", frm="saved", to="applied", source="import",
                                  note=f"{x.get('subset') or ''} v{x.get('n') or 0}", at=x.get("at") or None)
                    n += 1
        self._changed()
        return n

    # ---- what the window shows ---------------------------------------------------------
    def table(self) -> dict:
        """Every application with its company's name and its last event; every company with its counts."""
        cs = self.companies()
        apps = self.applications()
        last: dict[str, dict] = {}
        for ev in self.events():
            last[ev["app"]] = ev
        by_c = {c["id"]: c for c in cs}
        rows = []
        for a in apps:
            c = by_c.get(a["company"]) or {}
            rows.append({**a, "company_name": c.get("name", ""), "last": last.get(a["id"])})
        rank = {s: i for i, s in enumerate(STATE_IDS)}
        companies = []
        for c in cs:
            mine = [a for a in apps if a["company"] == c["id"]]
            live = [a for a in mine if a["status"] in OPEN or a["status"] in ("offer", "hired")]
            best = max(live or mine, key=lambda a: rank.get(a["status"], 0))["status"] if mine else ""
            mine.sort(key=lambda a: a.get("order") or 0)
            companies.append({**c, "roles": len(mine), "open": sum(1 for a in mine if a["status"] in OPEN), "best": best,
                              "apps": [{"id": a["id"], "role": a["role"], "status": a["status"]} for a in mine],
                              "last": max((last[a["id"]]["at"] for a in mine if a["id"] in last), default="")})
        return {"applications": rows, "companies": companies,
                "states": [{"id": s, "label": label, "open": s in OPEN} for s, label, _ in STATES]}
