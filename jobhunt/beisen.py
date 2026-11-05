"""北森 (Beisen, `<tenant>.zhiye.com` and companies' own domains on it) careers sites, read by the scan itself.

Every 北森 site lists its roles through the same public API: POST `/api/Jobad/GetJobAdPageList` with the portal's id
(`"PortalId":"…"` in the page), a page index and size. Each role says its kind of hiring (`CategoryId`: 1 social,
2 campus, 3 intern, other numbers a tenant's own lists), and the answer how many roles the list has (`Count`).

Before this (2026-11-05), each 北森 site needed the AI to write a route, and how good the route was depended on
whether the AI thought of the API: 鹏芯微's read the drawn page, which shows 20 of its 24 roles and sometimes none
at all. Read here, with no browser and no AI, every role comes in, paged to the end and checked against `Count`.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

PROVIDER = "beisen"
API = "/api/Jobad/GetJobAdPageList"
PAGE = 50              # a page of 100 with every description is 300 KB, and big sites cut it off midway (传音, 长鑫)
MAX_PAGES = 120         # 6,000 roles: no 北森 list the tracker has seen comes near it (长鑫, 1,819)
TIMEOUT = 60            # its API answers in 1-25 s, and now and then not at all
TRIES = 4
WAITS = (3, 10, 20)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
KINDS = {"1": "social", "2": "campus", "3": "intern"}
NET = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)   # IncompleteRead is an HTTPException
_PORTAL = re.compile(r'"PortalId"\s*:\s*"([0-9a-fA-F-]{36})"')
_MARKS = ("Powered by Beisen", "BSGlobal", "bstatics.com", "zhiye.com", "italent")


class ReadFailed(Exception):
    pass


def _get(url: str, timeout: float, tries: int = 1, waits: tuple[float, ...] = ()) -> str:
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # read() whole: read(n) hands back what came before a cut-off answer as if it were all of it
                # (IncompleteRead is raised only on a whole read), and a cut page has no portal in it
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            if attempt == tries - 1:
                raise
            time.sleep(waits[min(attempt, len(waits) - 1)] if waits else 0)
    raise ValueError("no tries")


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json;charset=UTF-8",
                                          "Accept": "application/json, text/plain, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme or 'https'}://{u.netloc}"


def is_beisen(url: str, page: str | None = None) -> bool:
    """Whether the site is on 北森: a zhiye.com address, or a page that is one of its portals."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "zhiye.com" or host.endswith(".zhiye.com"):
        return True
    return bool(page) and bool(_PORTAL.search(page)) and any(m in page for m in _MARKS)


def detect(url: str, timeout: float = 15) -> bool | None:
    """Whether the site is a 北森 portal whose list answers. A zhiye.com address is, by its name. A company's own
    domain on 北森 (career.pxwsemi.com) is when its page names a portal and the list API gives a count for it; one
    that only looks it (job.vanchen.com, whose API answers 500) is not, and stays with the route that reads it.
    None: the site didn't answer, so nobody knows yet (asked again next time, never kept as "no")."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "zhiye.com" or host.endswith(".zhiye.com"):
        return True
    # tests' made-up sites never go out on the network (tests/conftest.py)
    if not url.startswith(("http://", "https://")) or os.environ.get("STUDIO_BEISEN_DETECT") == "off":
        return False
    try:
        page = _get(url, timeout)
    except NET:
        return None
    if not is_beisen(url, page):
        return False
    m = _PORTAL.search(page)
    try:
        d = _post(origin(url) + API, {"PageIndex": 0, "PageSize": 1, "KeyWords": "", "SpecialType": 0,
                                      "PortalId": m.group(1) if m else ""}, timeout)
    except urllib.error.HTTPError:
        return False   # it answered, with an error: its list isn't there
    except NET:
        return None
    return isinstance(d, dict) and d.get("Code") == 200 and isinstance(d.get("Count"), int)


def lists(urls: list[str]) -> list[str]:
    """Each portal once. A portal is read whole, every kind of hiring it lists (campus, intern, social, its own):
    its /campus/jobs, /social/jobs and home are the same list filtered, and the routes the AI wrote before read
    more than their address said (晶晨's 151 social roles off its campus address), so reading only the named kinds
    would close roles the user tracks. Roles of a kind they don't go for are left out by their tracks, not here."""
    out: dict[str, str] = {}
    for u in urls:
        out.setdefault(origin(u), u)
    return list(out.values())


def _date(s: Any) -> str:
    s = str(s or "")[:10]
    return s if re.fullmatch(r"20\d\d-\d\d-\d\d", s) and not s.startswith(("2222", "2099")) else ""


def _city(names: Any) -> str:
    """"广东省·深圳市" -> 深圳; "北京市" -> 北京; several cities joined."""
    out: list[str] = []
    for n in names if isinstance(names, list) else []:
        for part in str(n).split("/"):
            bits = [b.strip() for b in part.split("·") if b.strip()]
            if not bits:
                continue
            city = bits[1] if len(bits) > 1 and re.search(r"(省|自治区)$", bits[0]) else bits[0]
            city = re.sub(r"市$", "", city)
            if city and city not in out:
                out.append(city)
    return " / ".join(out)


def posting(base: str, j: dict) -> dict | None:
    if not j.get("Id") or not j.get("JobAdName"):
        return None
    cat = str(j.get("CategoryId") or "")
    path = KINDS.get(cat, cat or "jobs")
    return {"title": str(j["JobAdName"]).strip(), "url": f"{base}/{path}/detail?jobAdId={j['Id']}",
            "location": _city(j.get("LocNames")), "category": str(j.get("ClassificationOne") or j.get("Category") or ""),
            "batch": KINDS.get(cat, ""), "posted": _date(j.get("PostDate")), "deadline": _date(j.get("EndTime")),
            # its ids, for a role kept before under another spelling of its link (scan.py _known_links)
            "id": str(j["Id"]).lower(), "number": str(j.get("JobAdId") or "")}


def read(url: str, *, timeout: float = TIMEOUT, waits: tuple[float, ...] = WAITS) -> dict:
    """Every role of the portal's list: {"jobs": [...], "total": the list's own count}. Raises ReadFailed when the
    site doesn't answer or answers with something that isn't its list."""
    base = origin(url)
    try:
        page = _get(url, timeout, TRIES, waits)   # 北森 drops a connection now and then (WinError 10054)
    except NET as e:
        raise ReadFailed(f"{url} didn't open in {TRIES} tries: {e}") from e
    m = _PORTAL.search(page)
    if not m and not is_beisen(url):
        raise ReadFailed(f"{url} names no 北森 portal any more")
    portal = m.group(1) if m else ""
    rows: list[dict] = []
    total = None
    for index in range(MAX_PAGES):
        body = {"PageIndex": index, "PageSize": PAGE, "KeyWords": "", "SpecialType": 0, "PortalId": portal,
                "DisplayFields": ["Category", "Kind", "LocId", "PostDate", "EndTime", "ClassificationOne"]}
        d = None
        for attempt in range(TRIES):
            try:
                d = _post(base + API, body, timeout)
                break
            except NET as e:
                if attempt == TRIES - 1:
                    raise ReadFailed(f"its list gave no answer for page {index + 1}: {e}") from e
                time.sleep(waits[min(attempt, len(waits) - 1)] if waits else 0)
        if not isinstance(d, dict) or d.get("Code") != 200 or not isinstance(d.get("Data"), list):
            raise ReadFailed(f"its list answered with something else: {str(d)[:200]}")
        total = d.get("Count") if isinstance(d.get("Count"), int) else total
        rows += d["Data"]
        if not d["Data"] or len(d["Data"]) < PAGE or (total is not None and len(rows) >= total):
            break
    jobs = [p for p in (posting(base, j) for j in rows) if p]
    return {"jobs": jobs, "total": total, "portal": portal}
