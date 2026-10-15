"""A stand-in for a skill's own web interface (career-ops' Next.js app), for tests of core/webhost.py.

    python tests/fake_web.py <port>

It guards like career-ops' origin-guard.mjs: a browser request whose Sec-Fetch-Site is anything but
same-origin / none is refused with 403. Pages are plain HTML with a <head>, plus a button that
navigates the way a single-page app does (history.pushState). /api/echo returns the headers it got.
"""
from __future__ import annotations

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Fake career-ops</title>
<style>body{{font:14px system-ui;margin:24px}} button{{margin-right:8px}}</style></head>
<body><h1 id="page">{path}</h1>
<a id="to-1" href="/pipeline/1">Open #1</a>
<button id="push" onclick="history.pushState({{}}, '', '/pipeline/2'); document.getElementById('page').textContent = '/pipeline/2'">Go to #2</button>
<button id="home" onclick="history.pushState({{}}, '', '/'); document.getElementById('page').textContent = '/'">Home</button>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:
        pass

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            self._send(403, "application/json", json.dumps({"error": f"cross-origin request refused (Sec-Fetch-Site: {site})"}).encode())
            return False
        return True

    def do_GET(self) -> None:
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path == "/api/echo":
            self._send(200, "application/json", json.dumps(dict(self.headers.items())).encode())
            return
        if path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
            return
        self._send(200, "text/html; charset=utf-8", PAGE.format(path=html.escape(path)).encode("utf-8"))

    def do_POST(self) -> None:
        if not self._guard():
            return
        n = int(self.headers.get("Content-Length") or 0)
        self._send(200, "application/json", json.dumps({"got": self.rfile.read(n).decode("utf-8")}).encode())


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
