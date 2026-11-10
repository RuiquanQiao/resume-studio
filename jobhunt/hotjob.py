"""HotJob (e成科技's wecruit, `wecruit.hotjob.cn`, `<x>.hotjob.cn` and companies' own domains on it) careers sites,
read by the scan itself (2026-11-11).

Every HotJob site lists its roles through the same public API: POST `/wecruit/positionInfo/listPosition/<SU…>`
(the tenant's portal id, the first part of every address on the site) with a form body naming the kind of hiring
(`recruitType`: 1 campus, 2 social, 12 intern, as each portal page's own script has it written in: school.html,
social.html, interns.html) and a page. The answer says how many roles the list has (`dataCount`) and how many pages
(`totalPage`). Its first page comes in the tenant's own size whatever is asked (5 on one, 12 on another), and
later pages in the size asked: asked for 50, a tenant's page 2 was roles 51-100 of a list of 49, and 37 of its 49
campus roles went unread. So every later page is asked in the first page's size.

Before this, each HotJob site needed the AI to write a route, one company at a time: 7 companies in the
tracker had 14 route pages calling this one API, found by jobhunt/systems.py. Read here, with no browser and
no AI, every role comes in, checked against the list's own count.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from . import patience

PROVIDER = "hotjob"
API = "/wecruit/positionInfo/listPosition/"
PAGE = 50            # asked for the first page; the server answers in its own size, used for every later page
MAX_PAGES = 600      # 3,000 roles at 5 a page; the biggest list seen has 515
TIMEOUT = 60
TRIES = 4
WAITS = (3, 10, 20)
AT_ONCE = 4          # pages asked at once: 103 pages of 5 one after another took 100 s from Australia
KINDS = {1: "campus", 2: "social", 12: "intern"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
NET = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)
_SU = re.compile(r"^/(SU[0-9a-fA-F]{24})(?:/|$)")


class ReadFailed(Exception):
    pass


def origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme or 'https'}://{u.netloc}"


def tenant(url: str) -> str:
    """The portal id the address is under (`SU6132e87abef57c3b637dcb71`), "" when it has none."""
    m = _SU.match(urlsplit(url).path)
    return m.group(1) if m else ""


def kind(url: str) -> int | None:
    """The kind of hiring the portal page is for, as its own script asks the API; None: every kind is read."""
    path = urlsplit(url).path.lower()
    if re.search(r"/(?:school|campus)(?:\.html|/|$)", path):
        return 1
    if re.search(r"/(?:social|society)(?:\.html|/|$)", path):
        return 2
    if re.search(r"/interns?(?:\.html|/|$)", path):
        return 12
    return None


def _post(url: str, rtype: int, page: int, timeout: float, size: int | None = None) -> Any:
    o = origin(url)
    body = urllib.parse.urlencode({"isFrompb": "true", "recruitType": str(rtype), "pageSize": str(size or PAGE),
                                   "currentPage": str(page)}).encode("utf-8")
    req = urllib.request.Request(f"{o}{API}{tenant(url)}?iSaJAx=isAjax&request_locale=zh_CN", data=body, method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                          "Accept": "application/json, text/plain, */*", "Origin": o, "Referer": url,
                                          "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _form(d: Any) -> dict | None:
    data = d.get("data") if isinstance(d, dict) else None
    form = data.get("pageForm") if isinstance(data, dict) else None
    ok = isinstance(form, dict) and str(d.get("state")) == "200"
    return form if ok and isinstance(form.get("pageData") or [], list) else None


def wait_for(url: str, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> patience.Patience:
    """How long to wait for this HotJob site (jobhunt/patience.py): its servers are in China."""
    return patience.of(url, TIMEOUT if timeout is None else timeout, TRIES, WAITS if waits is None else waits, cn=True)


def detect(url: str, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> bool | None:
    """Whether the site is a HotJob portal whose list answers: an address under a portal id (`/SU…/`) whose job API
    answers with a list. False: no portal id, or it answered something else. None: it didn't answer, so nobody knows
    yet (asked again next time, never kept as "no")."""
    if not tenant(url):
        return False
    # tests' made-up sites never go out on the network (tests/conftest.py)
    if not url.startswith(("http://", "https://")) or os.environ.get("STUDIO_HOTJOB_DETECT") == "off":
        return False
    p = wait_for(url, timeout, waits or ())
    try:
        d = patience.ask(url, lambda t: _post(url, kind(url) or 1, 1, t), p, net=NET,
                         answered=(urllib.error.HTTPError, json.JSONDecodeError), answered_is_final=True)
        return _form(d) is not None
    except (urllib.error.HTTPError, json.JSONDecodeError):
        return False
    except NET:
        return None


def lists(urls: list[str]) -> list[str]:
    """Each portal page once: its school, social and intern pages are three lists (one company's portals listed different roles)."""
    out: dict[tuple[str, str, int | None], str] = {}
    for u in urls:
        out.setdefault(key(u), u)
    return list(out.values())


def key(url: str) -> tuple[str, str, int | None]:
    return origin(url), tenant(url), kind(url)


def _day(s: Any) -> str:
    s = str(s or "")[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else ""


def posting(url: str, rtype: int, p: dict) -> dict | None:
    if not isinstance(p, dict) or not str(p.get("postName") or "").strip() or not p.get("postId"):
        return None
    title, pid = str(p["postName"]).strip(), str(p["postId"])
    rt = p.get("recruitType") if p.get("recruitType") in KINDS else rtype
    batch = KINDS.get(rt, "")
    if batch == "campus" and re.search(r"实习|intern", title, re.I):
        batch = "intern"
    end = _day(p.get("endDate"))
    return {"title": title,
            "url": f"{origin(url)}/{tenant(url)}/pb/posDetail.html?postId={urllib.parse.quote(pid)}&postType={batch or rt}",
            "location": str(p.get("workPlaceStr") or "").strip(), "category": str(p.get("postTypeName") or "").strip(),
            "batch": batch, "posted": _day(p.get("publishFirstDate") or p.get("publishDate")),
            # a role open until filled says it ends in 3000
            "deadline": end if end and end < "2099" else "", "id": pid.lower()}


def read(url: str, *, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> dict:
    """Every role of the portal page's list (each kind of hiring, for a page that names none): {"jobs", "total": the
    list's own count, "portal"}. Raises ReadFailed when a first page doesn't come or isn't a list. A later page that
    doesn't come leaves the rest read: {"failed": why}, and the scan closes nothing on a read that wasn't whole."""
    p = wait_for(url, timeout, waits)
    rtypes = [kind(url)] if kind(url) else list(KINDS)

    def page(rt: int, n: int, size: int | None = None) -> dict:
        try:
            d = patience.ask(url, lambda t: _post(url, rt, n, t, size), p, net=NET, answered=(urllib.error.HTTPError,),
                             answered_is_final=True)
        except urllib.error.HTTPError as e:
            raise ReadFailed(f"its job list answered HTTP {e.code} for page {n}") from e
        except NET as e:
            raise ReadFailed(f"its job list gave no answer for page {n} in {p.tries} tries: {e}") from e
        form = _form(d)
        if form is None:
            raise ReadFailed(f"its job list answered with something else: {str(d)[:200]}")
        return form

    def later(job: tuple[int, int, int]) -> tuple[int, list] | ReadFailed:
        try:
            return job[0], page(*job).get("pageData") or []
        except ReadFailed as e:
            return e

    rows: list[tuple[int, dict]] = []
    total, failed, rest = 0, [], []
    for rt in rtypes:
        first = page(rt, 1)
        rows += [(rt, x) for x in first.get("pageData") or []]
        count = first.get("dataCount")
        total += count if isinstance(count, int) else 0
        pages = first.get("totalPage") if isinstance(first.get("totalPage"), int) else 1
        size = first.get("pageSize") if isinstance(first.get("pageSize"), int) and first["pageSize"] > 0             else len(first.get("pageData") or []) or PAGE
        rest += [(rt, n, size) for n in range(2, min(MAX_PAGES, pages) + 1)]
    if rest:
        with ThreadPoolExecutor(AT_ONCE) as pool:
            for got in pool.map(later, rest):
                if isinstance(got, ReadFailed):
                    failed.append(str(got))
                else:
                    rows += [(got[0], x) for x in got[1]]
    jobs: dict[str, dict] = {}
    for rt, x in rows:   # a role that moved up a page between two reads, or listed under two kinds, once
        j = posting(url, rt, x)
        if j:
            jobs.setdefault(j["id"], j)
    out = {"jobs": list(jobs.values()), "total": total, "portal": f"{origin(url)}/{tenant(url)}/{kind(url) or ''}"}
    if failed:
        out["failed"] = "; ".join(failed[:3]) + (f" (and {len(failed) - 3} more pages)" if len(failed) > 3 else "")
    return out
