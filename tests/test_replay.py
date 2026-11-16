"""Route scripts read with no browser (jobhunt/replay.py, jobhunt/route_node.mjs; user, 2026-11-16: "既然建立了读的管道
应该是很快的……难不成还是后台在调用浏览器"). A site served here stands for a careers site whose page calls its own API."""
from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jobhunt import replay
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")

SCRIPT = """async (h) => {
  const r = await fetch('/api/jobs?page=1', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json'}, body: '{}'});
  const d = await r.json();
  return {total: d.total, postings: d.rows.map(x => ({title: x.name, url: location.origin + '/job/' + x.id}))};
}"""


class Site:
    """A careers site: its page sets a session cookie, and its API answers only with that cookie and a Referer."""

    def __init__(self, n: int = 3):
        self.n, self.calls = n, []
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, kind="application/json", cookie=None):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                site.calls.append(("GET", self.path, self.headers.get("Cookie")))
                self._send(200, b"<html><body>jobs</body></html>", "text/html", cookie="sid=abc; Path=/")

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.calls.append(("POST", self.path, self.headers.get("Cookie")))
                if "sid=abc" not in (self.headers.get("Cookie") or "") or not self.headers.get("Referer"):
                    return self._send(403, b'{"error": "no session"}')
                rows = [{"id": i, "name": f"岗位{i}"} for i in range(site.n)]
                self._send(200, json.dumps({"total": site.n, "rows": rows}, ensure_ascii=False).encode("utf-8"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/campus/jobs"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def site():
    s = Site()
    yield s
    s.close()


def test_a_script_calling_the_sites_api_reads_with_no_browser(site, tmp_path):
    out = replay.run(site.url, [SCRIPT], work_dir=tmp_path)
    assert out[0]["total"] == 3 and [p["title"] for p in out[0]["postings"]] == ["岗位0", "岗位1", "岗位2"]
    # as a browser would: the page opened first, its cookie carried by the API call
    assert site.calls[0][0] == "GET" and site.calls[1] == ("POST", "/api/jobs?page=1", "sid=abc")
    assert list(tmp_path.iterdir()) == []                                     # its job file is gone


def test_a_script_reading_the_drawn_page_needs_the_browser(site, tmp_path):
    out = replay.run(site.url, ["async (h) => ({postings: [...document.querySelectorAll('a')].map(a => ({title: a.innerText}))})",
                                "async (h) => { await h.waitFor('.job'); return {postings: []}; }",
                                "async (h) => { throw new Error('boom'); }"], work_dir=tmp_path)
    assert isinstance(out[0], replay.NeedsPage) and isinstance(out[1], replay.NeedsPage)
    assert isinstance(out[2], RuntimeError) and not isinstance(out[2], replay.NeedsPage) and "boom" in str(out[2])


def test_a_script_cannot_reach_the_machine(site, tmp_path):
    """The scripts are data (written by an AI, one day shared by other users): node's permission model keeps them
    off the disk and from starting programs, even by climbing out of the vm through a host function."""
    target = tmp_path / "pwned.txt"
    esc = json.dumps(str(target))
    out = replay.run(site.url, [
        "async (h) => ({postings: [], p: typeof process, r: typeof require})",
        "async (h) => { const P = fetch.constructor('return process')(); P.getBuiltinModule('fs').writeFileSync(" + esc + ", 'x'); return {postings: []}; }",
        "async (h) => { const P = fetch.constructor('return process')(); P.getBuiltinModule('child_process').execSync('whoami'); return {postings: []}; }",
    ], work_dir=tmp_path / "w")
    assert out[0] == {"postings": [], "p": "undefined", "r": "undefined"}
    assert isinstance(out[1], Exception) and isinstance(out[2], Exception)
    assert not target.exists()


def test_a_scan_reads_a_route_with_no_browser_once_it_has_read_the_same(hunt, site):
    """The first scan reads the page both ways and compares; after that the browser isn't opened at all. A run here
    that later reads far fewer roles goes back to the browser that time."""
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "sites": [{"url": site.url}]})
    site.n = 50
    rows = {"total": 50, "postings": [{"title": f"岗位{i}", "url": site.url.rsplit("/", 2)[0] + f"/job/{i}"} for i in range(50)]}
    browser = FakeBrowser({site.url: rows})
    reads = []
    real = browser.read_pages
    browser.read_pages = lambda url, scripts, **kw: reads.append(url) or real(url, scripts, **kw)
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.replay = replay.Known(lambda: hunt._load(replay.FILE), lambda d: hunt._write(replay.FILE, d))
    ps.set_scan_state("C1", route={"pages": [{"url": site.url, "script": SCRIPT}], "sites": [site.url]})
    assert sc.scan("C1")["found"] == 50 and reads == [site.url]               # compared once
    assert sc.no_browser([{"url": site.url, "script": SCRIPT}])
    browser.off = True
    assert sc.scan("C1")["found"] == 50 and reads == [site.url] and browser.off  # no browser read, none started
    site.n = 49                                                                # one role closed: no second look
    assert sc.scan("C1")["found"] == 49 and reads == [site.url]
    site.n = 10                                                                # the API now answers far fewer
    browser.pages[site.url] = {"total": 10, "postings": rows["postings"][:10]}
    assert sc.scan("C1")["found"] == 10 and reads == [site.url, site.url]       # checked in the browser that time
    assert sc.no_browser([{"url": site.url, "script": SCRIPT}])                # both agree: still no browser


def test_a_script_that_needs_the_page_is_told_to_the_ai(hunt, site):
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "sites": [{"url": site.url}]})
    dom = "async (h) => ({postings: [...document.querySelectorAll('.job')].map(e => ({title: e.innerText}))})"
    browser = FakeBrowser({site.url: {"postings": [{"title": "岗位0", "url": site.url + "/0"}]}})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.replay = replay.Known(lambda: hunt._load(replay.FILE), lambda d: hunt._write(replay.FILE, d))
    r, errs = sc.save_route("C1", {"pages": [{"url": site.url, "script": dom}]})
    assert not errs
    need = sc.needs_page(r["pages"])
    assert [p["url"] for p in need] == [site.url] and "document" in need[0]["why"]


def test_a_company_whose_route_reads_the_drawn_page_is_flagged_until_a_built_in_scan_reads_it(hunt, site):
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "sites": [{"url": site.url}]})
    dom = "async (h) => ({postings: [...document.querySelectorAll('.job')].map(e => ({title: e.innerText}))})"
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({site.url: {"postings": [{"title": "岗位0", "url": site.url + "/0"}]}}), lambda: None)
    sc.replay = replay.Known(lambda: hunt._load(replay.FILE), lambda d: hunt._write(replay.FILE, d))
    assert not sc.save_route("C1", {"pages": [{"url": site.url, "script": dom}]})[1]
    assert [p["url"] for p in sc.browser_pages(hunt.company("C1"))] == [site.url]
    sc._providers[site.url] = "beisen"                  # read by a built-in scan since: the route isn't read at all
    assert sc.browser_pages(hunt.company("C1")) == []
