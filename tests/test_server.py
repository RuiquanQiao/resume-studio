"""The window's own HTTP server (core/server.py)."""
from __future__ import annotations

import threading
import urllib.request


def test_a_page_s_parallel_requests_are_all_served(server):
    # the page asks for a dozen scripts at once; with the default backlog of 5 Windows refused some of them
    assert server.httpd.request_queue_size >= 64
    codes, errors = [], []

    def get():
        try:
            with urllib.request.urlopen(server.url + "app.js", timeout=30) as r:
                codes.append(r.status)
        except OSError as e:
            errors.append(repr(e))

    ts = [threading.Thread(target=get) for _ in range(40)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert not errors and codes == [200] * 40

def test_file_types_come_from_python_s_own_table_not_the_registry(monkeypatch):
    # 2026-11-13: the first file the window asked for waited 0.4 s on every start while Python read every file type
    # from the Windows registry (and a registry can call .js text/plain, which a browser then won't run)
    import mimetypes

    from core import server as srv
    monkeypatch.setattr(mimetypes.MimeTypes, "read_windows_registry", lambda *a, **k: (_ for _ in ()).throw(AssertionError("registry read")))
    assert [srv.guess_type(n) for n in ("a.js", "a.css", "a.html", "a.json", "a.woff2", "a.ttf", "a.svg", "a.png", "a.webp")] == [
        "text/javascript", "text/css", "text/html", "application/json", "font/woff2", "font/ttf", "image/svg+xml", "image/png", "image/webp"]
    from pathlib import Path
    assert srv.FileResponse(Path("web/fonts/x.woff2")).content_type == "font/woff2"
