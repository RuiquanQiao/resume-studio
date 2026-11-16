"""A recorded route's scripts read with no browser (2026-11-16).

User, 2026-11-16: "既然建立了读的管道应该是很快的……即便是对于完全公司自建的，第一遍用ai建立读的管道的时候也应该建立那种只要
对面没有改变代码就能直接不调用ai和浏览器读数据的管道". Most scripts the AI records call the site's own JSON API with
fetch; jobhunt/route_node.mjs runs them in node, sandboxed, as they would run on the page.

A script is trusted to run here only once it has read, here, what it reads in the browser (`verdict`): the first
scan of a route page runs it both ways and compares. From then on it runs here only; a run here that fails, or
reads far fewer roles than it did, goes back to the browser that scan and is checked again. What is known is kept
in hunt/replay.json by the page's address and script, so a route the AI records again starts over.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

RUNNER = Path(__file__).resolve().parent / "route_node.mjs"
FILE = "replay.json"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RECHECK_DAYS = 7      # a script that needed the page is tried here again after a week (the site may have changed)


class Stopped(Exception):
    pass


def key(url: str, script: str) -> str:
    return hashlib.sha1((url + "\n" + script).encode("utf-8")).hexdigest()[:20]


def ready() -> bool:
    return bool(shutil.which("node"))


def rows_of(out: Any) -> list | None:
    rows = out.get("postings") if isinstance(out, dict) else out
    return rows if isinstance(rows, list) else None


def run(url: str, scripts: list[str], *, timeout: float = 120, request_timeout: float = 30,
        stopping: Callable[[], bool] = lambda: False, work_dir: Path) -> list[Any]:
    """Each script's answer, or an Exception (NeedsPage when it touches the page). Killed at once on a stop."""
    work_dir.mkdir(parents=True, exist_ok=True)
    job = work_dir / f".replay-{threading.get_ident()}-{time.monotonic_ns()}.json"
    job.write_text(json.dumps({"url": url, "scripts": scripts, "timeout_ms": int(timeout * 1000),
                               "request_ms": int(request_timeout * 1000)}, ensure_ascii=False), encoding="utf-8")
    try:
        # each of a far site's addresses gets 2 s to connect, not node's 250 ms (Australia to China, 2026-11-16)
        cmd = [shutil.which("node") or "node", "--permission", f"--allow-fs-read={RUNNER}", f"--allow-fs-read={job}",
               "--network-family-autoselection-attempt-timeout=2000",
               str(RUNNER), str(job)]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace", creationflags=_NO_WINDOW) as p:
            outs: dict[str, str] = {}
            readers = [threading.Thread(target=lambda k, f: outs.__setitem__(k, f.read()), args=(k, f), daemon=True)
                       for k, f in (("out", p.stdout), ("err", p.stderr))]
            for r in readers:
                r.start()
            end = time.monotonic() + timeout * max(1, len(scripts)) + 30
            while p.poll() is None:
                if stopping():
                    p.kill()
                    raise Stopped()
                if time.monotonic() > end:
                    p.kill()
                    return [TimeoutError(f"no answer in {round(end)} s")] * len(scripts)
                time.sleep(0.1)
            for r in readers:
                r.join(5)
    finally:
        job.unlink(missing_ok=True)
    for line in reversed((outs.get("out") or "").strip().splitlines()):
        if line.startswith("{"):
            try:
                res = json.loads(line).get("results") or []
            except ValueError:
                break
            return [r["ok"] if "ok" in r else (NeedsPage(r.get("error") or "") if r.get("needs_page") else RuntimeError(r.get("error") or ""))
                    for r in res] + [RuntimeError("no answer")] * (len(scripts) - len(res))
    err = ((outs.get("err") or "").strip().splitlines() or ["no answer"])[-1]
    return [RuntimeError(err[:300])] * len(scripts)


class NeedsPage(Exception):
    pass


class Known:
    """hunt/replay.json  {"<key>": {"ok": true, "n": 48, "at": "..."}}: whether each route script reads with no
    browser, and how many roles it read when it last did."""

    def __init__(self, load: Callable[[], str | None], write: Callable[[dict], None]):
        self._load, self._write, self.lock = load, write, threading.Lock()
        self._d: dict | None = None

    def _data(self) -> dict:
        if self._d is None:
            try:
                d = json.loads(self._load() or "{}")
            except ValueError:
                d = {}
            self._d = d if isinstance(d, dict) else {}
        return self._d

    def get(self, k: str) -> dict:
        with self.lock:
            return dict(self._data().get(k) or {})

    def set(self, k: str, **v: Any) -> None:
        with self.lock:
            d = self._data()
            d[k] = {**v, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self._write(d)

    def mode(self, k: str) -> str:
        """"node": runs with no browser; "page": needs the browser (tried again after RECHECK_DAYS); "": not known."""
        e = self.get(k)
        if e.get("ok"):
            return "node"
        if e.get("ok") is False:
            at = e.get("at") or ""
            if at >= time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - RECHECK_DAYS * 86400)):
                return "page"
        return ""


def verdict(node_out: Any, page_out: Any) -> bool:
    """Whether a run here reads what the browser read: as many roles or more (never fewer than 98%, at least the
    same less 2), and the same roles (most of the browser's links or titles among them)."""
    a, b = rows_of(node_out), rows_of(page_out)
    if a is None or b is None or isinstance(node_out, dict) and node_out.get("signed_out"):
        return False
    if not b:
        return False   # the browser read none: nothing to compare against, not trusted yet
    if len(a) < len(b) - max(2, len(b) // 50):
        return False
    sig = lambda r: str((r or {}).get("url") or (r or {}).get("title") or "") if isinstance(r, dict) else str(r)
    have = {sig(r) for r in a}
    same = sum(1 for r in b if sig(r) in have)
    return same >= len(b) * 0.9
