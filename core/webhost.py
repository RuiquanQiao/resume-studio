"""Host a skill's own web interface inside the window.

Some skills ship a web UI of their own (career-ops has a Next.js app). Rebuilding it would be a fork,
so the window shows the real thing in a frame instead:

  - the skill's server runs as a child process on a free loopback port. On Windows it is put in a job
    object, so it (and everything it starts) ends with Resume Studio, even after a crash;
  - the frame does not load it directly but through a small proxy on its own loopback port. Such UIs
    guard against other sites with Fetch Metadata, and a frame from the window's port counts as
    "same-site" to them, so they refuse it. The proxy lets in exactly that case (a request from the
    window itself) and refuses every other site, then hands the request on as same-origin;
  - into the HTML it serves, the proxy adds one small script that tells the window which page is open
    (postMessage to the window's own origin), so the window can offer actions for that page.

Nothing of the skill's code is changed; its own guard still checks the Host header and everything else.
"""
from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .server import find_free_port

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
        "transfer-encoding", "upgrade", "content-length", "accept-encoding"}
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


@dataclass
class WebSpec:
    """How to run a skill's web server. `{port}` in argv is replaced with the port it gets."""
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    ready_path: str = "/"
    ready_timeout: float = 120.0


class StartError(Exception):
    """Preparing or starting the server failed; the message is for the user."""


# ---------------------------------------------------------------- the guard
def allow_request(headers: Any, window_ports: set[int], own_port: int) -> tuple[bool, bool]:
    """(allowed, from_window). Browsers send Sec-Fetch-Site on every request:
    same-origin / none        the framed app itself, or the user typing the address: allowed as is
    same-site                 another port on this computer: allowed only from the window's own page
    cross-site                another website: refused
    A request without it (not a browser) is passed on unchanged: the app's own guard decides."""
    site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if not site or site in ("same-origin", "none"):
        return True, False
    if site != "same-site":
        return False, False
    src = headers.get("Origin") or headers.get("Referer") or ""
    try:
        u = urlparse(src)
        port = u.port
    except ValueError:
        return False, False
    ok = u.scheme == "http" and (u.hostname or "") in _LOOPBACK and port in (window_ports | {own_port})
    return ok, ok


def bridge_script(window_origins: list[str]) -> bytes:
    """Reports the framed page's path to the window (only to the window's own origins)."""
    import json
    targets = json.dumps(window_origins)
    js = ("(function(){try{if(window.parent===window)return;var T=" + targets + ",last='';"
          "function post(){var p=location.pathname+location.search;var m={source:'resume-studio-webhost',"
          "path:p,title:document.title};last=p;for(var i=0;i<T.length;i++){try{parent.postMessage(m,T[i])}catch(e){}}}"
          "var H=window.history;['pushState','replaceState'].forEach(function(k){var f=H[k];"
          "H[k]=function(){var r=f.apply(this,arguments);setTimeout(post,0);return r}});"
          "addEventListener('popstate',post);addEventListener('load',post);"
          "setInterval(function(){if(location.pathname+location.search!==last)post()},1000);post()}catch(e){}})();")
    return ("<script>" + js + "</script>").encode("utf-8")


# ---------------------------------------------------------------- the child process
class _Job:
    """A Windows job object that kills its processes when the last handle closes (our exit)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("Read", "Write", "Other", "ReadT", "WriteT", "OtherT")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    def add(self, proc: subprocess.Popen) -> None:
        self.k32.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]

    def kill(self) -> None:
        if self.handle:
            self.k32.TerminateJobObject(self.handle, 1)
            self.k32.CloseHandle(self.handle)
            self.handle = None


class WebHost:
    """One hosted web UI: prepare (install / build), run, proxy. State changes call `on_change`."""

    def __init__(self, name: str, log_path: Path, window_ports: Callable[[], set[int]],
                 on_change: Callable[[], None] | None = None, proxy_port: int = 8870):
        self.name = name
        self.log_path = Path(log_path)
        self.window_ports = window_ports
        self.on_change = on_change or (lambda: None)
        self.state = "off"          # off | preparing | starting | running | error
        self.step = ""              # what preparing is doing (the spec's own step names)
        self.steps: dict[str, str] = {}
        self.error = ""
        self.proc: subprocess.Popen | None = None
        self.upstream: int | None = None
        self._job: _Job | None = None
        self._proxy: ThreadingHTTPServer | None = None
        self._proxy_port = proxy_port
        self._lock = threading.Lock()
        self._gen = 0               # bumps on every start/stop: a stale start thread gives up

    # ---- what the window shows -------------------------------------------
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._proxy.server_address[1]}/" if self._proxy else ""

    def status(self) -> dict:
        return {"state": self.state, "step": self.step, "steps": dict(self.steps), "error": self.error,
                "url": self.url if self.state == "running" else ""}

    def _set(self, state: str, **kw: Any) -> None:
        self.state = state
        for k, v in kw.items():
            setattr(self, k, v)
        try:
            self.on_change()
        except Exception:  # noqa: BLE001 - a listener failing must not stop the host
            pass

    def log_tail(self, n: int = 25) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    # ---- start / stop -------------------------------------------------------
    def start(self, prepare: Callable[[Callable[[str, str], None]], WebSpec], *, restart: bool = False) -> dict:
        """Prepare and start in the background (no-op while starting or running, unless `restart`)."""
        with self._lock:
            if not restart and self.state in ("preparing", "starting", "running") and (self.state != "running" or self._alive()):
                return self.status()
            self._stop_process()
            self._gen += 1
            gen = self._gen
            self.steps, self.error, self.step = {}, "", ""
            self.state = "preparing"
        self.on_change()
        threading.Thread(target=self._run, args=(prepare, gen), name=f"webhost-{self.name}", daemon=True).start()
        return self.status()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _run(self, prepare: Callable, gen: int) -> None:
        def progress(step: str, state: str) -> None:
            if gen != self._gen:
                return
            self.steps[step] = state
            self._set("preparing", step=step if state == "running" else self.step)

        try:
            spec = prepare(progress)
            if gen != self._gen:
                return
            self._set("starting", step="start")
            self._launch(spec, gen)
            self._ensure_proxy()
            if gen == self._gen:
                self._set("running", step="")
        except StartError as e:
            if gen == self._gen:
                self._stop_process()
                self._set("error", error=str(e))
        except Exception as e:  # noqa: BLE001 - surface anything to the window
            if gen == self._gen:
                self._stop_process()
                self._set("error", error=f"{type(e).__name__}: {e}")

    def _launch(self, spec: WebSpec, gen: int) -> None:
        port = find_free_port("127.0.0.1", 8890)
        argv = [a.replace("{port}", str(port)) for a in spec.argv]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "ab")
        log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(argv)}\n".encode("utf-8"))
        log.flush()
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = _NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(argv, cwd=str(spec.cwd), env={**os.environ, **spec.env}, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, **kwargs)
        except OSError as e:
            raise StartError(f"{argv[0]}: {e}")
        finally:
            log.close()   # the child has its own handle
        job = None
        if os.name == "nt":
            try:
                job = _Job()
                job.add(proc)
            except OSError:
                job = None
        with self._lock:
            if gen != self._gen:   # stopped while launching
                self.proc, self._job = proc, job
                self._stop_process()
                raise StartError("stopped")
            self.proc, self._job, self.upstream = proc, job, port
        deadline = time.time() + spec.ready_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                raise StartError(f"exited with code {proc.returncode}\n" + self.log_tail())
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{spec.ready_path}", timeout=5):
                    return
            except urllib.error.HTTPError:
                return          # it answers: that is all "ready" means here
            except (OSError, ValueError):
                time.sleep(0.4)
            if gen != self._gen:
                raise StartError("stopped")
        raise StartError(f"didn't answer within {int(spec.ready_timeout)} s\n" + self.log_tail())

    def _stop_process(self) -> None:
        proc, job = self.proc, self._job
        self.proc, self._job, self.upstream = None, None, None
        if job is not None:
            job.kill()
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                                   creationflags=_NO_WINDOW, timeout=15)
                else:
                    os.killpg(proc.pid, 15)
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self._stop_process()
        self._set("off", step="", error="")

    def shutdown(self) -> None:
        """At exit: the process and the proxy."""
        with self._lock:
            self._gen += 1
            self._stop_process()
        if self._proxy:
            self._proxy.shutdown()
            self._proxy.server_close()
            self._proxy.close_all()  # type: ignore[attr-defined]
            self._proxy = None

    # ---- the proxy ------------------------------------------------------------------
    def _ensure_proxy(self) -> None:
        if self._proxy:
            return
        host = self
        port = find_free_port("127.0.0.1", self._proxy_port)   # the same port each run keeps the app's own storage

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                pass

            def _deny(self, status: int, text: str) -> None:
                data = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _forward(self) -> None:
                ok, from_window = allow_request(self.headers, host.window_ports(), port)
                if not ok:
                    self._deny(403, "Refused: this page is only shown inside Resume Studio.")
                    return
                upstream = host.upstream
                if host.state != "running" or not upstream:
                    self._deny(503, f"{host.name} isn't running.")
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
                if from_window:
                    # checked above: the window's own page. The app sees what it would see if the
                    # window and the app shared an origin.
                    headers = {k: v for k, v in headers.items() if k.lower() not in ("sec-fetch-site", "origin")}
                    headers["Sec-Fetch-Site"] = "same-origin"
                conn = http.client.HTTPConnection("127.0.0.1", upstream, timeout=600)
                try:
                    conn.request(self.command, self.path, body=body, headers=headers)
                    resp = conn.getresponse()
                except OSError as e:
                    conn.close()
                    self._deny(502, f"{host.name}: {e}")
                    return
                try:
                    self._relay(resp)
                finally:
                    conn.close()

            def _relay(self, resp: http.client.HTTPResponse) -> None:
                ctype = resp.getheader("Content-Type") or ""
                inject = ctype.startswith("text/html") and self.command == "GET"
                self.send_response_only(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in _HOP:
                        self.send_header(k, v)
                no_body = self.command == "HEAD" or resp.status in (204, 304) or 100 <= resp.status < 200
                if no_body:
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                pending = b"" if inject else None
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                    if not chunk:
                        break
                    if pending is not None:
                        pending += chunk
                        at = _head_end(pending)
                        if at < 0 and len(pending) < 262144:
                            continue
                        if at >= 0:
                            chunk = pending[:at] + bridge_script(host.window_origins()) + pending[at:]
                        else:
                            chunk = pending
                        pending = None
                    self._chunk(chunk)
                if pending:
                    self._chunk(pending)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def _chunk(self, data: bytes) -> None:
                if data:
                    self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
                    self.wfile.flush()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

        class Server(ThreadingHTTPServer):
            allow_reuse_address = os.name != "nt"
            daemon_threads = True

            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.live: set = set()
                self.live_lock = threading.Lock()

            def process_request(self, request, client_address):
                with self.live_lock:
                    self.live.add(request)
                super().process_request(request, client_address)

            def shutdown_request(self, request):
                with self.live_lock:
                    self.live.discard(request)
                super().shutdown_request(request)

            def close_all(self) -> None:
                """Keep-alive connections would otherwise go on being served by a stopped proxy."""
                with self.live_lock:
                    conns = list(self.live)
                for sock in conns:
                    for fn in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
                        try:
                            fn()
                        except OSError:
                            pass

            def handle_error(self, request, client_address):
                if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                    return
                super().handle_error(request, client_address)

        self._proxy = Server(("127.0.0.1", port), Handler)
        threading.Thread(target=self._proxy.serve_forever, name=f"webhost-proxy-{self.name}", daemon=True).start()

    def window_origins(self) -> list[str]:
        return [f"http://{h}:{p}" for p in sorted(self.window_ports()) for h in ("127.0.0.1", "localhost")]


def _head_end(html: bytes) -> int:
    """Where to put the bridge script: right after the opening <head …> tag."""
    low = html.lower()
    i = low.find(b"<head")
    while i >= 0 and low[i + 5:i + 6] not in (b">", b" ", b"\t", b"\n", b"\r"):
        i = low.find(b"<head", i + 1)
    if i < 0:
        return -1
    j = low.find(b">", i)
    return j + 1 if j >= 0 else -1
