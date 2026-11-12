"""Telling the developer which recruiting systems are still read by the AI's routes (2026-11-12).

A system the app has no built-in scan for is learned by the AI one company at a time: each company costs the AI
once, and every user pays again for the same system. jobhunt/systems.py finds systems in one user's routes; this
turns them into a report the user can send, so a built-in scan gets written for everyone.

What a report says is public and about a system, never about the user: the API paths the routes of two or more
companies call (ids and numbers as `*`, as systems.py reduces them), the hosts two or more of them call those on,
and how many companies and route pages use it. Never a company's name, its own domain (it would say which companies the user
follows), notes, statuses or accounts. The user sees the whole text before anything is sent, and sends it
themselves: a GitHub issue filled in for them (needs a GitHub account), or the text copied to send any other way.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, Iterable
from urllib.parse import quote, urlsplit

from core.i18n import t

from . import systems

REPO = "https://github.com/RuiquanQiao/resume-studio"
FILE = "reported.json"    # hunt/reported.json  {"<key>": {"at", "how": "issue" | "copied" | "ignored"}}
SHARED = 2   # companies whose routes call a path before it is a platform's, and is reported (systems.py builds at 3)
_ABS = re.compile(r"""['"`](https?://[A-Za-z0-9.\-]+(?::\d+)?/[A-Za-z0-9_\-./]*)""")
def _hosts(script: str) -> dict[str, set[str]]:
    """API path (as systems.paths gives it) -> the hosts the script calls it on, for absolute addresses only."""
    out: dict[str, set[str]] = {}
    for m in _ABS.finditer(script or ""):
        for p in systems.paths(f"'{m.group(1)}'"):
            out.setdefault(p, set()).add((urlsplit(m.group(1)).hostname or "").lower())
    return out


def offers(pages: Iterable[tuple[str, str, str]], skip: Callable[[str, str], bool] = lambda url, path: False) -> list[dict]:
    """The systems worth reporting in these route pages (company, page url, script): each API path no built-in scan
    reads (`skip`) that the routes of 2 or more companies call, and of its hosts those 2 or more companies call too.
    Each as {key, paths, hosts, companies, pages}: counts, no names. Paths the same companies call are one system.

    Only what several companies share is a platform's: one company's API, its host or even its path, can name it
    (`job-api.<company>.com`, `/indexes/<company>_jobs/query`, seen in a real tracker), and a host on another domain
    than the company's sites is still often the company's own."""
    by_path: dict[str, dict] = {}
    for company, url, script in pages:
        hosts = _hosts(script)
        for p in systems.paths(script):
            if skip(url, p):
                continue
            g = by_path.setdefault(p, {"companies": set(), "pages": 0, "hosts": {}})
            g["companies"].add(company)
            g["pages"] += 1
            for host in hosts.get(p, set()):
                g["hosts"].setdefault(host, set()).add(company)
    merged: dict[tuple, dict] = {}
    for p, g in by_path.items():
        if len(g["companies"]) < SHARED:
            continue
        k = tuple(sorted(g["companies"]))
        m = merged.setdefault(k, {"paths": [], "hosts": set(), "companies": len(k), "pages": 0})
        m["paths"].append(p)
        m["hosts"].update(h for h, cs in g["hosts"].items() if len(cs) >= SHARED)
        m["pages"] = max(m["pages"], g["pages"])
    out = []
    for m in merged.values():
        paths = sorted(m["paths"])
        out.append({"key": hashlib.sha1("\n".join(paths).encode()).hexdigest()[:12], "paths": paths,
                    "hosts": sorted(m["hosts"]), "companies": m["companies"], "pages": m["pages"]})
    return sorted(out, key=lambda x: (-x["companies"], x["paths"]))


def text(items: list[dict], ui: str | None = None) -> str:
    """The report, as the user sees it and as it is sent: Markdown in the interface's language, so the user can read
    every word of what goes out (the paths and hosts are what they are in every language)."""
    lines = [t("Recruiting systems that Resume Studio still reads with routes the AI recorded, one company at a time. "
               "A built-in scan for one of them would read every company on it with no AI.", ui=ui), ""]
    for i, x in enumerate(items, 1):
        lines.append("### " + t("System {i}: {companies}, {pages}", ui=ui, i=i,
                                companies=t("{n} company", "{n} companies", ui=ui, n=x["companies"]),
                                pages=t("{n} route page", "{n} route pages", ui=ui, n=x["pages"])))
        lines += ["- " + t("API path: {path}", ui=ui, path=f"`{p}`") for p in x["paths"]]
        lines += ["- " + t("API host: {host}", ui=ui, host=f"`{h}`") for h in x["hosts"]]
        lines.append("")
    lines.append(t("Sent from Resume Studio. No company names, own domains, notes or account details are included.", ui=ui))
    return "\n".join(lines)


def issue_url(items: list[dict], body: str) -> str:
    first = items[0]["paths"][0] if items and items[0]["paths"] else "a recruiting system"
    title = f"Scan to build: {first}" + (f" and {len(items) - 1} more" if len(items) > 1 else "")
    return f"{REPO}/issues/new?title={quote(title)}&body={quote(body)}"


def load(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}
