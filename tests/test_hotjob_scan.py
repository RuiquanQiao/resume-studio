"""HotJob portals read by the scan itself (2026-11-11).

Seven companies in the tracker each had a route the AI wrote for its HotJob site, 14 route pages calling one API
(found by jobhunt/systems.py). The site below answers as the real ones did when asked from Python: a form POST per
kind of hiring, its first page in the tenant's own size whatever is asked, later pages in the size asked.
Companies and addresses are made up. Each test failed on the code before HotJob was read by the scan."""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jobhunt import hotjob
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401

SU = "SU6132e87abef57c3b637dcb71"


def role(i: int, rtype: int, title: str = "") -> dict:
    return {"postId": f"66599aae1eb8051cd2{i:06x}", "postName": title or f"研发工程师{i}", "recruitType": rtype,
            "workPlaceStr": "郑州", "postTypeName": "研发类", "publishFirstDate": "2026-10-08 00:00:00",
            "publishDate": "2026-10-20 10:00:00", "endDate": "3000-01-01 23:59:59" if i % 2 else "2026-12-31 23:59:59",
            "longTermRelease": 0}


class HotJobSite:
    """A HotJob tenant over HTTP: `rows` by kind of hiring, paged five at a time as the real server does."""

    def __init__(self, rows: dict[int, list[dict]]):
        self.rows, self.asked = rows, []
        site = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body, kind="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(200, b"<html><body><div id='root'></div></body></html>", "text/html")

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                path = urllib.parse.urlsplit(self.path).path
                if path != hotjob.API + SU:
                    return self._send(404, b"<html>not here</html>", "text/html")
                if not form.get("recruitType"):
                    return self._send(200, json.dumps({"state": 1000, "msg": "error"}).encode())
                rt, n = int(form["recruitType"]), int(form["currentPage"])
                site.asked.append((rt, n))
                rows = site.rows.get(rt, [])
                # as a real tenant's: page 1 in its own size whatever is asked, later pages in the size asked
                size = 5 if n == 1 else int(form["pageSize"])
                page = {"state": 200, "data": {"positonNum": len(rows), "pageForm": {
                    "pageData": rows[(n - 1) * size:n * size], "dataCount": len(rows), "totalPage": -(-len(rows) // size),
                    "pageSize": size}}}
                self._send(200, json.dumps(page, ensure_ascii=False).encode("utf-8"))

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()


def test_a_hotjob_portal_is_read_whole_by_the_scan_itself(hunt, monkeypatch):
    """No route, no browser, no AI: the school and social pages are each read to their last page of five."""
    site = HotJobSite({1: [role(i, 1) for i in range(12)] + [role(50, 1, "2027届研发实习生")],
                       2: [role(100 + i, 2) for i in range(7)]})
    monkeypatch.setenv("STUDIO_HOTJOB_DETECT", "on")
    monkeypatch.setattr(hotjob, "WAITS", (0, 0, 0))
    try:
        ps = Postings(hunt)
        school, social = f"{site.url}/{SU}/pb/school.html", f"{site.url}/{SU}/pb/social.html"
        hunt.add_company({"name": "长风集团", "sites": [{"url": school, "kind": "campus", "system": "HotJob"},
                                                    {"url": social, "kind": "social", "system": "HotJob"}]})
        browser = FakeBrowser({})
        sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["hotjob", "hotjob"]
        res = sc.scan("长风集团")
        assert (res["found"], res["new"], res["needs"], res["short"]) == (20, 20, "", None) and not res["errors"]
        assert browser.opened == []
        # 13 roles, 5 a page; page 1 once more by the check of whether the page is on HotJob
        assert sorted(n for rt, n in site.asked if rt == 1) == [1, 1, 2, 3]
        assert sorted(n for rt, n in site.asked if rt == 2) == [1, 1, 2]
        assert not any(rt == 12 for rt, _ in site.asked)                       # a page names its own kind
        p = next(x for x in ps.all() if x["title"] == "研发工程师1")
        assert (p["url"], p["location"], p["category"], p["batch"], p["posted"], p["deadline"]) == \
            (f"{site.url}/{SU}/pb/posDetail.html?postId={role(1, 1)['postId']}&postType=campus", "郑州", "研发类",
             "campus", "2026-10-08", "")                                        # open until filled: "3000-01-01"
        assert next(x for x in ps.all() if x["title"] == "研发工程师2")["deadline"] == "2026-12-31"
        assert next(x for x in ps.all() if x["title"] == "2027届研发实习生")["batch"] == "intern"
        assert next(x for x in ps.all() if x["title"] == "研发工程师101")["batch"] == "social"
        st = ps.scan_state("C1")
        assert st["ok"] and st["how"] == "hotjob"
        # a role gone from the site is closed: the read was whole
        site.rows[2] = site.rows[2][1:]
        assert (lambda r: (r["found"], r["closed"]))(sc.scan("长风集团")) == (19, 1)
    finally:
        site.close()


def test_roles_a_route_kept_keep_their_links_and_the_route_steps_aside(hunt, monkeypatch):
    """The AI's routes linked each role its own way (postType=2, postType=society): read by the scan now, every
    role keeps the link it has, so none is closed and opened again and an application on one stays on it. The old
    route is not run: a page a built-in scan reads is never read twice."""
    site = HotJobSite({2: [role(i, 2) for i in range(6)]})
    monkeypatch.setenv("STUDIO_HOTJOB_DETECT", "on")
    monkeypatch.setattr(hotjob, "WAITS", (0, 0, 0))
    try:
        ps = Postings(hunt)
        social = f"{site.url}/{SU}/pb/social.html"
        hunt.add_company({"name": "远山能源", "sites": [{"url": social, "kind": "social"}]})
        old = [{"title": r["postName"], "url": f"{site.url}/{SU}/pb/posDetail.html?postId={r['postId']}&postType=2"}
               for r in site.rows[2]]
        ps.merge("C1", old, how="route", complete=True)
        ps.set_scan_state("C1", route={"pages": [{"url": social, "script": "async (h) => ({postings: []})"}], "sites": [social]})
        browser = FakeBrowser({social: {"postings": []}})
        sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
        res = sc.scan("远山能源")
        assert (res["found"], res["new"], res["closed"]) == (6, 0, 0) and not res["errors"]
        assert sorted(p["url"] for p in ps.all()) == sorted(x["url"] for x in old)
        assert browser.opened == []
    finally:
        site.close()


def test_only_an_address_under_a_portal_id_is_asked(hunt, monkeypatch):
    site = HotJobSite({1: [role(1, 1)]})
    monkeypatch.setenv("STUDIO_HOTJOB_DETECT", "on")
    monkeypatch.setattr(hotjob, "WAITS", (0, 0, 0))
    try:
        assert hotjob.detect(f"{site.url}/{SU}/pb/school.html") is True
        assert hotjob.detect(f"{site.url}/campus/jobs") is False                       # no portal id: never asked
        assert site.asked == [(1, 1)]
        assert hotjob.detect(f"{site.url}/SU0000000000000000000000ab/pb/school.html") is False   # answers HTML
        assert hotjob.detect(f"http://127.0.0.1:9/{SU}/pb/school.html") is None         # no answer: asked again later
        assert hotjob.kind(f"{site.url}/{SU}/pb/interns.html") == 12
        assert hotjob.kind(f"{site.url}/{SU}/mc/position/campus") == 1
        assert hotjob.kind(f"{site.url}/{SU}/pb/index.html") is None
        # a page that names no kind reads every kind
        assert len(hotjob.read(f"{site.url}/{SU}/pb/index.html")["jobs"]) == 1
    finally:
        site.close()
