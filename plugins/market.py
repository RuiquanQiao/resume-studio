"""The plugin market (docs/PLUGINS.md): a list in the public repository (plugins/market.json), read from GitHub and,
when that can't be reached (raw.githubusercontent.com is often blocked in mainland China), from jsDelivr.
Nothing goes out until the user opens the market. $RESUME_STUDIO_MARKET (addresses, comma-separated) replaces
the defaults: a mirror, or the tests' own server."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from . import manifest

REPO = "RuiquanQiao/resume-studio"
DEFAULTS = (f"https://raw.githubusercontent.com/{REPO}/main/plugins/market.json",
            f"https://cdn.jsdelivr.net/gh/{REPO}@main/plugins/market.json")
LOCAL = Path(__file__).resolve().parent / "market.json"   # what this copy of the app was released with
AGENT = {"User-Agent": "ResumeStudio-plugins/1"}


class MarketError(Exception):
    pass


def addresses() -> list[str]:
    env = os.environ.get("RESUME_STUDIO_MARKET", "").strip()
    return [a.strip() for a in env.split(",") if a.strip()] if env else list(DEFAULTS)


def _allowed(url: str) -> bool:
    # https only; plain http just for this computer (a test server, a local mirror)
    return bool(re.match(r"^https://", url) or re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/", url))


def _get(url: str, limit: int, timeout: float = 15.0) -> bytes:
    if not _allowed(url):
        raise MarketError(f"{url}: only https addresses")
    req = urllib.request.Request(url, headers=AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise MarketError(f"{url}: larger than expected")
    return data


def _entries(raw: Any) -> list[dict]:
    items = raw.get("plugins") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise MarketError("the market list isn't in the expected shape")
    out = []
    for e in items:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not manifest.ID.match(e["id"]):
            continue
        if not e.get("bundled") and not (isinstance(e.get("download"), str) and _allowed(e["download"])
                                         and isinstance(e.get("sha256"), str) and re.match(r"^[0-9a-f]{64}$", e["sha256"])):
            continue   # an entry that can't be installed safely isn't offered
        out.append({k: e[k] for k in ("id", "name", "description", "author", "version", "homepage", "download",
                                      "sha256", "size", "bundled") if k in e})
    return out


def listing() -> dict:
    """{plugins, source}: the market's entries, from the first address that answers; this copy's own list when
    none does (the bundled plugins, at least), with `offline` set."""
    errors = []
    for url in addresses():
        try:
            return {"plugins": _entries(json.loads(_get(url, 2 * 1024 * 1024).decode("utf-8"))), "source": url}
        except (OSError, ValueError, MarketError) as e:
            errors.append(f"{url}: {e}")
    try:
        local = _entries(json.loads(LOCAL.read_text(encoding="utf-8")))
    except (OSError, ValueError, MarketError):
        local = []
    return {"plugins": local, "source": "", "offline": True, "errors": errors}


def download(entry: dict) -> bytes:
    """The plugin's zip, its checksum checked against the market's."""
    data = _get(entry["download"], manifest.MAX_PLUGIN * 2, timeout=60.0)
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise MarketError("the download doesn't match the market's checksum; nothing was installed")
    return data
