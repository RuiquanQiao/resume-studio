"""飞书招聘 (Feishu Jobs) careers sites on any domain, read by the scan itself.

Every Feishu tenant lists its roles through the same public API: POST `/api/v1/search/job/posts` with a page
(`limit`, `offset`) and, in a `website-path` header, the portal it is for (the first part of the site's address:
`/campus/position/list` is the campus portal, `/index/...` the experienced one). Without the header the API answers
for every portal at once.

career-ops' provider reads it only on `*.jobs.feishu.cn` and `jobs.bytedance.com`. A company that runs Feishu under
its own domain went unread: 小米集团's `xiaomi.jobs.f.mioffice.cn` (2026-11-09) was "no scan can read it", and the
AI, asked to learn it, replayed a GET of the page's signed request, got the page's HTML back, and stopped at the
10 roles on the first page of 783. Asked here with a POST, no browser and no AI, every role comes in.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

PROVIDER = "feishu"
API = "/api/v1/search/job/posts"
PAGE = 100
MAX_PAGES = 100        # 10,000 roles; 小米's experienced portal, the biggest seen, has 2,053
# read from Australia, 小米's API took 5-70 s a page whatever its size and now and then dropped the connection
# (2026-11-09): each page gets 90 s and four tries. Two pages at a time per portal: the scan reads two portals at once,
# and eight requests open together had 小米 reset the connection (WinError 10054) four times running on one page
# (2026-11-10); four in all is what career-ops checked live on Feishu.
# The resets come in bad patches, at any page size (the same page failed, then answered in 21 s, 2026-11-10): six tries
# over about four minutes ride one out.
TIMEOUT = 90
TRIES = 6
WAITS = (5, 15, 30, 60, 120)
AT_ONCE = 2
# jobs.bytedance.com turns a Windows Chrome UA away (career-ops' notes, verified live); a macOS one passes everywhere
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
NET = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)


class ReadFailed(Exception):
    pass


def origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme or 'https'}://{u.netloc}"


def portal(url: str) -> str:
    """The portal a site's address is for: its first path segment ("" for the site's home)."""
    seg = next((s for s in urlsplit(url).path.split("/") if s), "")
    return seg if re.fullmatch(r"[\w-]{1,40}", seg) else ""


def _post(url: str, body: dict, path: str, timeout: float) -> Any:
    o = origin(url)
    headers = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json, text/plain, */*",
               "Origin": o, "Referer": url}
    if path:
        headers["website-path"] = path
    req = urllib.request.Request(o + API, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _list_of(d: Any) -> list | None:
    data = d.get("data") if isinstance(d, dict) else None
    rows = data.get("job_post_list") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) and d.get("code") == 0 else None


def detect(url: str, timeout: float = 60, tries: int = 2) -> bool | None:
    """Whether the site is a Feishu tenant whose list answers, asked of the site: its job API answers with a list.
    False: it answered something else (another system, an error). None: it didn't answer, so nobody knows yet
    (asked again next time, never kept as "no"). 小米's API took over 30 s for one role, now and then (2026-11-10)."""
    # tests' made-up sites never go out on the network (tests/conftest.py)
    if not url.startswith(("http://", "https://")) or os.environ.get("STUDIO_FEISHU_DETECT") == "off":
        return False
    for attempt in range(tries):
        try:
            d = _post(url, {"limit": 1, "offset": 0, "keyword": ""}, portal(url), timeout)
            return _list_of(d) is not None
        except urllib.error.HTTPError:
            return False   # it answered, with an error: no Feishu list there
        except json.JSONDecodeError:
            return False   # it answered with a page, not a list
        except NET:
            if attempt == tries - 1:
                return None
    return None


def lists(urls: list[str]) -> list[str]:
    """Each portal of each site once: two addresses of one portal (its list and a role in it) are one read."""
    out: dict[tuple[str, str], str] = {}
    for u in urls:
        out.setdefault((origin(u), portal(u)), u)
    return list(out.values())


def _batch(rt: Any) -> str:
    names = []
    while isinstance(rt, dict) and len(names) < 4:
        names.append(f"{rt.get('name') or ''} {rt.get('en_name') or ''}".lower())
        rt = rt.get("parent")
    s = " ".join(names)
    return ("intern" if re.search(r"实习|intern", s) else "campus" if re.search(r"校|应届|campus|graduate", s)
            else "social" if re.search(r"社|experienced|social", s) else "")


def posting(base: str, path: str, p: dict) -> dict | None:
    if not isinstance(p, dict) or not p.get("title") or p.get("id") in (None, ""):
        return None
    cities = [c.get("name") for c in p.get("city_list") or [] if isinstance(c, dict) and c.get("name")]
    if not cities and isinstance(p.get("city_info"), dict) and p["city_info"].get("name"):
        cities = [p["city_info"]["name"]]
    posted = p.get("publish_time")
    # its time is milliseconds; the day is China's, whatever the computer's own time zone
    posted = time.strftime("%Y-%m-%d", time.gmtime(posted / 1000 + 8 * 3600)) if isinstance(posted, (int, float)) and posted > 0 else ""
    return {"title": str(p["title"]).strip(), "url": f"{base}/{path or 'index'}/position/{p['id']}/detail",
            "location": " / ".join(dict.fromkeys(cities)), "category": str((p.get("job_category") or {}).get("name") or ""),
            "batch": _batch(p.get("recruit_type")), "posted": posted, "deadline": "", "id": str(p["id"])}


def read(url: str, *, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> dict:
    """Every role of the portal's list: {"jobs": [...], "total": the list's own count, "portal"}. Raises ReadFailed
    when its first page doesn't come, or comes as something that isn't its list. A later page that doesn't come
    leaves the rest read: {"failed": why} with what came, never all of the portal lost for one page (小米's intern
    portal, 2026-11-10); the scan keeps them, says so, and closes nothing on a read that wasn't whole."""
    # looked up when called, not when defined: a test's shorter waits reached only the calls that passed them, and
    # the scan's own call slept 230 s through its six tries
    timeout = TIMEOUT if timeout is None else timeout
    waits = WAITS if waits is None else waits
    base, path = origin(url), portal(url)

    def page(n: int) -> tuple[list[dict], int | None]:
        for attempt in range(TRIES):
            try:
                d = _post(url, {"limit": PAGE, "offset": n * PAGE, "keyword": ""}, path, timeout)
                break
            except urllib.error.HTTPError as e:
                raise ReadFailed(f"its job list answered HTTP {e.code} for page {n + 1}") from e
            except NET as e:   # a page cut off midway is no JSON: tried again too
                if attempt == TRIES - 1:
                    raise ReadFailed(f"its job list gave no answer for page {n + 1} in {TRIES} tries: {e}") from e
                time.sleep(waits[min(attempt, len(waits) - 1)] if waits else 0)
        got = _list_of(d)
        if got is None:
            raise ReadFailed(f"its job list answered with something else: {str(d)[:200]}")
        count = (d.get("data") or {}).get("count")
        return got, count if isinstance(count, int) else None

    def later(n: int) -> list[dict] | ReadFailed:
        try:
            return page(n)[0]
        except ReadFailed as e:
            return e

    rows, total = page(0)
    failed: list[str] = []
    if total is None:   # no count: page on until a short page
        n = 1
        while len(rows) == n * PAGE and n < MAX_PAGES:
            got = later(n)
            if isinstance(got, ReadFailed):
                failed.append(str(got))
                break
            rows += got
            n += 1
    else:
        pages = min(MAX_PAGES, -(-total // PAGE))
        if pages > 1:
            with ThreadPoolExecutor(AT_ONCE) as pool:
                for got in pool.map(later, range(1, pages)):
                    if isinstance(got, ReadFailed):
                        failed.append(str(got))
                    else:
                        rows += got
    jobs = [x for x in (posting(base, path, p) for p in rows) if x]
    jobs = list({j["url"]: j for j in jobs}.values())   # a role that moved up a page between two reads, once
    out = {"jobs": jobs, "total": total, "portal": f"{base}/{path}"}
    if failed:
        out["failed"] = "; ".join(failed[:3]) + (f" (and {len(failed) - 3} more pages)" if len(failed) > 3 else "")
    return out
