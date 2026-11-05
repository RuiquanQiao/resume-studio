"""北森 portals read by the scan itself, and routes checked before they are kept (鹏芯微, 2026-11-05).

鹏芯微's careers site lists 24 roles. The AI's route read the drawn page, which shows 20 of them and sometimes none;
`scan-route` kept it anyway and only said "20 of 24" after, and the next scan's "read none" was shown as "the site
has probably changed". Each test here failed on the code before the fix."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.i18n import t
from jobhunt import beisen
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401


class BeisenSite:
    """A 北森 portal over HTTP, as career.pxwsemi.com is: a page that names the portal, and a list API that pages
    through `rows` (`Count` is the whole list). `drops`: how many requests to cut off before answering."""

    def __init__(self, rows: list[dict], portal: str = "857dacc4-aa18-4775-b74a-eebb8f74926b"):
        self.rows, self.portal, self.asked, self.drops = rows, portal, [], 0
        site = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body, kind):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _dropped(self):
                if site.drops:
                    site.drops -= 1
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")   # promised, never sent: IncompleteRead
                    self.end_headers()
                    self.wfile.write(b"{")
                    return True
                return False

            def do_GET(self):
                if self._dropped():
                    return
                page = ('<html><head><script>var BSGlobal = {"PortalId":"' + site.portal + '"};</script></head>'
                        '<body><div id="app"></div>Powered by Beisen</body></html>')
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self._dropped():
                    return
                site.asked.append(body)
                if self.path != beisen.API or body.get("PortalId") != site.portal:
                    return self._send(500, b"{}", "application/json")
                n, size = body["PageIndex"], body["PageSize"]
                page = {"Code": 200, "Message": "operation success", "Count": len(site.rows),
                        "Data": site.rows[n * size:(n + 1) * size]}
                self._send(200, json.dumps(page, ensure_ascii=False).encode("utf-8"), "application/json")

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()


def role(i: int, kind: str = "2") -> dict:
    return {"Id": f"6e5432f5-1cd4-46e8-9ee9-{i:012d}", "JobAdId": 351580000 + i, "JobAdName": f"27届-工程师{i}(J{10600 + i})",
            "CategoryId": kind, "LocNames": ["广东省·深圳市"], "PostDate": "2026-10-27T09:00:00", "EndTime": "2222-02-02T00:00:00",
            "ClassificationOne": None, "Category": "校园招聘"}


def test_a_beisen_portal_is_read_whole_by_the_scan_itself(hunt, monkeypatch):
    """No route, no browser, no AI: every page of the list to the end, checked against the site's own count."""
    site = BeisenSite([role(i) for i in range(24)] + [role(99, "1")])
    monkeypatch.setattr(beisen, "PAGE", 10)   # three pages
    monkeypatch.setattr(beisen, "WAITS", (0, 0, 0))
    monkeypatch.setenv("STUDIO_BEISEN_DETECT", "on")
    try:
        ps = Postings(hunt)
        hunt.add_company({"name": "鹏芯微", "sites": [{"url": site.url + "/", "kind": "campus", "system": "北森"}]})
        browser = FakeBrowser({})
        sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["beisen"]   # its own domain: found by its page
        res = sc.scan("鹏芯微")
        assert (res["found"], res["new"], res["needs"], res["short"]) == (25, 25, "", None) and not res["errors"]
        assert browser.opened == []
        assert [b["PageIndex"] for b in site.asked if b["PageSize"] == 10] == [0, 1, 2]
        p = next(x for x in ps.all() if x["title"] == "27届-工程师3(J10603)")
        assert (p["url"], p["location"], p["batch"], p["posted"], p["deadline"]) == \
            (site.url + "/campus/detail?jobAdId=6e5432f5-1cd4-46e8-9ee9-000000000003", "深圳", "campus", "2026-10-27", "")
        assert next(x for x in ps.all() if x["title"].startswith("27届-工程师99"))["batch"] == "social"
        st = ps.scan_state("C1")
        assert st["ok"] and st["how"] == "beisen" and not st.get("short")
        # a role gone from the site is closed: the read was whole
        site.rows = site.rows[1:]
        assert (lambda r: (r["found"], r["closed"]))(sc.scan("鹏芯微")) == (24, 1)
        # the site cuts two answers off midway (北森 does, on big lists): asked again, nothing lost
        site.drops = 2
        res = sc.scan("鹏芯微")
        assert (res["found"], res["closed"]) == (24, 0) and not res["errors"]
    finally:
        site.close()


def test_a_site_that_only_looks_like_beisen_stays_with_its_route(hunt, monkeypatch):
    """job.vanchen.com names a 北森 portal, but its list API doesn't answer (500 on 2026-11-05): not read as 北森,
    its route reads it. A site that doesn't answer at all is asked again at the next scan, never written off."""
    site = BeisenSite([role(1)])
    monkeypatch.setenv("STUDIO_BEISEN_DETECT", "on")
    try:
        assert beisen.detect(site.url + "/") is True
        site.srv.RequestHandlerClass.do_POST = lambda self: self._send(500, b"{}", "application/json")
        assert beisen.detect(site.url + "/") is False
        assert beisen.detect("http://127.0.0.1:9/") is None
        assert beisen.detect("https://anything.zhiye.com/campus") is True
        # a scan doesn't keep "can't tell" as "not 北森"
        hunt.add_company({"name": "万辰集团", "sites": [{"url": "http://127.0.0.1:9/"}]})
        sc = Scanner(hunt, Postings(hunt), NoTool(), FakeBrowser({}), lambda: None)
        sc.plan(hunt.company("C1"))
        assert "http://127.0.0.1:9/" not in sc._not_beisen
        sc.beisen_detect = lambda u: True
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["beisen"]
    finally:
        site.close()


def test_a_beisen_role_kept_under_a_routes_link_keeps_it(hunt):
    """The AI's routes linked 北森 roles every which way (/campus/jobs?jobAdId=<number>, /job/detail?jobAdId=<uuid>).
    A role's link is its key: read by the scan now, each keeps the link it has, never closed and opened anew."""
    ps = Postings(hunt)
    base = "https://fox-ess.zhiye.com"
    hunt.add_company({"name": "麦田能源", "sites": [{"url": base + "/", "kind": "campus", "system": "北森"}]})
    ps.merge("C1", [{"title": "电力电子工程师", "url": base + "/campus/jobs?jobAdId=351580001"},
                    {"title": "嵌入式工程师", "url": base + "/job/detail?jobAdId=6e5432f5-1cd4-46e8-9ee9-000000000002"}], how="route")
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc.beisen_read = lambda url: {"jobs": [beisen.posting(base, role(i)) for i in (1, 2, 3)], "total": 3}
    res = sc.scan("麦田能源")
    assert (res["found"], res["new"], res["closed"]) == (3, 1, 0)
    by = {p["id"]: p["url"] for p in ps.all()}
    assert by == {"P1": base + "/campus/jobs?jobAdId=351580001",
                  "P2": base + "/job/detail?jobAdId=6e5432f5-1cd4-46e8-9ee9-000000000002",
                  "P3": base + "/campus/detail?jobAdId=6e5432f5-1cd4-46e8-9ee9-000000000003"}


def test_a_beisen_portal_with_a_recorded_route_is_read_by_the_scan(hunt):
    """Routes the AI recorded before for 北森 portals (39 companies) give way to the scan's own reader, which
    reads a portal once however many of its addresses the company has (campus, social, its own domain)."""
    ps = Postings(hunt)
    base = "https://novastar.zhiye.com"
    hunt.add_company({"name": "诺瓦星云", "sites": [{"url": base + "/campus/jobs", "kind": "campus"},
                                                   {"url": base + "/social/jobs", "kind": "social"}]})
    browser = FakeBrowser({})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    ps.set_scan_state("C1", route={"pages": [{"url": base + "/campus/jobs", "script": "async (h) => ({postings: []})"}],
                                   "sites": [base + "/campus/jobs", base + "/social/jobs"]})
    read = []
    sc.beisen_read = lambda url: read.append(url) or {"jobs": [beisen.posting(base, role(i)) for i in range(5)], "total": 5,
                                                      "portal": "p1"}
    res = sc.scan("诺瓦星云")
    assert res["found"] == 5 and read == [base + "/campus/jobs"] and browser.opened == [] and res["needs"] == ""
    # the AI may not record a route for one: the scan reads it
    r, errs = sc.save_route("诺瓦星云", {"pages": [{"url": base + "/campus/jobs", "script": "async (h) => ({postings: []})"}]})
    assert not r and "beisen reads this list with no route" in errs[0]


def test_a_beisen_portal_that_reads_short_of_its_count_is_not_done(hunt):
    """The site says 24 and 20 came back: the scan says so, and closes nothing."""
    ps = Postings(hunt)
    base = "https://career.pxwsemi.com"
    hunt.add_company({"name": "鹏芯微", "sites": [{"url": base + "/", "system": "北森"}]})
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc._providers[base + "/"] = "beisen"
    rows = [beisen.posting(base, role(i)) for i in range(24)]
    sc.beisen_read = lambda url: {"jobs": rows, "total": 24}
    assert sc.scan("C1")["short"] is None
    sc.beisen_read = lambda url: {"jobs": rows[:20], "total": 24}
    res = sc.scan("C1")
    assert res["short"] == [20, 24] and res["closed"] == 0 and sum(1 for p in ps.all() if not p["closed"]) == 24


def test_a_route_is_tried_before_it_is_kept(hunt):
    """A route is run once before it is kept: one that reads no roles, or fewer than its site says, goes back to
    the AI with why, and the company keeps the route it had."""
    ps = Postings(hunt)
    url = "https://careers.xinghe.example/campus/jobs"
    hunt.add_company({"name": "星河科技", "site": url})
    rows = [{"title": f"岗位 {i}", "url": f"{url}/{i}"} for i in range(24)]
    browser = FakeBrowser({url: {"total": 24, "postings": rows[:20]}})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.empty_wait = 0
    sc._providers[url] = None
    script = {"pages": [{"url": url, "script": "async (h) => ({total, postings})"}]}
    r, errs = sc.save_route("星河科技", script)
    assert not r and "read 20 different roles and the site says it lists 24. Nothing was kept" in errs[0]
    assert ps.route("C1") is None and ps.all() == []
    browser.pages[url] = {"total": 24, "postings": []}
    r, errs = sc.save_route("星河科技", script)
    assert not r and "read no roles" in errs[0] and ps.route("C1") is None
    # one that reads them all is kept, and the scan right after uses what it just read: the site isn't read twice
    browser.pages[url] = {"total": 24, "postings": rows}
    browser.opened = []
    r, errs = sc.save_route("星河科技", script)
    assert r and not errs and browser.opened == [url]
    res = sc.scan("星河科技", just_tried=True)
    assert res["found"] == 24 and browser.opened == [url]
    # a later, worse version never replaces it
    browser.pages[url] = {"total": 24, "postings": rows[:3]}
    assert sc.save_route("星河科技", {"pages": [{"url": url, "script": "async (h) => 'v2'"}]})[1]
    assert ps.route("C1")["pages"] == script["pages"]


def test_a_list_that_shows_late_is_read_again_before_the_route_counts_as_broken(hunt):
    """鹏芯微's page draws its list a while after it loads: one scan read 20 roles, the next none, and the window
    said the site had probably changed. A page that reads no roles is read once more, a while later; a route that
    still reads none says what happened, never that the site changed."""
    ps = Postings(hunt)
    url = "https://careers.xinghe.example/campus/jobs"
    hunt.add_company({"name": "星河科技", "site": url})
    rows = [{"title": "服务端研发工程师", "url": url + "/1"}]
    answers = iter([{"postings": rows}, {"postings": []}, {"postings": rows}, {"postings": []}, {"postings": []}])
    browser = FakeBrowser({})
    browser.read_page = lambda u, s, timeout=60.0: next(answers)
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.empty_wait = 0
    sc._providers[url] = None
    assert not sc.save_route("星河科技", {"pages": [{"url": url, "script": "async (h) => ({postings})"}]})[1]
    res = sc.scan("星河科技")                      # none at first, then the list was there
    assert res["found"] == 1 and not res["errors"] and not ps.all()[0]["closed"]
    res = sc.scan("星河科技")                      # none twice: the route's trouble, said as it is
    # in the interface's language (the suite runs in Chinese, a test run alone may not)
    said = t("the route's pages opened but its scripts read no roles on {pages}, twice, a while apart: the list may "
             "not show yet when they read it, or they read the wrong part of the page", pages=url)
    assert res["route_broken"] and res["error"] == said
    assert not ps.all()[0]["closed"]
