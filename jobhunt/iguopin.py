"""国聘 (iguopin.com) careers pages, read by the scan itself (2026-11-12).

Many state-owned companies and banks run their campus hiring on 国聘: each gets an exclusive page on a subdomain of
its own (`<x>.iguopin.com`, its campus list usually at `/job`), and all of them list their roles through one public
API, no sign-in and no browser:

1. GET `gp-api.iguopin.com/api/activity/exclusive/v1/info?domain=<x>` says the page's company (`company_id`) and its
   menu (`content`, a JSON text): each menu entry showing a job list names the projects it lists (`projectId`,
   comma separated; `/job` on every page seen).
2. POST `gp-api.iguopin.com/api/jobs/v1/project-job` with those `project_id`s, the company (`company_id_with_sub`:
   it and its branches) and a page lists the roles, with how many there are (`total`).

Before this, each company on 国聘 needed the AI to write a route, one company at a time: two companies in the tracker
had routes calling this one API (one with the project id written in, so a new project the next season would have
gone unread). Read here, every role comes in, checked against the list's own count.
"""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from . import patience

PROVIDER = "iguopin"
API = "/api/jobs/v1/project-job"
INFO = "/api/activity/exclusive/v1/info"
BASE = "https://gp-api.iguopin.com"
PAGE = 100           # the API gives up to 100 a page (asked 200, it gave 100)
MAX_PAGES = 50
TIMEOUT = 60
TRIES = 4
WAITS = (3, 10, 20)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
NET = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)
# 国聘's own parts, never a company's exclusive page
NOT_A_COMPANY = {"www", "m", "gp-api", "api", "static", "img", "cdn", "passport", "user", "admin"}
KINDS = {"校招": "campus", "社招": "social", "实习": "intern"}
GENERIC = {"其他", "其他职位", "其它", "其它职位"}


class ReadFailed(Exception):
    pass


def subdomain(url: str) -> str:
    """The company's exclusive page it is on (`starbank2027` for starbank2027.iguopin.com), "" for none."""
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith(".iguopin.com"):
        return ""
    sub = host[: -len(".iguopin.com")]
    return "" if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sub) or sub in NOT_A_COMPANY else sub


def detect(url: str) -> bool:
    """Whether the address is a company's exclusive page on 国聘: told by its address alone, nothing asked."""
    return bool(subdomain(url))


def key(url: str) -> str:
    """One exclusive page is one list, whichever of its pages the address is (its home page, /job, /notice)."""
    return subdomain(url)


def lists(urls: list[str]) -> list[str]:
    out: dict[str, str] = {}
    for u in urls:
        out.setdefault(key(u), u)
    return list(out.values())


def wait_for(url: str, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> patience.Patience:
    """How long to wait for 国聘 (jobhunt/patience.py): its servers are in China."""
    return patience.of(url, TIMEOUT if timeout is None else timeout, TRIES, WAITS if waits is None else waits, cn=True)


def _ask(path: str, body: dict | None, timeout: float, referer: str) -> Any:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"User-Agent": UA, "Content-Type": "application/json",
                                          "Accept": "application/json, text/plain, */*", "Referer": referer,
                                          "Origin": f"https://{urlsplit(referer).hostname}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def projects(info: dict, url: str) -> list[list[str]]:
    """The projects each job list of the page lists: the menu entry the address is on, or every one that lists jobs
    (an address of the home page). Each list once."""
    try:
        menu = json.loads(info.get("content") or "{}").get("params", {}).get("nav") or []
    except (ValueError, AttributeError):
        menu = []
    entries = [(str(n.get("route") or ""), str((n.get("props") or {}).get("projectId") or ""))
               for n in menu if isinstance(n, dict)]
    entries = [(r, p) for r, p in entries if p.strip()]
    path = (urlsplit(url).path or "/").rstrip("/") or "/"
    mine = [p for r, p in entries if r.rstrip("/") == path] or [p for _, p in entries]
    if not mine and info.get("project_id"):   # a page with no menu entry for its jobs: the page's own project
        mine = [str(info["project_id"])]
    out: list[list[str]] = []
    for p in mine:
        ids = [x.strip() for x in p.split(",") if x.strip()]
        if ids and ids not in out:
            out.append(ids)
    return out


def _day(s: Any) -> str:
    s = str(s or "")[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else ""


def posting(j: dict, prefix: bool) -> dict | None:
    """A role as the scan keeps it. `prefix`: the list has roles of several of the company's units (a bank's head
    office and its branches all list 信息科技类), so each title says whose it is."""
    if not isinstance(j, dict) or not str(j.get("job_name") or "").strip() or not j.get("job_id"):
        return None
    name, jid = str(j["job_name"]).strip(), str(j["job_id"])
    unit = str(j.get("company_name") or "").strip()
    category = str(j.get("category_cn") or "").strip()
    batch = next((v for k, v in KINDS.items() if k in str(j.get("nature_cn") or "") + str(j.get("recruitment_type_cn") or "")), "")
    if batch == "campus" and re.search(r"实习|intern", name, re.I):
        batch = "intern"
    places = [str(d.get("area_cn") or "").strip() for d in j.get("district_list") or [] if isinstance(d, dict)]
    return {"title": f"{unit} {name}" if prefix and unit and not name.startswith(unit) else name,
            "url": "https://www.iguopin.com/job/detail?id=" + urllib.parse.quote(jid),
            "location": "、".join(dict.fromkeys(p for p in places if p)), "category": "" if category in GENERIC else category,
            "batch": batch, "posted": _day(j.get("start_time")), "deadline": _day(j.get("end_time")), "id": jid}


def read(url: str, *, timeout: float | None = None, waits: tuple[float, ...] | None = None) -> dict:
    """Every role of the exclusive page's job lists: {"jobs", "total": the lists' own count, "portal"}. Raises
    ReadFailed when the page or a first page of its list doesn't come. A later page that doesn't come leaves the rest
    read: {"failed": why}, and the scan closes nothing on a read that wasn't whole."""
    sub = subdomain(url)
    if not sub:
        raise ReadFailed(f"{url} is no company's page on 国聘")
    p = wait_for(url, timeout, waits)

    def ask(path: str, body: dict | None, what: str) -> dict:
        try:
            d = patience.ask(url, lambda t: _ask(path, body, t, url), p, net=NET,
                             answered=(urllib.error.HTTPError, json.JSONDecodeError), answered_is_final=True)
        except urllib.error.HTTPError as e:
            raise ReadFailed(f"{what} answered HTTP {e.code}") from e
        except json.JSONDecodeError as e:
            raise ReadFailed(f"{what} answered with something that isn't JSON") from e
        except NET as e:
            raise ReadFailed(f"{what} gave no answer in {p.tries} tries: {e}") from e
        if not isinstance(d, dict) or d.get("code") != 200 or not isinstance(d.get("data"), dict):
            raise ReadFailed(f"{what} answered with something else: {str(d)[:200]}")
        return d["data"]

    info = ask(f"{INFO}?domain={urllib.parse.quote(sub)}", None, "its page's settings")
    lists_ = projects(info, url)
    if not lists_:
        raise ReadFailed("its page lists no jobs (no menu entry names a project)")
    company = str(info.get("company_id") or "")
    rows: list[dict] = []
    total, failed = 0, []
    for ids in lists_:
        body = {"page_size": PAGE, "project_id": ids, "source": "s_job_list"}
        if company:
            body["company_id_with_sub"] = company
        first = ask(API, {**body, "page": 1}, "its job list")
        rows += first.get("list") or []
        count = first.get("total") if isinstance(first.get("total"), int) else 0
        total += count
        for n in range(2, min(MAX_PAGES, -(-count // PAGE)) + 1):
            try:
                rows += ask(API, {**body, "page": n}, f"page {n} of its job list").get("list") or []
            except ReadFailed as e:
                failed.append(str(e))
    prefix = len({str(j.get("company_name") or "") for j in rows if isinstance(j, dict)}) > 1
    jobs: dict[str, dict] = {}
    for j in rows:   # a role listed in two projects, or moved up a page between two reads, once
        x = posting(j, prefix)
        if x:
            jobs.setdefault(x["id"], x)
    out = {"jobs": list(jobs.values()), "total": total, "portal": f"iguopin:{sub}"}
    if failed:
        out["failed"] = "; ".join(failed[:3]) + (f" (and {len(failed) - 3} more pages)" if len(failed) > 3 else "")
    return out
