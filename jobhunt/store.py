"""The tracker's data: companies, applications and what happened to them (docs/JOB_HUNT.md).

Plain files next to the data file, in our own schema, so switching trackers loses nothing:

    resume-studio-data/hunt/companies.json     {"next": 3, "companies": [{id: "C1", name, aliases, site, sites, industry, nature,
                                     strength, hq: {area, id} | null, places, size, logo, sources, kept, note,
                                     account, subset, about, created, updated}]}
    resume-studio-data/hunt/applications.json  {"next": 5, "applications": [{id: "A1", company: "C1", role, url, channel,
                                     platform, order, status, applied, note, subset,
                                     places: [{kind: "onsite"|"hybrid"|"remote", area, id?}],
                                     sent: [{at, subset, n}], schedule: [{id, kind?, title, at, minutes,
                                     where, note}],
                                     co_app, created, updated}]}
    resume-studio-data/hunt/logos/C1.png       a company's logo, downloaded once (docs/JOB_HUNT.md, 2026-10-18)
    resume-studio-data/hunt/history.json       what chats changed, each undoable (jobhunt/history.py)

A company fills itself: Claude researches it and sends every field at once (`fill`), each with its source.
A field the user edits in the window goes into `kept`, and Claude's later fills leave it alone. Deadlines
belong to roles, not to companies; a company deadline from before moves to its roles.
    resume-studio-data/hunt/events.tsv         at  app  kind  from  to  source  note      (append-only)

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
Since 2026-10-27 a place is picked from a list (jobhunt/geo.py) and kept by its `id`, its `area` then being its
English name: the window shows it in the interface's language. One the list doesn't know has no id and is kept
as written. A company's headquarters (`hq`) is one such city.

An application's `schedule` holds what is booked for it (docs/JOB_HUNT.md, 2026-10-21): a test, 一面, 二面,
HR 面…, each with its date and time. The window shows them on a calendar and a Gantt chart, and
exports them as an .ics file for the user's own calendar (jobhunt/ics.py). Unlike the ledger, they
are edited and deleted freely: a rescheduled interview moves. Booking one moves the status forward
(never back): an interview (一面, HR 面…) to interviewing, a test (笔试, 测评, OA) to tests; a line in the
ledger says so, and the user can set it back.

When to apply by is one of them, not a field of the role (docs/JOB_HUNT.md, 2026-10-21): the one of kind
"apply". A role has one only when something real says when it closes (docs/JOB_HUNT.md, 2026-10-26): `at`
is the date the posting gives, or `rolling: true` when the posting (or the user) says it closes once filled
(滚动招聘, 招满即止). Not knowing is no event at all, never a guess. The `apply_by` field the window and Claude
send ("2026-10-31", "rolling", or "" to drop it) sets it. The table shows it as the role's next step until it
is applied to.

The usual rounds (笔试, 测评, 一面 … 终面) and the apply event are built in: `kind` names them and `title` stays
"" so they read in the interface's language (`event_title`); a title the user types is theirs, kept as written.

A role linked to a posting (jobhunt/postings.py) follows it (docs/JOB_HUNT.md, 2026-10-30): when a scan finds the
posting gone from the company's site, a role still at "want to apply" becomes "posting closed", and one further on
gets a line in its timeline (the posting closing then says nothing about how it is going). A posting back on
the site puts a role it closed back to "want to apply".
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from core.i18n import t

from . import geo
from .natures import nature   # what kind of employer a company is: a track can be for some kinds only

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
    ("closed", "Posting closed", "discarded"),   # gone from the company's site before it was applied to (a scan says so)
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
    "closed": ["posting closed", "taken down", "no longer listed", "已下架", "下架", "岗位关闭", "已关闭", "撤岗"],
}
CHANNELS = ["site", "platform", "referral", "other"]
# how far a role has come, for what booking a round moves it to (forward only)
STAGES = ["saved", "applied", "assessment", "interview"]
_TEST = re.compile(r"笔试|测评|机试|测试|性格|\boa\b|assessment|\btests?\b|exam|hackerrank|codility", re.IGNORECASE)
# what people write for a role that closes when it is filled (the apply event's `rolling`)
ROLLING = {"rolling", "滚动", "滚动招聘", "招满即止", "招满为止", "until filled", "open until filled"}
# the events built in: their names follow the interface's language (title ""), and what booking one says about the role
BOOKED_KINDS = {"apply": "Apply by", "test": "Written test", "assessment": "Assessment", "round1": "1st interview",
                "round2": "2nd interview", "round3": "3rd interview", "hr": "HR interview", "final": "Final interview"}
KIND_STAGES = {"test": "assessment", "assessment": "assessment", "round1": "interview", "round2": "interview",
               "round3": "interview", "hr": "interview", "final": "interview"}
# names the built-in rounds had as typed titles before they were built in (the chips were Chinese only)
_OLD_NAMES = {"笔试": "test", "测评": "assessment", "一面": "round1", "二面": "round2", "三面": "round3",
              "hr 面": "hr", "hr面": "hr", "终面": "final", "投递截止": "apply"}
PLACE_KINDS = ["onsite", "hybrid", "remote"]
# what people (and Claude) write before the area: "remote: APAC", "远程：中国大陆", "hybrid 悉尼"
PLACE_WORDS = {"remote": "remote", "远程": "remote", "wfh": "remote", "居家": "remote",
               "hybrid": "hybrid", "混合": "hybrid", "混合办公": "hybrid",
               "onsite": "onsite", "on-site": "onsite", "office": "onsite", "现场": "onsite", "坐班": "onsite"}
# what Claude fills on a company (the name stays the user's: an official name goes into the aliases)
FILLABLE = ["aliases", "industry", "nature", "strength", "hq", "places", "size", "site", "sites", "logo", "about"]
SITE_KINDS = ["campus", "intern", "social", "other"]
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
              "image/svg+xml": "svg", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico"}
EVENT_KINDS = ["created", "status", "note", "sent"]
MIGRATIONS = ["builtin-events", "place-ids", "place-ids-2"]   # done once on an old applications.json (`Hunt.migrate`); a new one starts with them done
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


def _apply_by(v: Any) -> tuple[str, bool]:
    """When to apply by, from what someone wrote, as (date, rolling): "2026-10-31" is a date; "rolling" or
    "招满即止" closes once filled; "" is nothing known."""
    s = _text(v, 40)
    return ("", True) if s.casefold() in ROLLING else (_date(s), False)


def event_title(e: dict) -> str:
    """What a booked thing is called: the user's own title, or a built-in one's name in the interface's language."""
    return e.get("title") or (t(BOOKED_KINDS[e["kind"]]) if e.get("kind") in BOOKED_KINDS else "")


def _builtin_kind(title: str) -> str | None:
    """The built-in event a title names, in any interface language (what one made from a chip was saved as)."""
    from core.i18n import UI_LANGS
    key = _key(title)
    if not key:
        return None
    for kind, name in BOOKED_KINDS.items():
        if any(_key(t(name, ui=lang)) == key for lang in UI_LANGS):
            return kind
    return _OLD_NAMES.get(_text(title, 20).casefold())


def booked_stage(title: Any, kind: str | None = None) -> str | None:
    """What a booked thing says about the role: an interview (一面, HR 面, interview), a test (笔试, 测评, OA),
    or nothing (a title it can't tell). A built-in kind says it outright."""
    if kind in KIND_STAGES:
        return KIND_STAGES[kind]
    s = _text(title, 60)
    if "面" in s or "interview" in s.casefold():
        return "interview"
    return "assessment" if _TEST.search(s) else None


def apply_event(a: dict) -> dict | None:
    """When to apply by: the booked thing of kind "apply" (None once the user deleted it)."""
    return next((e for e in a.get("schedule") or [] if e.get("kind") == "apply"), None)


def _next_eid(a: dict) -> str:
    return "E" + str(max((int(e["id"][1:]) for e in a.get("schedule") or [] if str(e.get("id", ""))[1:].isdigit()), default=0) + 1)


def _set_apply_by(a: dict, v: Any) -> None:
    """The role's apply event gets this date, or says it closes once filled; nothing known ("") drops it."""
    at, rolling = _apply_by(v)
    e = apply_event(a)
    if not at and not rolling:
        if e:
            a["schedule"].remove(e)
        return
    if not e:
        e = {"id": _next_eid(a), "kind": "apply", "title": "", "at": "", "minutes": 0, "where": "", "note": ""}
        a.setdefault("schedule", []).append(e)
    e["at"], e["rolling"] = at, rolling
    a["schedule"].sort(key=lambda x: x["at"])


def _place(v: Any) -> dict:
    """{kind, area, id} from a dict or from what someone wrote: "南京", "remote: APAC", "远程（中国大陆）".
    A place picked from the list (or found in it by what was written) keeps its id, and its English name as the
    area; one the list doesn't know has no id and stays as written (jobhunt/geo.py)."""
    pid = None
    if isinstance(v, dict):
        kind, area = _text(v.get("kind"), 20).casefold() or "onsite", _text(v.get("area"), 80)
        pid = v.get("id") if geo.known(v.get("id")) else None
    else:
        s, kind, area = _text(v, 100), "onsite", _text(v, 80)
        m = re.match(r"^\s*(" + "|".join(sorted(map(re.escape, PLACE_WORDS), key=len, reverse=True)) + r")(?![a-z])\s*[:：·,，\-(（]?\s*(.*?)[)）]?\s*$",
                     s, re.IGNORECASE)
        if m:
            kind, area = PLACE_WORDS[m.group(1).casefold()], _text(m.group(2), 80)
    if kind not in PLACE_KINDS:
        raise ValueError(t("Unknown kind of place “{value}”: use one of {kinds}", value=kind, kinds=", ".join(PLACE_KINDS)))
    if not area and not pid:
        raise ValueError(t("Say which region a remote role is open to (不限 if anywhere)") if kind == "remote" else t("Say which city"))
    pid = pid or geo.resolve(kind, area)
    return {"kind": kind, "area": geo.english(pid), "id": pid} if pid else {"kind": kind, "area": area}


def _same_place(a: dict, b: dict) -> bool:
    return a["kind"] == b["kind"] and (a["id"] == b["id"] if a.get("id") and b.get("id") else _key(a["area"]) == _key(b["area"]))


def _fit_ai(v: Any) -> dict:
    """{score 0–100, why, have[], gaps[], subset, at}: the window's AI on how well a role's description fits the
    resume it goes out with (hunt.py fit). The rules' score (jobhunt/rank.py) counts skill words; this one reads.
    {} or no score: none."""
    if not isinstance(v, dict) or v.get("score") in (None, ""):
        return {}
    try:
        score = max(0, min(100, round(float(v["score"]))))
    except (TypeError, ValueError):
        raise ValueError(t("The score is a number from 0 to 100")) from None
    items = lambda x: [y for y in dict.fromkeys(_text(s, 120) for s in (x if isinstance(x, list) else [])) if y][:8]  # noqa: E731
    out = {"score": score, "why": _long(v.get("why"), 1200), "have": items(v.get("have")), "gaps": items(v.get("gaps")),
           "subset": _text(v.get("subset"), 80), "at": time.strftime("%Y-%m-%d")}
    return {k: x for k, x in out.items() if x not in ("", [])}


def _places(v: Any) -> list[dict]:
    out: list[dict] = []
    for x in v if isinstance(v, list) else [v] if v else []:
        p = _place(x)
        if not any(_same_place(o, p) for o in out):
            out.append(p)
    return out[:40]


def _found_places(ps: Any) -> list[dict]:
    """Places written before, looked up in the list (one it can't read is left out, and twice is once)."""
    out: list[dict] = []
    for p in ps or []:
        try:
            q = _place(p)
        except ValueError:
            continue
        if not any(_same_place(o, q) for o in out):
            out.append(q)
    return out


def _hq(v: Any) -> dict | None:
    """A company's headquarters: one city, like a place ("上海", or one picked from the list); none for ""."""
    if not v:
        return None
    p = _place(v if isinstance(v, dict) else {"kind": "onsite", "area": v})
    return {"area": p["area"], **({"id": p["id"]} if p.get("id") else {})}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _key(s: str) -> str:
    return re.sub(r"[\s·・\-_/()（）]+", "", str(s or "")).casefold()


def site_host(url: str) -> str:
    return (urlsplit(url if "//" in url else "https://" + url).hostname or "").lower().removeprefix("www.")


def site_key(url: str) -> str:
    """An address as a site's key: no scheme, no www, no trailing slash, any case."""
    u = urlsplit(url if "//" in url else "https://" + url)
    return (site_host(url) + u.path.rstrip("/") + ("?" + u.query if u.query else "")).lower()



# a company's arm in one country or region is a company of its own, with its own careers site: 麦当劳中国,
# McDonald's Australia, Siemens Deutschland, 日本マクドナルド. The names, in many languages: scripts/build_regions.py
REGIONS_FILE = Path(__file__).resolve().parent / "data" / "regions.txt"
_regions: tuple[str, ...] | None = None


def _region_names() -> tuple[str, ...]:
    global _regions
    if _regions is None:
        try:
            _regions = tuple(REGIONS_FILE.read_text(encoding="utf-8").split())
        except OSError:
            _regions = ()
    return _regions


# a name's words: Latin-script words whole ("Tencent Cloud" is tencent, cloud), every CJK / kana / hangul
# character a word of its own (字节 is inside 字节跳动; ブリヂストン inside ブリヂストンタイヤ)
_WIDE = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"
_WORD = re.compile(rf"[{_WIDE}]|[^\s·・\-_/()（）,，.。{_WIDE}]+")


def _words(name: str) -> tuple[str, set[int]]:
    """A name's key (as _key makes it) and the places in it where a word starts or ends."""
    key, edges = "", {0}
    for w in _WORD.findall(str(name or "").casefold()):
        key += w
        edges.add(len(key))
    return key, edges


def _close(a: str, b: str) -> bool:
    """Two names that may be one company: one is whole words of the other (字节 / 字节跳动有限公司, ByteDance /
    ByteDance Ltd, TCL / TCL实业), never a piece of a word (TCL is not in "Tencent Cloud" nor in Rootcloud),
    unless what the longer one adds starts or ends with a country or region (麦当劳中国有限公司, McDonald's
    Australia Pty Ltd, 日本マクドナルド next to 麦当劳, McDonald's, マクドナルド); in its middle, a name is a
    word's part (タイヤ)."""
    (short, _), (long, edges) = sorted((_words(a), _words(b)), key=lambda x: len(x[0]))
    if not short or short == long or len(short) < (2 if re.search(r"[^\x00-\x7f]", short) else 3):
        return False
    at = [i for i in range(len(long) - len(short) + 1)
          if long.startswith(short, i) and i in edges and i + len(short) in edges]
    if not at:
        return False
    rest = long[:at[0]] + long[at[0] + len(short):]
    return not any(rest.startswith(r) or rest.endswith(r) for r in _region_names())


class SameCompany(ValueError):
    """A company being added that is on the list already (`same`), or may be (`close`)."""
    def __init__(self, message: str, same: dict | None = None, close: list[dict] | None = None):
        super().__init__(message)
        self.same, self.close = same, close or []


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
        self._peeked: dict[str, tuple[tuple[int, int], dict]] = {}   # file → (its time and size, parsed): _peek
        self._peek_lock = threading.Lock()
        self.on_change: Callable[[], None] | None = None
        self.scanner: Any = None   # set by jobhunt/api.py: scanning the companies' sites (jobhunt/scan.py)
        # set by jobhunt/api.py: finding a logo on a company's own sites (jobhunt/logos.py), and asking for it
        self.find_logo: Callable[[list[str]], tuple[bytes, str, str] | None] | None = None
        self.want_logos: Callable[..., None] = lambda *cids: None
        self.logo_watcher: Any = None   # set by jobhunt/api.py: the logos folder, for pictures changed from outside
        geo.use(self.dir)   # the places found on OpenStreetMap for this data folder join the list
        self._lexicon: Any = None   # the skills learned from the descriptions read (jobhunt/skills.py)

    def lexicon(self) -> Any:
        if self._lexicon is None:
            from .skills import Lexicon   # skills reads postings, which reads this module
            self._lexicon = Lexicon(self.dir)
        return self._lexicon

    # ---- the files ----------------------------------------------------------------
    def _file(self, name: str) -> Path:
        return self.dir / name

    def _load(self, name: str) -> str:
        """A file's text, "" when there is none. Windows: while _write replaces it, opening it fails for a
        moment; reading that as "no file" showed an empty list (a role's page read in the background, 2026-10-22)."""
        path = self._file(name)
        for attempt in range(40):
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.025)
        return ""

    def _read(self, name: str, key: str) -> dict:
        raw = self._load(name)
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        if name == "applications.json" and not raw:
            data["migrated"] = list(MIGRATIONS)   # a new file has nothing from before to carry over
        data.setdefault("next", 1)
        data[key] = [x for x in data.get(key) or [] if isinstance(x, dict) and x.get("id")]
        return data

    def _peek(self, name: str, key: str) -> dict:
        """_read for a look only, parsed once per change of the file: the window's table asked for the companies
        eight times a look, each a parse of the whole file (5,000 companies: 5.6 s a look, 2026-11-13). The rows
        are copies, one level deep, so a caller's own fields stay its own; a change is written from _read's fresh
        copy, never from this one."""
        try:
            st = self._file(name).stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        with self._peek_lock:
            kept = self._peeked.get(name)
        if sig is None or not kept or kept[0] != sig:
            kept = (sig, self._read(name, key))
            if sig is not None:   # read after the look at its time: a change in between shows as another time next look
                with self._peek_lock:
                    self._peeked[name] = kept
        data = kept[1]
        return {**data, key: [dict(x) for x in data[key]]}

    def _read_rows(self, name: str, key: str) -> dict:
        """A file _write wrote with `rows=key`, read a record at a time. Parsing postings.json (27 MB) in one call
        held Python's lock for a third of a second, and the window's own thread waits for that lock: the window
        froze a moment after it opened (2026-11-11). Between records, other threads get their turn. A file in
        another shape is read whole."""
        path = self._file(name)
        mark = json.dumps(key) + ":["
        for attempt in range(40):
            try:
                with path.open(encoding="utf-8") as f:
                    first = f.readline().rstrip("\n")
                    if not first.endswith(mark):
                        break
                    head = first[: -len(mark)].rstrip(",")
                    data = json.loads((head + "}") if head != "{" else "{}")
                    rows = []
                    for line in f:
                        s = line.rstrip("\n").removesuffix(",")
                        if s == "]}":
                            break
                        if s:
                            rows.append(json.loads(s))
                data = data if isinstance(data, dict) else {}
                data.setdefault("next", 1)
                data[key] = [x for x in rows if isinstance(x, dict) and x.get("id")]
                return data
            except FileNotFoundError:
                break
            except PermissionError:   # Windows: _write is replacing it
                if attempt == 39:
                    raise
                time.sleep(0.025)
            except ValueError:
                break
        return self._read(name, key)

    def _write(self, name: str, data: dict, *, rows: str = "") -> None:
        """`rows`: the key of a long list written one record per line, each without spaces (postings.json: 53,000
        records indented took 31 MB; a line each is still read and compared by eye, one record at a time)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._file(name)
        tmp = path.with_name(path.name + ".tmp")
        if rows:
            dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
            head = ",".join(f"{dump(k)}:{dump(v)}" for k, v in data.items() if k != rows)
            text = "{" + head + ("," if head else "") + dump(rows) + ":[\n" + ",\n".join(dump(x) for x in data.get(rows) or []) + "\n]}\n"
        else:
            text = json.dumps(data, ensure_ascii=False, indent=1)
        tmp.write_text(text, encoding="utf-8", newline="\n")
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
        lines = self._load("events.tsv").splitlines()
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
        return self._peek("companies.json", "companies")["companies"]

    def company(self, ref: str, exact: bool = False) -> dict:
        """By id ("C2") or by name or alias, any case and spacing; a unique part of one is enough unless `exact`.
        A change from a chat is exact: a part of a name once matched another company (a name that arrived as
        "????" was part of an alias "??????????"), and the first company's research was written over the second's."""
        cs = self.companies()
        r = str(ref or "").strip()
        hit = next((c for c in cs if c["id"] == r), None)
        if hit:
            return hit
        k = _key(r)
        hits = [c for c in cs if k and (k == _key(c["name"]) or k in {_key(a) for a in c.get("aliases") or []})]
        if len(hits) == 1:
            return hits[0]
        hits = hits or [c for c in cs if k and (k in _key(c["name"]) or any(k in _key(a) for a in c.get("aliases") or []))]
        if exact and hits:
            raise KeyError(t("No company is called “{ref}”: did you mean {names}? Give its id or its whole name",
                             ref=r, names=", ".join(f"{c['name']} ({c['id']})" for c in hits[:5])))
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise KeyError(t("“{ref}” could be {names}: say which", ref=r, names=", ".join(f"{c['name']} ({c['id']})" for c in hits)))
        raise KeyError(t("No company “{ref}”", ref=r))

    @staticmethod
    def _renamed(fields: dict) -> dict:
        """The headquarters were `location`, free text, until 2026-10-27: sent by that name, they still arrive."""
        out = {("hq" if k == "location" else k): v for k, v in fields.items() if not (k == "location" and "hq" in fields)}
        if isinstance(out.get("sources"), dict) and "location" in out["sources"]:
            out["sources"] = {("hq" if k == "location" else k): v for k, v in out["sources"].items()}
        return out

    @staticmethod
    def _company_fields(c: dict, fields: dict) -> None:
        for k, limit in (("name", 80), ("site", 500), ("industry", 80), ("strength", 160), ("size", 60),
                         ("account", 120), ("subset", 80)):
            if k in fields:
                c[k] = _text(fields[k], limit)
        if "hq" in fields:
            c["hq"] = _hq(fields["hq"])
        if "nature" in fields:
            c["nature"] = nature(fields["nature"])
        if "note" in fields:
            c["note"] = _long(fields["note"], 4000)
        if "sites" in fields:
            c["sites"] = Hunt._sites(fields["sites"])
            # `site` (the one shown first) was one of them: taken off the list, it goes too, or the scan would still read
            # it and warn about it (a company's first site removed in the window stayed, 2026-11-04)
            if c.get("site") and "site" not in fields and not any(x["url"] == c["site"] for x in c["sites"]):
                c["site"] = ""
        # the list is what the window shows and edits: a site given alone (the "Add a company" form) is on it too
        if c.get("site") and not any(x["url"] == c["site"] for x in c.get("sites") or []):
            c["sites"] = Hunt._sites([{"url": c["site"]}, *(c.get("sites") or [])])
            c["site"] = c["sites"][0]["url"] if c["sites"] else ""
        if "sites" in fields:
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
        """A company's careers sites: [{url, kind (campus | intern | social | other, or "" until someone who has
        looked at the site says), system (Moka, 北森, own site…), list}]. `list`, when there is one, is where the site's
        job list really is: a company's own page that is a front for its Feishu or Moka board names that board, and
        the scan reads the board through its provider, no AI (星河科技, 2026-11-03)."""
        out = []
        for x in v if isinstance(v, list) else []:
            x = x if isinstance(x, dict) else {"url": x}
            url = _text(x.get("url"), 500)
            if not url:
                continue
            if not re.match(r"https?://", url):
                url = "https://" + url
            kind = _text(x.get("kind"), 20).lower()
            site = {"url": url, "kind": kind if kind in SITE_KINDS else "", "system": _text(x.get("system"), 40)}
            lst = _text(x.get("list"), 500)
            if lst:
                site["list"] = lst if re.match(r"https?://", lst) else "https://" + lst
            out.append(site)
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

    def look_alike(self, name: str, *, but: str = "") -> tuple[dict | None, list[dict]]:
        """The company `name` already is (its name or one of its aliases, any case and spacing), or else the
        ones it may be (see _close); `but` leaves one company out."""
        k = _key(name)
        close = []
        for c in self.companies() if k else []:
            if c["id"] == but:
                continue
            names = [n for n in [c["name"], *(c.get("aliases") or [])] if _key(n)]
            if k in [_key(n) for n in names]:
                return c, []
            if any(_close(name, n) for n in names):
                close.append(c)
        return None, close

    def add_company(self, fields: dict) -> dict:
        """`different` adds it even when its name is close to a company's on the list (never the same as one)."""
        fields = self._renamed(fields)
        with self.lock:
            data = self._companies()
            name = _text(fields.get("name"), 80)
            same, close = self.look_alike(name)
            if same:
                raise SameCompany(t("{name} is already on the list: {company} ({id}), added {date}", name=name,
                                    company=same["name"], id=same["id"], date=(same.get("created") or "")[:10]), same=same)
            if close and not fields.get("different"):
                raise SameCompany(t("“{name}” may be {names}, already on the list. If it's another company, add it with --different",
                                    name=name, names=", ".join(f"{c['name']} ({c['id']})" for c in close)), close=close)
            c = {"id": f"C{data['next']}", "name": "", "aliases": [], "site": "", "sites": [], "industry": "", "nature": "", "strength": "",
                 "hq": None, "places": [], "size": "", "logo": "", "sources": {}, "kept": [], "note": "", "account": "",
                 "subset": "", "about": None, "created": _now(), "updated": _now()}
            self._company_fields(c, fields)
            data["next"] += 1
            data["companies"].insert(0, c)
            self._write("companies.json", data)
        self._changed()
        return c

    def update_company(self, ref: str, fields: dict, *, source: str = "window") -> dict:
        """An edit. Made in the window, the fields it changes become the user's (`kept`): Claude's fills skip them."""
        fields = self._renamed(fields)
        with self.lock:
            cid = self.company(ref)["id"]
            data = self._companies()
            c = next(x for x in data["companies"] if x["id"] == cid)
            name = _text(fields.get("name"), 80) if "name" in fields else ""
            same = self.look_alike(name, but=cid)[0] if name else None
            if same:
                raise ValueError(t("{name} is already on the list: {company} ({id}), added {date}", name=name,
                                   company=same["name"], id=same["id"], date=(same.get("created") or "")[:10]))
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
        fields = self._renamed(fields)
        src = fields.get("sources") if isinstance(fields.get("sources"), dict) else {}
        if nature(fields.get("nature")) and not re.match(r"https?://", _text(src.get("nature"), 500)):
            # what a track for 国央企 only goes by: never without where it was read (工商登记, the listed parent's 实控人)
            raise ValueError(t("A company's nature needs its source: sources.nature, the page it was read on (its "
                               "shareholders in the business register, or the listed company's controller). Not sure: leave nature out"))
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
            if "sites" in take:   # a fill that leaves a site's list out doesn't drop the list found before
                had = {site_key(x["url"]): x.get("list") for x in c.get("sites") or [] if x.get("list")}
                take["sites"] = [x if x.get("list") or site_key(x["url"]) not in had else {**x, "list": had[site_key(x["url"])]}
                                 for x in self._sites(take["sites"])]
            if "sites" in kept and "sites" in fields:   # the user's sites stay; only a kind nobody set yet is Claude's
                said = {site_key(x["url"]): x for x in self._sites(fields["sites"])}
                mine = [{**x, **{k: said[site_key(x["url"])][k] for k in ("kind", "system", "list")
                                 if site_key(x["url"]) in said and not x.get(k) and said[site_key(x["url"])].get(k)}}
                        for x in c.get("sites") or []]
                if mine != (c.get("sites") or []):
                    take["sites"] = mine
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

    def remove_logo(self, ref: str) -> dict:
        """The user takes the logo away (a wrong one): it stays away, Claude's fill and the site's icon skip it."""
        c = self.company(ref)
        for old in (self.dir / "logos").glob(c["id"] + ".*"):
            old.unlink(missing_ok=True)
        with self.lock:
            cs = self._companies()
            me = next(x for x in cs["companies"] if x["id"] == c["id"])
            me["logo"] = ""
            me["kept"] = sorted(set(me.get("kept") or []) | {"logo"})
            me["updated"] = _now()
            self._write("companies.json", cs)
        self._changed()
        return me

    @staticmethod
    def _builtin_events(apps: dict) -> bool:
        """Once (2026-10-26): built-in events are named by kind (they read in the interface's language), and an apply
        event with no date stops meaning 招满即止, so the ones made for every new role, never touched, go."""
        done = apps.setdefault("migrated", [])
        if "builtin-events" in done or not apps["applications"]:
            return False
        for a in apps["applications"]:
            keep = []
            for e in a.get("schedule") or []:
                kind = _builtin_kind(e.get("title") or "")
                if e.get("kind") == "apply":
                    if kind == "apply":
                        e["title"] = ""
                    e["rolling"] = False
                    if not e.get("at") and not e.get("title") and not e.get("note") and not e.get("where") and not e.get("minutes"):
                        continue   # made for every role and never set: not known, which is no event
                elif kind and kind != "apply" and not e.get("kind"):
                    e["kind"], e["title"] = kind, ""
                keep.append(e)
            if "schedule" in a:
                a["schedule"] = keep
        done.append("builtin-events")
        return True

    def _place_ids(self, apps: dict, cs: dict) -> bool:
        """Once (2026-10-27): places are picked from a list and kept by id, so they read in the interface's language.
        The ones written before are looked up in it (what it doesn't know stays as written), and a company's
        `location`, free text, becomes its headquarters city (`hq`). The files as they were are kept beside them."""
        done = apps.setdefault("migrated", [])
        if "place-ids" in done:
            return False
        for name in ("applications.json", "companies.json"):
            before = self._file(name)
            if before.exists():
                before.with_name(name.replace(".json", ".before-place-ids.json")).write_bytes(before.read_bytes())

        for a in apps["applications"]:
            if a.get("places"):
                a["places"] = _found_places(a["places"])
        for c in cs["companies"]:
            c["places"] = _found_places(c.get("places"))
            if "location" in c:
                c["hq"] = _hq(_text(c.pop("location"), 80))
            if "location" in (c.get("sources") or {}):
                c["sources"]["hq"] = c["sources"].pop("location")
            if "location" in (c.get("kept") or []):
                c["kept"] = sorted({"hq" if k == "location" else k for k in c["kept"]})
        done.append("place-ids")
        return True

    def _place_ids_again(self, apps: dict, cs: dict) -> bool:
        """Once more (2026-10-27): the first look-up read 湖南长沙 as 湖南, a city in Japan, and 印度 班加罗尔(卡纳塔克邦)
        as Khanna. The places as they were written, kept by `_place_ids`, are looked up again, on every role and
        company not changed since then (what the user picked after it stays)."""
        done = apps.setdefault("migrated", [])
        if "place-ids-2" in done:
            return False
        done.append("place-ids-2")
        kept = {n: self._file(n.replace(".json", ".before-place-ids.json")) for n in ("applications.json", "companies.json")}
        try:
            since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(kept["companies.json"].stat().st_mtime))
            old_apps = {a["id"]: a for a in json.loads(kept["applications.json"].read_text(encoding="utf-8"))["applications"]}
            old_cs = {c["id"]: c for c in json.loads(kept["companies.json"].read_text(encoding="utf-8"))["companies"]}
        except (OSError, ValueError, KeyError, TypeError):
            return True
        for a in apps["applications"]:
            o = old_apps.get(a["id"])
            if o and o.get("places") and str(a.get("updated") or "") <= since:
                a["places"] = _found_places(o["places"])
        for c in cs["companies"]:
            o = old_cs.get(c["id"])
            if not o or str(c.get("updated") or "") > since:
                continue
            if o.get("places"):
                c["places"] = _found_places(o["places"])
            if o.get("location") and "hq" not in (c.get("kept") or []):
                c["hq"] = _hq(_text(o["location"], 80))
        return True

    def link_places(self, look_up: Callable[[str], str | None]) -> int:
        """The cities written as text (the list didn't know them) that `look_up` finds now (OpenStreetMap,
        jobhunt/geo.py) become places like the others: read in the interface's language, on the map. Returns how
        many changed. The lookups run outside the lock: each one may take a second."""
        def unknown(ps: Any) -> list[str]:
            return [p["area"] for p in ps or [] if isinstance(p, dict) and not p.get("id") and p.get("kind") != "remote" and p.get("area")]

        areas = {a for x in self._apps()["applications"] + self._companies()["companies"] for a in unknown(x.get("places"))}
        areas |= {c["hq"]["area"] for c in self._companies()["companies"] if isinstance(c.get("hq"), dict) and not c["hq"].get("id")}
        found = {a: pid for a in sorted(areas) if (pid := look_up(a))}
        if not found:
            return 0
        n = 0

        def link(p: dict) -> dict:
            nonlocal n
            pid = found.get(p.get("area") or "") if not p.get("id") and p.get("kind") != "remote" else None
            if not pid:
                return p
            n += 1
            return {**p, "area": geo.english(pid), "id": pid}

        with self.lock:
            apps, cs = self._apps(), self._companies()
            for x in apps["applications"] + cs["companies"]:
                if x.get("places"):
                    x["places"] = [link(p) if isinstance(p, dict) else p for p in x["places"]]
            for c in cs["companies"]:
                if isinstance(c.get("hq"), dict) and not c["hq"].get("id") and c["hq"].get("area") in found:
                    c["hq"] = {"area": geo.english(found[c["hq"]["area"]]), "id": found[c["hq"]["area"]]}
                    n += 1
            if n:
                self._write("applications.json", apps)
                self._write("companies.json", cs)
        if n:
            self._changed()
        return n

    def migrate(self) -> None:
        """Deadlines belong to roles (2026-10-18): a company's deadline moves to its roles that have none.
        A role's next step and its date (until 2026-10-21) become something booked when they had a date,
        else a line in its notes. A role's deadline field (until 2026-10-21) becomes its apply event, which
        has no "rolling" flag any more: no date is 招满即止. Then 2026-10-26: see `_builtin_events`, and
        2026-10-27: `_place_ids` and `_place_ids_again`."""
        with self.lock:
            cs = self._companies()
            moving = {c["id"]: c.pop("deadline") for c in cs["companies"] if "deadline" in c}
            apps = self._apps()
            old = [a for a in apps["applications"] if "next" in a or "due" in a]
            fields = [a for a in apps["applications"] if "deadline" in a]
            flags = self._builtin_events(apps)
            places = self._place_ids(apps, cs) if apps["applications"] or cs["companies"] else False
            places = (self._place_ids_again(apps, cs) if apps["applications"] or cs["companies"] else False) or places
            if not moving and not old and not fields and not flags and not places:
                return
            for a in apps["applications"]:
                if moving.get(a["company"]) and not a.get("deadline"):
                    a["deadline"] = moving[a["company"]]
                if "deadline" in a:
                    day = str(a.pop("deadline") or "")
                    e = apply_event(a)
                    if not e or (day and not e.get("at")):
                        _set_apply_by(a, day)
            for a in old:
                nxt, due = _text(a.pop("next", "")), str(a.pop("due", "") or "")
                if nxt and due:
                    a.setdefault("schedule", []).append({"id": _next_eid(a), "title": nxt, "at": due, "minutes": 0, "where": "", "note": ""})
                elif nxt or due:
                    a["note"] = ((a.get("note") or "") + "\n" + " ".join(x for x in (nxt, due) if x)).strip()
            self._write("applications.json", apps)
            if moving or places:
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
        apps = self._peek("applications.json", "applications")["applications"]
        if company:
            cid = self.company(company)["id"]
            apps = [a for a in apps if a["company"] == cid]
        return apps

    def application(self, ref: str, exact: bool = False) -> dict:
        """By id ("A3"), or "Company/Role" (any case; a unique part of the role is enough, and of the company
        unless `exact`)."""
        r = str(ref or "").strip()
        apps = self.applications()
        hit = next((a for a in apps if a["id"] == r), None)
        if hit:
            return hit
        if "/" not in r:
            raise KeyError(t("No application “{ref}”: give its id (A3) or Company/Role", ref=r))
        cref, role = r.split("/", 1)
        c = self.company(cref, exact)
        mine = [a for a in apps if a["company"] == c["id"]]
        k = _key(role)
        hits = [a for a in mine if _key(a["role"]) == k] or [a for a in mine if k and k in _key(a["role"])]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise KeyError(t("“{ref}” could be {names}: say which", ref=r, names=", ".join(f"{a['role']} ({a['id']})" for a in hits)))
        raise KeyError(t("{company} has no role “{role}”", company=c["name"], role=role.strip()))

    def _app_fields(self, a: dict, fields: dict) -> None:
        for k, limit in (("role", 120), ("url", 500), ("referral", 80), ("subset", 80)):
            if k in fields:
                a[k] = _text(fields[k], limit)
        if "note" in fields:
            a["note"] = _long(fields["note"], 4000)
        if "jd" in fields:   # the posting's own text, as it was read: kept apart from the user's notes
            jd = _long(fields["jd"], 20000)
            if jd != a.get("jd") and "skills" not in fields:
                # the skills it asks for, by rules (jobhunt/skills.py): worked out again for a new description
                from .skills import VERSION, skills_of
                a["skills"], a["skills_v"] = skills_of(jd, self.lexicon()), VERSION
            if jd != a.get("jd") and "fit_ai" not in fields:
                a.pop("fit_ai", None)   # the AI's judgement was of the old description
            a["jd"] = jd
        if "fit_ai" in fields:   # the window's AI read the description against the resume (after Want to apply)
            a["fit_ai"] = _fit_ai(fields["fit_ai"])
            if not a["fit_ai"]:
                a.pop("fit_ai")
        if "skills" in fields:   # the user's own correction of them
            xs = fields["skills"] if isinstance(fields["skills"], list) else re.split(r"[,，、;；\n]", str(fields["skills"] or ""))
            a["skills"] = [k for k in dict.fromkeys(_text(x, 40) for x in xs) if k][:40]
            a["skills_set"] = True   # kept as the user left them until a new description comes in
        elif "jd" in fields:
            a.pop("skills_set", None)
        if "track" in fields:   # the track the user put it in ("": its title's)
            a["track"] = _text(fields["track"], 8)
        if "jd_read" in fields:   # when it was read from the role's page (jobhunt/reader.py), "" when typed
            a["jd_read"] = _text(fields["jd_read"], 20)
        if "apply_by" in fields:
            _set_apply_by(a, fields["apply_by"])
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
                 "order": 0, "status": status, "applied": "", "note": "", "jd": "", "subset": "",
                 "places": [], "sent": [], "schedule": [], "co_app": None, "created": _now(), "updated": _now()}
            # an apply event only when a closing date (or 招满即止) was given: not knowing is no event
            self._app_fields(a, {"channel": "site", **fields})
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

    def _skills_corrected(self, data: dict, a: dict, had: list[str]) -> None:
        """A skill the user took off one role is taken off every role, and one typed in is looked for in every
        description: the other roles' skills are worked out again, except those the user set by hand, which only
        lose the one taken off."""
        from .skills import VERSION, _fold, skills_of
        now = {_fold(x) for x in a["skills"]}
        removed = [x for x in had if _fold(x) not in now]
        added = [x for x in a["skills"] if _fold(x) not in {_fold(y) for y in had}]
        if not removed and not added:
            return
        lex = self.lexicon()
        lex.correct(removed, added)
        gone = {_fold(x) for x in removed}
        for x in data["applications"]:
            if x is a or not x.get("jd"):
                continue
            if x.get("skills_set"):
                x["skills"] = [s for s in x.get("skills") or [] if _fold(s) not in gone]
            else:
                x["skills"], x["skills_v"] = skills_of(x["jd"], lex, learn=False), VERSION

    def fill_skills(self, more: list[str] = ()) -> int:
        """The skills of every role whose description was read before skills were (2026-11-14), or by older rules,
        worked out once; never the ones the user set by hand. `more`: other descriptions to learn skills from first
        (the picked roles' pages read for ranking), when nothing was learned yet."""
        from .skills import VERSION, skills_of
        lex = self.lexicon()
        if not lex.path.exists():
            for jd in list(more) + [a.get("jd") or "" for a in self.applications()]:
                skills_of(jd, lex)
        with self.lock:
            data = self._apps()
            todo = [a for a in data["applications"] if a.get("jd") and not a.get("skills_set") and a.get("skills_v") != VERSION]
            for a in todo:
                a["skills"], a["skills_v"] = skills_of(a["jd"], lex, learn=False), VERSION
            if todo:
                self._write("applications.json", data)
        if todo:
            self._changed()
        return len(todo)

    def update_application(self, ref: str, fields: dict) -> dict:
        with self.lock:
            aid = self.application(ref)["id"]
            data = self._apps()
            a = next(x for x in data["applications"] if x["id"] == aid)
            if "role" in fields:
                role = _text(fields["role"], 120)
                if any(x["company"] == a["company"] and x["id"] != aid and _key(x["role"]) == _key(role) for x in data["applications"]):
                    raise ValueError(t("{role} is already on this company's list", role=role))
            had = list(a.get("skills") or [])
            self._app_fields(a, fields)
            if "skills" in fields:
                self._skills_corrected(data, a, had)
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
            if to not in ("saved", "withdrawn", "closed") and not a.get("applied"):
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

    def mark_sent(self, ref: str, subset: str, n: int, *, source: str = "window", where: str = "", name: str = "") -> dict:
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
            self._log(aid, "sent", frm=frm, to=a["status"], source=source, note=f"{subset} v{name or n}" + (f" · {where}" if where else ""))
        self._changed()
        return a

    # ---- what is booked: tests, interviews ------------------------------------------
    @staticmethod
    def _booked_fields(e: dict, fields: dict) -> None:
        if "title" in fields:
            title = _text(fields["title"], 60)
            # a built-in one keeps no title while it carries its own name (in whichever language it was shown)
            e["title"] = "" if e.get("kind") in BOOKED_KINDS and _builtin_kind(title) == e["kind"] else title
        if "at" in fields:   # applying may have no date (招满即止, or not known yet); everything else booked has one
            if e.get("kind") == "apply":
                e["at"], rolling = _apply_by(fields["at"])
                e["rolling"] = rolling
            else:
                e["at"] = _date(fields["at"])
        if "rolling" in fields and e.get("kind") == "apply":
            e["rolling"] = bool(fields["rolling"])
            if e["rolling"]:
                e["at"] = ""
        if "minutes" in fields:
            e["minutes"] = _minutes(fields["minutes"])
        if "where" in fields:
            e["where"] = _text(fields["where"], 300)
        if "note" in fields:
            e["note"] = _long(fields["note"], 1000)
        if not event_title(e):
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

    def _advance(self, ref: str, e: dict, source: str) -> None:
        """A round was booked: the status moves forward to it (never back, never off an outcome)."""
        to = booked_stage(e.get("title"), e.get("kind")) if e.get("kind") != "apply" else None
        a = self.application(ref)
        if to and a["status"] in STAGES and STAGES.index(to) > STAGES.index(a["status"]):
            self.set_status(a["id"], to, note=t("{what} booked", what=event_title(e)), source=source)

    def add_booked(self, ref: str, fields: dict, *, source: str = "window") -> dict:
        """Something booked for this application: a test, 一面, 二面… with its date (and time)."""
        def add(a: dict) -> dict:
            e = {"id": _next_eid(a), "title": "", "at": "", "minutes": 0, "where": "", "note": ""}
            kind = fields.get("kind") or _builtin_kind(fields.get("title") or "")   # a built-in one's name, typed or sent
            if kind == "apply":
                if apply_event(a):
                    raise ValueError(t("{role} has its “{what}” already: change that one", role=a["role"], what=t("Apply by")))
                e.update(kind="apply", rolling=False)
            elif kind in BOOKED_KINDS:
                e["kind"] = kind
            self._booked_fields(e, fields)
            a["schedule"].append(e)
            return e
        e = self._booked(ref, add)
        self._advance(ref, e, source)
        return e

    def update_booked(self, ref: str, eid: str, fields: dict, *, source: str = "window") -> dict:
        def edit(a: dict) -> dict:
            e = self._booked_one(a, eid)
            self._booked_fields(e, fields)
            return e
        e = self._booked(ref, edit)
        if "title" in fields:   # renamed to a later round (笔试 → 一面)
            self._advance(ref, e, source)
        return e

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
            or next((e for e in a["schedule"] if _key(event_title(e)) == _key(r)), None) \
            or next((e for e in a["schedule"] if e.get("kind") and e.get("kind") == _builtin_kind(r)), None)
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
                    c = self.add_company({"name": name, "site": s.get("site") or "", "note": s.get("note") or "", "different": True,
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
        of: dict[str, list[dict]] = {}   # each company's applications, gathered once (not a pass over all of them each)
        for a in apps:
            c = by_c.get(a["company"]) or {}
            rows.append({**a, "company_name": c.get("name", ""), "last": last.get(a["id"])})
            of.setdefault(a["company"], []).append(a)
        rank = {s: i for i, s in enumerate(STATE_IDS)}
        companies = []
        for c in cs:
            mine = list(of.get(c["id"]) or [])
            live = [a for a in mine if a["status"] in OPEN or a["status"] in ("offer", "hired")]
            best = max(live or mine, key=lambda a: rank.get(a["status"], 0))["status"] if mine else ""
            mine.sort(key=lambda a: a.get("order") or 0)
            companies.append({**c, "roles": len(mine), "open": sum(1 for a in mine if a["status"] in OPEN), "best": best,
                              "apps": [{"id": a["id"], "role": a["role"], "status": a["status"]} for a in mine],
                              "last": max((last[a["id"]]["at"] for a in mine if a["id"] in last), default="")})
        return {"applications": rows, "companies": companies,
                "states": [{"id": s, "label": label, "open": s in OPEN} for s, label, _ in STATES]}
