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