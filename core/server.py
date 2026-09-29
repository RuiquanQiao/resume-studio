"""Tiny HTTP app on the standard library: JSON routes, static files, SSE.

Deliberately dependency-free so a Skill can ship its UI and start with one
`python` command on any machine that has Claude Code.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import socket
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .events import EventBus


class HttpError(Exception):
    def __init__(self, status: int, message: str, data: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.data = data


class Request:
    def __init__(self, method: str, path: str, query: dict, params: dict, body: bytes):
        self.method = method
        self.path = path
        self.query = query
        self.params = params
        self.body = body

    def json(self) -> Any:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError as e:
            raise HttpError(400, f"invalid JSON: {e}")


class FileResponse:
    def __init__(self, path: Path, content_type: str | None = None):
        self.path = path
        self.content_type = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


Handler = Callable[[Request], Any]


class App:
    def __init__(self, static_dir: Path | None = None):
        self.routes: list[tuple[str, re.Pattern, Handler]] = []
        self.static_dir = static_dir
        self.bus = EventBus()

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        """Register a handler. `{name}` in the pattern captures one path segment."""
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn: Handler) -> Handler:
            self.routes.append((method, regex, fn))
            return fn

        return deco

    # ---- serving -------------------------------------------------------
    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
                pass

            def _send_json(self, status: int, obj: Any) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _send_file(self, fr: FileResponse) -> None:
                try:
                    data = fr.path.read_bytes()
                except OSError:
                    self._send_json(404, {"error": "not found"})
                    return
                ctype = fr.content_type
                if ctype.startswith("text/") or ctype in ("application/javascript",):
                    ctype += "; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = app.bus.subscribe()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            event, payload = q.get(timeout=15)
                            msg = f"event: {event}\ndata: {payload}\n\n"
                        except queue.Empty:
                            msg = ": ping\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    app.bus.unsubscribe(q)
                    self.close_connection = True

            def _dispatch(self, method: str) -> None:
                url = urlparse(self.path)
                path = unquote(url.path)
                if method == "GET" and path == "/api/events":
                    self._sse()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                for m, regex, fn in app.routes:
                    if m != method:
                        continue
                    match = regex.match(path)
                    if not match:
                        continue
                    req = Request(method, path, parse_qs(url.query), match.groupdict(), body)
                    try:
                        result = fn(req)
                    except HttpError as e:
                        self._send_json(e.status, {"error": e.message, "data": e.data})
                        return
                    except Exception as e:  # surface bugs to the UI instead of hanging
                        traceback.print_exc()
                        self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
                        return
                    if isinstance(result, FileResponse):
                        self._send_file(result)
                    else:
                        self._send_json(200, result if result is not None else {"ok": True})
                    return
                if method == "GET" and app.static_dir is not None:
                    rel = path.lstrip("/") or "index.html"
                    target = (app.static_dir / rel).resolve()
                    if app.static_dir.resolve() in target.parents and target.is_file():
                        self._send_file(FileResponse(target))
                        return
                self._send_json(404, {"error": f"no route for {method} {path}"})

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_PUT(self) -> None:
                self._dispatch("PUT")

            def do_DELETE(self) -> None:
                self._dispatch("DELETE")

        return _H

    def serve(self, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
              on_ready: Callable[[str], None] | None = None, on_exit: Callable[[], None] | None = None) -> None:
        port = find_free_port(host, port)

        class _Server(ThreadingHTTPServer):
            # on Windows SO_REUSEADDR lets two servers share a port; never want that
            allow_reuse_address = os.name != "nt"

        httpd = _Server((host, port), self._make_handler())
        httpd.daemon_threads = True
        url = f"http://{host}:{port}/"
        print(f"  UI running at {url}  (Ctrl+C to stop)", flush=True)
        if on_ready:
            on_ready(url)
        if open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  stopped.")
        finally:
            httpd.server_close()
            if on_exit:
                on_exit()


def running_instance(state_file: Path, expect: dict) -> str | None:
    """URL of a live server recorded in `state_file` whose /api/instance matches `expect`."""
    import urllib.request

    try:
        info = json.loads(state_file.read_text(encoding="utf-8"))
        url = info["url"]
        with urllib.request.urlopen(url + "api/instance", timeout=1.5) as r:
            live = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    return url if all(live.get(k) == v for k, v in expect.items()) else None


def find_free_port(host: str, start: int, tries: int = 100) -> int:
    """Ports are plentiful: if the preferred one is taken, just take the next."""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
