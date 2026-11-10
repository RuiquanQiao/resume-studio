"""阿里's talent sites (one recruiting system run by one group: each company's own `talent.<x>` domain for its
social list, and `campus-talent.alibaba.com` shared by the whole group for campus and intern lists), read by the
scan itself (2026-11-11).

Every such site lists its roles through the same API: POST `/position/search` with a JSON body naming the
`channel` (`group_official_site` for a social list, `group_overseas_official_site` for one in English,
`campus_group_official_site` for campus), a page, and for campus a `batchId` (POST `/searchCondition/listBatch`
lists them: graduate, internship, topTalentPlan). The answer says how many roles there are (`totalCount`).
It sits behind a double-submit CSRF filter, not a sign-in: any token sent as both the `XSRF-TOKEN` cookie and the
`x-xsrf-token` header passes (career-ops' alibaba provider found that; checked on each site here), so no page
is opened first.

The shared campus site lists the whole group's roles (446 graduate roles on 2026-11-11). A company's own part of
it is named in its address: `circleCode=60006`, or `filterParams={"customDept": ["YQNHYU"]}`, asked as
`circleCodes` (a company's code) or `customDeptCode` (a department's). An address of it with neither is not
read here: the group's roles would all go on one company.

Before this, each company of the group had a route the AI wrote, five companies with 12 route pages calling this
one API (found by jobhunt/systems.py), each waiting in the browser for the page's own request to carry a token.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import patience

PROVIDER = "alitalent"
API = "/position/search"
BATCHES = "/searchCondition/listBatch"
PAGE = 50
MAX_PAGES = 200        # 10,000 roles; the biggest list seen (a company's social one) has 562
TIMEOUT = 60
TRIES = 6              # from Australia, its sites reset the connection or time out now and then (2026-11-11)
WAITS = (5, 15, 30, 60, 120)
AT_ONCE = 2
SHARED = {"campus-talent.alibaba.com"}   # the whole group's campus site: read only for a company named in the address
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
NET = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)
_KINDS = {"graduate": "campus", "internship": "intern", "topTalentPlan": "campus"}


class ReadFailed(Exception):
    pass


def origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme or 'https'}://{u.netloc}"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _prefix(url: str) -> str:
    """`/zh` or `/en` when the site's addresses start with their language (`<site>/zh/off-campus/...`)."""
    m = re.match(r"^/(zh|en)(?=/|$)", urlsplit(url).path)
    return "/" + m.group(1) if m else ""


def english(url: str) -> bool:
    q = parse_qs(urlsplit(url).query)
    return _prefix(url) == "/en" or (q.get("lang") or [""])[0].lower().startswith("en")


def campus(url: str) -> bool:
    return bool(re.search(r"/campus(?:/|-|$)", urlsplit(url).path.lower())) or _host(url) in SHARED


def channel(url: str) -> str:
    return "campus_group_official_site" if campus(url) else \
        "group_overseas_official_site" if english(url) else "group_official_site"


def scope(url: str) -> dict:
    """The part of the group's list the address names: {"circleCodes": [...]} and/or {"customDeptCode": "a,b"}."""
    q = parse_qs(urlsplit(url).query)
    circles = [c for v in q.get("circleCode") or [] for c in v.split(",") if c.strip()]
    depts: list[str] = [d for v in q.get("deptCodes") or [] for d in v.split(",") if d.strip()]
    for raw in q.get("filterParams") or []:
        try:
            f = json.loads(raw)
        except ValueError:
            continue
        for v in (f.get("customDept") or []) if isinstance(f, dict) else []:
            (circles if re.fullmatch(r"\d{5}", str(v)) else depts).append(str(v))
        for v in (f.get("circleCode") or f.get("circleCodes") or []) if isinstance(f, dict) else []:
            circles.append(str(v))
    out: dict[str, Any] = {}
    if circles:
        out["circleCodes"] = sorted(set(circles))
    if depts:
        out["customDeptCode"] = ",".join(sorted(set(depts)))
    return out


def batch_of(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("batchId") or [""])[0]


def key(url: str) -> tuple:
    """One list: a site's social or campus list, in its language, for the part of the group the address names.
    A campus address's `batchId` is only the batch its page shows first: the company's campus list is every batch
    (an AI's route read a company's graduate and both intern batches from its graduate page: 82 roles, 45 of them
    graduate ones, 2026-11-11)."""
    return origin(url), _prefix(url), channel(url), json.dumps(scope(url), sort_keys=True)


def lists(urls: list[str]) -> list[str]:
    out: dict[tuple, str] = {}
    for u in urls:
        out.setdefault(key(u), u)
    return list(out.values())


def _post(url: str, path: str, body: dict, timeout: float) -> Any:
    """The API under the site's language when its addresses carry one: one company's /zh page asks
    /zh/position/search (301 roles on 2026-11-11), and /position/search answers 8 of them; its /en site has no
    /en/position/search (404) and asks the plain one."""
    o, token = origin(url), str(uuid.uuid4())
    for at in dict.fromkeys([_prefix(url) + path, path]):
        req = urllib.request.Request(o + at, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"User-Agent": UA, "Content-Type": "application/json",
                                              "Accept": "application/json, text/plain, */*", "Origin": o, "Referer": url,
                                              "Cookie": f"XSRF-TOKEN={token}", "x-xsrf-token": token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code != 404 or at == path:
                raise
    raise AssertionError("unreachable")


def _content(d: Any) -> dict | None:
    c = d.get("content") if isinstance(d, dict) and d.get("success") else None
    return c if isinstance(c, dict) else None


def _base(url: str) -> dict:
    return {"channel": channel(url), "language": "en" if english(url) else "zh"}


def wait_for(url: str, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> patience.Patience:
    """How long to wait for this site (jobhunt/patience.py): its servers are in China."""
    return patience.of(url, TIMEOUT if timeout is None else timeout, TRIES, WAITS if waits is None else waits, cn=True)


def detect(url: str, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> bool | None:
    """Whether the site is one of these whose list answers: its `/position/search` answers with a list (a campus
    list: it lists batches). False: it answered something else, a campus page has no batch, or it is the group's
    shared campus site with no company named in the address. None:
    it didn't answer, so nobody knows yet (asked again next time, never kept as "no")."""
    if _host(url) in SHARED and not scope(url):
        return False
    # tests' made-up sites never go out on the network (tests/conftest.py)
    if not url.startswith(("http://", "https://")) or os.environ.get("STUDIO_ALITALENT_DETECT") == "off":
        return False
    p = patience.of(url, TIMEOUT if timeout is None else timeout, 2, waits or (), cn=True)
    # a campus list is asked for its batches: searched with none, it answers `datas: null` for any site
    path, body = (BATCHES, _base(url)) if campus(url) else (API, {**_base(url), **scope(url), "pageIndex": 1, "pageSize": 1, "key": ""})
    try:
        d = patience.ask(url, lambda t: _post(url, path, body, t), p, net=NET,
                         answered=(urllib.error.HTTPError, json.JSONDecodeError), answered_is_final=True)
    except (urllib.error.HTTPError, json.JSONDecodeError):
        return False
    except NET:
        return None
    c = _content(d)
    if c is None:
        return False
    # a company's own campus page with no batch of its own fronts the group's campus site: its roles are there,
    # read by the route (or the shared site's address) that reads them. Taken as read here, it read none and a
    # scan closed the 82 campus roles its route had read (2026-11-11, on a copy of the tracker)
    return any(isinstance(b, dict) and b.get("id") for k, v in c.items() if k != "sequence" and isinstance(v, list)
               for b in v) if campus(url) else isinstance(c.get("datas"), list)


def _day(ms: Any) -> str:
    # milliseconds; the day is China's, whatever the computer's own time zone
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000 + 8 * 3600)) if isinstance(ms, (int, float)) and ms > 0 else ""


def posting(url: str, batch: str, p: dict) -> dict | None:
    if not isinstance(p, dict) or not str(p.get("name") or "").strip() or p.get("id") in (None, ""):
        return None
    pid = str(p["id"])
    if _host(url) in SHARED:
        link = f"{origin(url)}/campus/position/{pid}"
    elif campus(url):
        link = f"{origin(url)}{_prefix(url)}/campus/position-detail?positionId={pid}"
    else:
        link = f"{origin(url)}{_prefix(url)}/off-campus/position-detail?positionId={pid}"
    cats = p.get("categories") if isinstance(p.get("categories"), list) else []
    return {"title": str(p["name"]).strip(), "url": link,
            "location": " / ".join(str(x) for x in p.get("workLocations") or [] if x),
            "category": str(p.get("categoryName") or (cats[0] if cats else "") or ""),
            "batch": batch, "posted": _day(p.get("publishTime")), "deadline": "", "id": pid}


def read(url: str, *, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> dict:
    """Every role of the list: {"jobs", "total": the list's own count, "portal"}. A campus list is each of its
    batches (the one its address names, else all the site lists). Raises ReadFailed when a first page doesn't
    come or isn't a list; a later page that doesn't come leaves the rest read: {"failed": why}."""
    p = wait_for(url, timeout, waits)

    def ask(path: str, body: dict, what: str) -> dict:
        try:
            d = patience.ask(url, lambda t: _post(url, path, body, t), p, net=NET, answered=(urllib.error.HTTPError,),
                             answered_is_final=True)
        except urllib.error.HTTPError as e:
            raise ReadFailed(f"its job list answered HTTP {e.code} for {what}") from e
        except NET as e:
            raise ReadFailed(f"its job list gave no answer for {what} in {p.tries} tries: {e}") from e
        c = _content(d)
        if c is None:
            raise ReadFailed(f"its job list answered with something else for {what}: {str(d)[:200]}")
        return c

    base = {**_base(url), **scope(url), "key": ""}
    # the lists to read: (body, the batch's kind of hiring)
    if campus(url):
        got = ask(BATCHES, _base(url), "its batches")
        kinds: dict[str, str] = {}
        plan: list[tuple[dict, str]] = []
        for name in got.get("sequence") or [k for k in got if k != "sequence"]:
            for b in got.get(name) or []:
                if not isinstance(b, dict) or not b.get("id"):
                    continue
                bid = str(b["id"])
                # 阿里星 is the graduate batch with one more filter: its roles are the graduate list's
                if bid in kinds:
                    continue
                kinds[bid] = _KINDS.get(name, "campus")
                plan.append(({**base, "batchId": b["id"]}, kinds[bid]))
        if not plan and batch_of(url):   # a batch the site doesn't list (any more): the one the address names
            plan = [({**base, "batchId": batch_of(url)}, "campus")]
        if not plan:   # a site with no batches lists its campus roles under none
            plan = [({**base, "batchId": ""}, "campus")]
    else:
        plan = [({**base, "batchId": ""}, "social")]

    # each list of the plan: its kind, its own count, the rows of each page that came
    counts: list[int | None] = []
    pages_of: list[dict[int, list]] = []
    failed: list[str] = []
    rest: list[tuple[int, int]] = []
    for i, (body, kind) in enumerate(plan):
        first = ask(API, {**body, "pageIndex": 1, "pageSize": PAGE}, "page 1")
        count = first.get("totalCount") if isinstance(first.get("totalCount"), int) else None
        counts.append(count)
        pages_of.append({1: first.get("datas") or []})
        pages = min(MAX_PAGES, -(-count // PAGE)) if count else 1
        rest += [(i, n) for n in range(2, pages + 1)]

    def later(job: tuple[int, int]) -> tuple[int, int, list] | ReadFailed:
        i, n = job
        try:
            return i, n, ask(API, {**plan[i][0], "pageIndex": n, "pageSize": PAGE}, f"page {n}").get("datas") or []
        except ReadFailed as e:
            return e

    if rest:
        with ThreadPoolExecutor(AT_ONCE) as pool:
            for r in pool.map(later, rest):
                if isinstance(r, ReadFailed):
                    failed.append(str(r))
                else:
                    pages_of[r[0]][r[1]] = r[2]
    total = 0
    jobs: dict[str, dict] = {}
    for i, (body, kind) in enumerate(plan):
        got = [x for n in sorted(pages_of[i]) for x in pages_of[i][n]]
        want = counts[i] or 0
        # every page came and the last wasn't full: the site has no more to give, whatever its count says
        # (one company's /zh list said 8 and gave 7 on page 1, nothing on page 2, 2026-11-11)
        whole = len(pages_of[i]) == max(1, -(-want // PAGE)) and len(pages_of[i][max(pages_of[i])]) < PAGE
        total += min(want, len(got)) if whole else want
        for x in got:
            j = posting(url, kind, x)
            if j and j["id"] in jobs:   # one role in two batches: counted twice by the lists, read once
                total -= 1
            elif j:
                jobs[j["id"]] = j
    out = {"jobs": list(jobs.values()), "total": total,
           "portal": "|".join(map(str, key(url)))}
    if failed:
        out["failed"] = "; ".join(failed[:3]) + (f" (and {len(failed) - 3} more pages)" if len(failed) > 3 else "")
    return out
