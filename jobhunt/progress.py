"""How far a scan has got, for the window to say while it reads (user, 2026-11-17: "a button that only spins tells
the user nothing; what does the industry do?"). What a long job shows elsewhere (a download, a CI run, an image
pull): the step it is on, how far along it is when the whole is known, how long it has taken, and, when a site is
slow, that it is being asked again rather than stuck.

The readers (北森, Feishu, HotJob, 阿里, 国聘, a recorded route) say each page they learn of and each that came, by
the address they read; patience.ask says each request it sends again. Only an address the scan watches is kept,
so a reader called from anywhere else (a test, the AI's check of a site) reports to no one.
"""
from __future__ import annotations

import threading
import time
from typing import Callable
from urllib.parse import urlsplit

_lock = threading.Lock()
_watched: dict[str, dict] = {}                    # address -> its tally
_tell: dict[str, Callable[[], None]] = {}         # address -> who to tell when it changes


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def _find(url: str) -> dict | None:
    """The tally an address reports to: its own, else one of the same site (a reader asks its API at the site's
    origin while the scan watches the page address it was given)."""
    got = _watched.get(url)
    if got is None:
        o = _origin(url)
        got = next((v for k, v in _watched.items() if _origin(k) == o), None)
    return got


def watch(url: str, system: str, tell: Callable[[], None]) -> dict:
    """Keep a tally of the read of `url`, on `system` ("北森", "Moka", "route"), and call `tell` on each change."""
    tally = {"url": url, "host": urlsplit(url).hostname or url, "system": system, "pages": 0, "done": 0, "rows": 0,
             "total": None, "again": None, "started": time.time()}
    with _lock:
        _watched[url] = tally
        _tell[url] = tell
    return tally


def unwatch(url: str) -> None:
    with _lock:
        _watched.pop(url, None)
        _tell.pop(url, None)


def _change(url: str, fn: Callable[[dict], None]) -> None:
    with _lock:
        tally = _find(url)
        if tally is None:
            return
        fn(tally)
        tell = _tell.get(tally["url"])
    if tell:
        tell()


def pages(url: str, n: int) -> None:
    """The read has `n` more pages to read (a first page said how many there are)."""
    _change(url, lambda x: x.update(pages=x["pages"] + max(0, n)))


def count(url: str, n: int | None) -> None:
    """The list says it has `n` roles (several lists of one site add up)."""
    if isinstance(n, int):
        _change(url, lambda x: x.update(total=(x["total"] or 0) + n))


def page(url: str, rows: int, failed: bool = False, n: int = 1) -> None:
    """A page was read, with `rows` roles on it (or didn't come: `failed`, counted as done, its roles not). `n`:
    that many pages at once, `rows` all of theirs (a recorded route's pages on one address)."""
    def f(x: dict) -> None:
        x["done"] += n
        x["rows"] += 0 if failed else rows
    _change(url, f)


def _slow(x: dict) -> dict:
    return x.setdefault("_slow", {})


def again(url: str, sent: int) -> None:
    """The site gave no answer yet, or none: this request (the calling thread's: each page is asked from a thread
    of its own) is sent for the `sent`th time. Said is the most of the requests still waiting, so the number never
    jumps back and forth between pages read side by side."""
    me = threading.get_ident()

    def f(x: dict) -> None:
        _slow(x)[me] = sent
        x["again"] = max(_slow(x).values())
    _change(url, f)


def answered(url: str) -> None:
    """This request was answered, or given up on: no longer asked again."""
    me = threading.get_ident()
    with _lock:
        tally = _find(url)
        slow = tally is not None and me in _slow(tally)
    if slow:
        def f(x: dict) -> None:
            _slow(x).pop(me, None)
            x["again"] = max(_slow(x).values(), default=None)
        _change(url, f)


def snapshot(urls: list[str]) -> list[dict]:
    with _lock:
        return [{k: v for k, v in _watched[u].items() if not k.startswith("_")} for u in urls if u in _watched]
