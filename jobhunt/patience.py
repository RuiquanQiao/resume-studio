"""How long to wait for a careers site: by how far it is from the user, and by how it has answered before.

Every reader of a site (北森, Feishu, a route's pages in the browser) asks here, never a number of its own. Before
this (2026-11-11), each had one: the browser's page got 90 s and three tries (2026-11-02, a user abroad), but the
北森 check written three days later gave 15 s once, and join.huolala.cn, 19-28 s from Australia, was marked "can't
be scanned: the AI has to learn it" after every restart of the window.

Far means across the Great Firewall: a user outside mainland China reading a site in it, or one inside reading a
site outside. Where the user is comes from the computer's time zone (nothing is sent anywhere to find out); where a
site is, from its address or the system it runs on. Then each site's own answers: the slowest of its last ones,
three times over, is waited for at least, and a site that didn't answer lately gets two more tries. They are kept
in hunt/net.json, so a restart doesn't forget a slow site.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import progress

KEEP = 20          # answers kept per site
MAX_WAIT = 300     # one request never waits longer than this, however slow the site was
_CN_ZONES = {"China Standard Time", "Asia/Shanghai", "Asia/Chongqing", "Asia/Chungking", "Asia/Harbin", "Asia/Urumqi",
             "PRC", "Asia/Kashgar"}
# addresses of systems that run in mainland China, whatever the company's own domain
_CN_HOSTS = re.compile(r"(\.|^)(zhiye\.com|mokahr\.com|feishu\.cn|hotjob\.cn|italent\.cn|dayee\.com|zhaopin\.com|"
                       r"51job\.com|liepin\.com|nowcoder\.com|bytedance\.com|mioffice\.cn|iguopin\.com)$")

_lock = threading.Lock()
_path: Path | None = None
_seen: dict[str, dict] = {}
_home: str | None = None


REFUSING = (401, 403, 405, 407, 451)   # answers that turn the visitor away, not "no such page"


class Refused(Exception):
    """The site answered and turned the request away (HTTP 403, 405 …): nothing a wait or another try changes, so it
    is never asked again in the same scan. nsbd.cn answers 405 to every address outside mainland China, and the
    北森 check took it for "no answer", tried it six times and waited 228 s (2026-11-18)."""

    def __init__(self, url: str, code: int):
        super().__init__(f"HTTP {code}")
        self.url, self.code = url, code


def follow_system_proxy() -> None:
    """Read the system's proxy again, for the requests from now on. urllib reads it once, on its first request, and
    keeps it: a proxy turned on after the window opened (a VPN, an accelerator into mainland China) was never used,
    and the site went on refusing the scan (2026-11-18)."""
    urllib.request.install_opener(urllib.request.build_opener())


@dataclass(frozen=True)
class Patience:
    timeout: float
    tries: int
    waits: tuple[float, ...]


def use(path: Path | str | None) -> None:
    """Keep what sites answered in this file (hunt/net.json); None keeps it in memory only."""
    global _path, _seen
    with _lock:
        _path = Path(path) if path else None
        try:
            _seen = json.loads(_path.read_text(encoding="utf-8")) if _path and _path.exists() else {}
        except (OSError, ValueError):
            _seen = {}


def home() -> str:
    """"cn" when the computer's clock is set to mainland China's time zone, "" otherwise."""
    global _home
    if _home is None:
        zone = os.environ.get("TZ", "")
        if not zone and os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation") as k:
                    zone = str(winreg.QueryValueEx(k, "TimeZoneKeyName")[0]).strip("\x00 ")
            except OSError:
                zone = ""
        if not zone:
            try:
                zone = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
            except OSError:
                zone = ""
        _home = "cn" if zone in _CN_ZONES else ""
    return _home


def _host(url: str) -> str:
    return (urlsplit(url).netloc or url).lower()


def local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def in_china(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host.endswith(".cn") or bool(_CN_HOSTS.search(host))


def far(url: str, cn: bool = False) -> bool:
    """Across the Great Firewall from the user. `cn`: the caller knows the site runs in China (北森, Feishu)."""
    if local(url):
        return False
    return (cn or in_china(url)) != (home() == "cn")


def of(url: str, timeout: float, tries: int, waits: tuple[float, ...] = (), *, cn: bool = False) -> Patience:
    """The wait for one request to this site. `timeout`, `tries`, `waits`: what a near site gets, from its reader."""
    waits = tuple(waits)
    if far(url, cn):
        timeout, tries = max(timeout * 1.5, 90), tries + 2
        last = waits[-1] if waits else 0
        waits = waits + (last * 2, last * 4)
    with _lock:
        seen = dict(_seen.get(_host(url)) or {})
    took = seen.get("took") or []
    if took:   # the slowest of its last answers, three times over: one slow answer is never cut off at the next
        timeout = max(timeout, min(MAX_WAIT, 3 * max(took)))
    if seen.get("missed") and time.time() - seen["missed"][-1] < 14 * 86400:
        tries += 2
        last = waits[-1] if waits else 0
        waits = waits + (last, last)
    return Patience(min(timeout, MAX_WAIT), tries, waits)


HEDGE_MIN = 5.0    # a second request is never sent sooner than this
MAX_OUT = 3        # requests out at once for one ask: the first and two hedges, sent one hedge time apart


def hedge_after(url: str, p: Patience) -> float:
    """How long a request waits for its answer before the same request is sent once more beside it (a hedged
    request, "The Tail at Scale", Dean and Barroso 2013): twice the site's usual answer time, or a third of the
    timeout for a site with no answers kept yet. A site that answers or hangs, never slowly in between, costs the
    usual time, not the whole timeout: join.huolala.cn answers its list in 3-26 s or not at all, and each page that
    hung was waited for 97 s before it was asked again, so a scan of its 12 pages ran for 20 minutes and more;
    hedged, 4 pages at a time, all 561 roles came in 154 s (2026-11-17)."""
    with _lock:
        took = sorted((_seen.get(_host(url)) or {}).get("took") or [])
    usual = took[len(took) // 2] * 2 if len(took) >= 3 else p.timeout / 3
    return min(p.timeout, max(HEDGE_MIN, usual))


def ask(url: str, call: Callable[[float], Any], p: Patience, *, net: tuple[type[BaseException], ...],
        answered: tuple[type[BaseException], ...] = (), answered_is_final: bool = False) -> Any:
    """`call(timeout)` with the site's patience, tried again after a pause when it doesn't answer (`net`). Each answer
    and each miss is kept for the next wait. `answered` (an HTTP error): the site did answer; with
    `answered_is_final`, that answer stands and isn't asked again.

    A request with no answer after `hedge_after` is sent once more beside the first, and whichever answers first is
    used; the others are left to end by itself. Every caller asks a list, which reading twice changes nothing. A hedge
    counts as one of the tries, and at most MAX_OUT requests are out at once: with two, both hanging left the page
    waiting out the 97 s timeout, and join.huolala.cn took 454 s instead of 154 (2026-11-17)."""
    out: queue.Queue = queue.Queue()
    hedge = hedge_after(url, p)

    def attempt() -> None:
        start = time.time()
        try:
            got = call(p.timeout)
        except answered as e:
            took(url, time.time() - start)
            out.put(("answered", e))
        except net as e:
            missed(url)
            out.put(("net", e))
        except BaseException as e:  # noqa: BLE001  anything else is the caller's, raised as it came
            out.put(("other", e))
        else:
            took(url, time.time() - start)
            out.put(("ok", got))

    sent, live, failures = 0, 0, 0
    while True:
        if live == 0:
            if sent >= p.tries:
                raise ValueError("no tries")
            if failures:
                progress.again(url, sent + 1)   # the window says the site is slow and is asked again
                time.sleep(p.waits[min(failures - 1, len(p.waits) - 1)] if p.waits else 0)
            threading.Thread(target=attempt, daemon=True).start()
            sent, live = sent + 1, live + 1
        try:
            kind, value = out.get(timeout=hedge if live < MAX_OUT and sent < p.tries else None)
        except queue.Empty:   # no answer yet: the same request once more, beside it
            progress.again(url, sent + 1)
            threading.Thread(target=attempt, daemon=True).start()
            sent, live = sent + 1, live + 1
            continue
        live -= 1
        if kind == "ok":
            progress.answered(url)
            return value
        if kind == "other" or (kind == "answered" and answered_is_final) or (live == 0 and sent >= p.tries):
            progress.answered(url)   # given up on: no longer said as being asked again
            raise value
        failures += 1


def took(url: str, seconds: float) -> None:
    """The site answered, after this long."""
    _note(url, "took", round(seconds, 1))


def missed(url: str) -> None:
    """The site didn't answer in time."""
    _note(url, "missed", int(time.time()))


def _note(url: str, what: str, value: float) -> None:
    if local(url) and _path is None:
        return
    with _lock:
        seen = _seen.setdefault(_host(url), {})
        seen[what] = (seen.get(what) or [])[-(KEEP - 1):] + [value]
        if _path:
            try:
                tmp = _path.with_suffix(".tmp")
                tmp.write_text(json.dumps(_seen, ensure_ascii=False), encoding="utf-8")
                tmp.replace(_path)
            except OSError:
                pass
