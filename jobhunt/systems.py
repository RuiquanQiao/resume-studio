"""Which recorded routes look like one recruiting system: the job API their scripts call (2026-11-11).

The AI learns one careers site at a time, so it never sees that the site it is on is the seventh it has learned on
the same system. 北森 is how that went: about 120 route pages, each written by the AI for one company, all calling
`/api/Jobad/GetJobAdPageList`, before anyone noticed and jobhunt/beisen.py read them all with no AI (2026-11-05).
Two Feishu tenants on their own domains were recorded the same way before jobhunt/feishu.py.

So the code, not the AI, compares: every route page's script is reduced to the API paths it calls, and a path
that the routes of `AT_LEAST` companies call is a system worth one built-in scan for all of them. `scan-route`
says so when a new route joins such a group, and `hunt.py systems` lists every group.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable
from urllib.parse import urlsplit

AT_LEAST = 3   # two companies share a path like /api/job/list by chance (two unrelated sites did); three seldom do

# a quoted path or address in a script: '/api/...', "https://host/api/...", `/wecruit/.../${id}` (up to the ${)
_LIT = re.compile(r"""['"`]((?:https?://[A-Za-z0-9.\-]+(?::\d+)?)?/[A-Za-z0-9_\-./]*)""")
_STATIC = re.compile(r"\.(?:html?|css|js|png|jpe?g|svg|gif|ico|woff2?)$", re.I)
_LINK = re.compile(r"\b(?:url|href|link|detail\w*)\s*[:=]|\.href\b|location\.(?:origin|href)", re.I)
# a part only an API's path has: a page's path (`/campus/detail`, kept in a script's table of links) has none
_API = re.compile(r"api|graphql|search|list|query|page|^get|json|service|rest|ajax", re.I)
_ID = re.compile(r"^(?=.*\d)[A-Za-z0-9_\-]{8,}$|^\d+$")   # a tenant's id (SU6619d98e…) or a number: any value


def paths(script: str) -> set[str]:
    """The API paths a page script calls, each as a pattern: lower case, an id or number in it as `*`, one at the
    end left out (HotJob's `/wecruit/positionInfo/listPosition/<tenant>`, written in or added on). Paths of one
    part (`/jobs`), files (`/JobDetails.html`) and the pages a role's link is built on (`url: origin + '/campus/
    detail'`, or a table of them) say nothing about the system: an own site and two 北森 sites shared `/campus/detail` as a
    page, not as an API, so a path counts only with a part an API's has (api, search, list, query…)."""
    out = set()
    text = script or ""
    for m in _LIT.finditer(text):
        before = text[max(0, m.start() - 120):m.start()]
        before = re.split(r"[\n;{}]|,(?![^()]*\))", before)[-1]   # the expression the literal is in
        if _LINK.search(before):
            continue
        path = urlsplit(m.group(1)).path if m.group(1).startswith("http") else m.group(1)
        parts = ["*" if _ID.match(p) else p.lower() for p in path.split("/") if p and p != "."]
        while parts and parts[-1] == "*":
            parts.pop()
        if len(parts) < 2 or _STATIC.search(parts[-1]) or not any(_API.search(x) for x in parts) or "detail" in parts[-1]:
            continue
        out.add("/" + "/".join(parts))
    return out


def site_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def groups(pages: Iterable[tuple[str, str, str]], skip: Callable[[str, str], bool] = lambda url, path: False,
           at_least: int = AT_LEAST) -> list[dict]:
    """Route pages (company, page url, script) grouped by the API path they call: each group the routes of
    `at_least` companies share, as {"paths", "companies", "sites"}, the biggest first. Paths shared by exactly the
    same companies are one group (one site calling two APIs of its system). `skip(url, path)`: a page or path a built-in scan reads already."""
    by_path: dict[str, dict[str, set[str]]] = {}
    for company, url, script in pages:
        for p in paths(script):
            if skip(url, p):
                continue
            g = by_path.setdefault(p, {})
            g.setdefault(company, set()).add(site_of(url))
    merged: dict[tuple[str, ...], dict] = {}
    for p, cs in by_path.items():
        if len(cs) < at_least:
            continue
        key = tuple(sorted(cs))
        g = merged.setdefault(key, {"paths": [], "companies": list(key), "sites": sorted({s for v in cs.values() for s in v})})
        g["paths"].append(p)
    out = sorted(merged.values(), key=lambda g: (-len(g["companies"]), g["paths"]))
    # a group whose companies and sites all sit in a bigger one is that system's second call (北森's
    # /api/jobad/getjobadpagelist and the /campus/jobs page its scripts read too): kept once, under the bigger group
    kept: list[dict] = []
    for g in out:
        for k in kept:
            if set(g["companies"]) <= set(k["companies"]) and set(g["sites"]) <= set(k["sites"]):
                k["also"] = sorted({*k.get("also", []), *g["paths"]})
                break
        else:
            g["paths"].sort()
            kept.append(g)
    return kept


def kin(company: str, pages: Iterable[tuple[str, str, str]], others: Iterable[tuple[str, str, str]],
        skip: Callable[[str, str], bool] = lambda url, path: False, at_least: int = AT_LEAST) -> list[dict]:
    """The groups a company's route pages join: each path they call that the routes of other companies call too,
    `at_least` companies in all, as {"paths", "companies" (the others), "sites"}."""
    mine = {p for _, url, script in pages for p in paths(script) if not skip(url, p)}
    if not mine:
        return []
    rest = [(c, u, s) for c, u, s in others if c != company]
    out = []
    for g in groups([*[(company, u, s) for _, u, s in pages], *rest], skip, at_least):
        if company in g["companies"] and mine & {*g["paths"], *g.get("also", [])}:
            out.append({**g, "companies": [c for c in g["companies"] if c != company]})
    return out
