"""A site's read route: how to read the profile without Claude, recorded by Claude the first time.

A route is a list of pages, each with a short read-only script Claude wrote and tested on that page:

    {"pages": [{"url": "https://www.linkedin.com/in/alex/details/experience/",
                "reads": ["experience"],
                "script": "async (h) => ({items: h.all('li.item').map((li) => ({section: 'experience', fields: {...}}))})"}]}

A script finds things by headings, labels, roles and text, never by generated class names, so small
redesigns don't break it. "Check for updates" opens each page and runs its script (core/browser.py
`run_script`). When a script fails, or a section that had items comes back empty, the route counts as
broken and the window offers to let Claude read the site again and record the route anew.
"""
from __future__ import annotations

from typing import Any, Callable

from core.browser import BrowserError

from .profile import validate_current
from .sites import ALL_SECTIONS, Site

READS = ["basics", *ALL_SECTIONS]


class RouteBroken(Exception):
    """The route didn't read the site: what went wrong, on which page."""

    def __init__(self, message: str, page: str = "", signed_out: bool = False):
        super().__init__(message)
        self.page = page
        self.signed_out = signed_out


def validate(data: Any, site: Site) -> tuple[dict, list[str]]:
    errs: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list) or not data["pages"]:
        return {}, ['the route must be {"pages": [{"url", "reads", "script"}, ...]}']
    pages = []
    for n, p in enumerate(data["pages"]):
        if not isinstance(p, dict):
            errs.append(f"pages[{n}] must be an object")
            continue
        url, reads, script = str(p.get("url") or ""), p.get("reads"), str(p.get("script") or "")
        if not url.startswith(("http://", "https://")) or not site.owns(url):
            errs.append(f"pages[{n}].url must be a page of {site.name} ({', '.join(site.domains)})")
        if not isinstance(reads, list) or not reads or not all(r in READS for r in reads):
            errs.append(f"pages[{n}].reads must list what the page gives, from: {', '.join(READS)}")
        if "=>" not in script and "function" not in script:
            errs.append(f"pages[{n}].script must be a function: async (h) => ({{...}})")
        pages.append({"url": url, "reads": [r for r in reads or [] if r in READS], "script": script})
    return ({"pages": pages} if not errs else {}), errs


def covers(route: dict | None) -> list[str]:
    return list(dict.fromkeys(r for p in (route or {}).get("pages") or [] for r in p.get("reads") or []))


def run(browser: Any, site: Site, route: dict, base: dict | None = None,
        progress: Callable[[int, int, str], None] | None = None) -> tuple[dict, list[str]]:
    """Read the site by the route: (what it says now, the sections that reading covers)."""
    reading: dict[str, Any] = {"url": "", "basics": {}, "items": [], "skills": []}
    pages = route.get("pages") or []
    for n, p in enumerate(pages):
        if progress:
            progress(n, len(pages), p["url"])
        try:
            here = browser.open(p["url"])
        except BrowserError as e:
            raise RouteBroken(str(e), p["url"]) from e
        if site.signed_out(here.get("url") or ""):
            raise RouteBroken(f"{site.name} asks to sign in", p["url"], signed_out=True)
        try:
            out = browser.run_script(p["script"], timeout=90)
        except BrowserError as e:
            raise RouteBroken(f"the script stopped: {e}", p["url"]) from e
        if not isinstance(out, dict):
            raise RouteBroken("the script gave no profile data", p["url"])
        for it in out.get("items") or []:
            if not isinstance(it, dict) or it.get("section") not in p["reads"]:
                raise RouteBroken(f"the script gave an item outside {', '.join(p['reads'])}", p["url"])
            reading["items"].append(it)
        if "basics" in p["reads"] and isinstance(out.get("basics"), dict):
            reading["basics"].update(out["basics"])
        if "skills" in p["reads"] and isinstance(out.get("skills"), list):
            reading["skills"] += [s for s in out["skills"] if isinstance(s, str)]
        reading["url"] = reading["url"] or here.get("url") or p["url"]
    cur, errs = validate_current(reading, site)
    if errs:
        raise RouteBroken("what the scripts read doesn't fit: " + "; ".join(errs[:3]))
    scope = covers(route)
    # a section that had entries and now reads as empty: far more likely a redesign than a wiped profile
    for sec in scope:
        if sec in ("basics", "skills"):
            had = bool((base or {}).get(sec))
            now = bool(cur.get(sec)) and (sec != "basics" or any(cur["basics"].values()))
        else:
            had = any(i.get("section") == sec for i in (base or {}).get("items") or [])
            now = any(i["section"] == sec for i in cur["items"])
        if had and not now:
            raise RouteBroken(f"{sec} came back empty")
    return cur, scope
