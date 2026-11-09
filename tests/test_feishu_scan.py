"""Feishu tenants on a company's own domain, read by the scan itself (小米集团, 2026-11-09).

小米集团 runs Feishu Jobs at xiaomi.jobs.f.mioffice.cn: career-ops' provider knows only *.jobs.feishu.cn, so the
scan said no scan could read it, and the AI sent to learn it replayed a GET of the page's request, got the page's
HTML back and stopped at the 10 roles of the first page of 783. The site's API answers a POST with every role.
Each test here failed on the code before the fix."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jobhunt import feishu
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401


class FeishuSite:
    """A Feishu tenant over HTTP on a domain of its own: its pages are the app's HTML (for GET, as the AI found),
    its job API pages through each portal's rows by the `website-path` header. `drops`: answers to cut off."""

    def __init__(self, portals: dict[str, list[dict]]):
        self.portals, self.asked, self.drops = portals, [], 0
        site = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body, kind):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(200, b"<!DOCTYPE html><html><body><div id='mainBox'></div></body></html>", "text/html")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if site.drops:
                    site.drops -= 1
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    self.wfile.write(b"{")
                    return
                if self.path != feishu.API:
                    return self._send(404, b"<html>not here</html>", "text/html")
                portal = self.headers.get("website-path") or ""
                site.asked.append((portal, body["offset"], body["limit"]))
                rows = site.portals.get(portal, [r for rs in site.portals.values() for r in rs])
                page = {"code": 0, "data": {"count": len(rows), "job_post_list": rows[body["offset"]:body["offset"] + body["limit"]]}}
                self._send(200, json.dumps(page, ensure_ascii=False).encode("utf-8"), "application/json")

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()


def role(i: int, kind: str = "校招") -> dict:
    return {"id": str(7680536313443600000 + i), "title": f"AI软件工程师{i}", "city_list": [{"name": "北京"}, {"name": "上海"}],
            "job_category": {"name": "软件开发"}, "recruit_type": {"name": "正式", "parent": {"name": kind}},
            "publish_time": 1762646400000}


def test_a_feishu_site_on_its_own_domain_is_read_whole_by_the_scan_itself(hunt, monkeypatch):
    """No route, no browser, no AI: each portal named by its address, every page to the end."""
    site = FeishuSite({"campus": [role(i) for i in range(25)], "internship": [role(100 + i, "实习") for i in range(7)]})
    monkeypatch.setattr(feishu, "PAGE", 10)   # three pages of campus
    monkeypatch.setattr(feishu, "WAITS", (0, 0, 0))
    monkeypatch.setenv("STUDIO_FEISHU_DETECT", "on")
    try:
        ps = Postings(hunt)
        hunt.add_company({"name": "小米集团", "sites": [{"url": site.url + "/campus/position/list", "kind": "campus", "system": "飞书"},
                                                    {"url": site.url + "/internship/position/list", "kind": "intern"}]})
        browser = FakeBrowser({})
        sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["feishu", "feishu"]
        res = sc.scan("小米集团")
        assert (res["found"], res["new"], res["needs"], res["short"]) == (32, 32, "", None) and not res["errors"], res
        assert browser.opened == []
        assert sorted(o for p, o, n in site.asked if p == "campus" and n == 10) == [0, 10, 20]
        p = next(x for x in ps.all() if x["title"] == "AI软件工程师3")
        assert (p["url"], p["location"], p["batch"], p["category"], p["posted"]) == \
            (site.url + "/campus/position/7680536313443600003/detail", "北京 / 上海", "campus", "软件开发", "2025-11-09")
        assert next(x for x in ps.all() if x["title"] == "AI软件工程师100")["batch"] == "intern"
        assert ps.scan_state("C1")["ok"] and ps.scan_state("C1")["how"] == "feishu"
        # the read was whole: a role gone from the site is closed
        site.portals["campus"] = site.portals["campus"][1:]
        assert (lambda r: (r["found"], r["closed"]))(sc.scan("小米集团")) == (31, 1)
        # answers cut off midway are asked again, nothing lost
        site.drops = 2
        res = sc.scan("小米集团")
        assert (res["found"], res["closed"]) == (31, 0) and not res["errors"], res
    finally:
        site.close()


def test_a_page_that_never_comes_loses_only_itself(hunt, monkeypatch):
    """小米's intern portal (2026-11-10): page 2 of 6 was cut off four tries running, and the portal's 590 roles were
    all thrown away for it. Now the other pages are kept, the scan says what didn't come, and nothing is closed on a
    read that wasn't whole."""
    site = FeishuSite({"internship": [role(i, "实习") for i in range(30)]})
    monkeypatch.setattr(feishu, "PAGE", 10)
    monkeypatch.setattr(feishu, "WAITS", (0,))
    monkeypatch.setattr(feishu, "AT_ONCE", 1)   # pages in order, so the cut-off one is known
    monkeypatch.setenv("STUDIO_FEISHU_DETECT", "on")
    try:
        ps = Postings(hunt)
        hunt.add_company({"name": "小米集团", "sites": [{"url": site.url + "/internship/position/list"}]})
        sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
        assert sc.scan("小米集团")["found"] == 30
        # every try of page 2 is cut off (the detect check, page 1 and page 3 answer)
        real = site.srv.RequestHandlerClass.do_POST
        def flaky(self):
            n = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if n["offset"] == 10:
                self.send_response(200); self.send_header("Content-Length", "100000"); self.end_headers(); self.wfile.write(b"{")
                return
            self.rfile = __import__("io").BytesIO(json.dumps(n).encode())
            self.headers.replace_header("Content-Length", str(len(json.dumps(n))))
            real(self)
        site.srv.RequestHandlerClass.do_POST = flaky
        r = feishu.read(site.url + "/internship/position/list", waits=(0,))
        assert len(r["jobs"]) == 20 and r["total"] == 30 and "page 2" in r["failed"], r.get("failed")
        res = sc.scan("小米集团")
        assert (res["found"], res["closed"]) == (20, 0) and res["errors"] and "page 2" in res["errors"][0], res
        assert res["short"] == [20, 30]
        assert sum(1 for p in ps.all() if not p["closed"]) == 30
    finally:
        site.close()


def test_whether_a_site_is_on_feishu_is_asked_of_its_api(hunt, monkeypatch):
    """A site whose API answers with a job list is Feishu; one answering a page or an error isn't; one that doesn't
    answer is asked again at the next scan, never written off."""
    site = FeishuSite({"campus": [role(1)]})
    monkeypatch.setenv("STUDIO_FEISHU_DETECT", "on")
    try:
        assert feishu.detect(site.url + "/campus/position/list") is True
        site.srv.RequestHandlerClass.do_POST = lambda self: self._send(200, b"<!DOCTYPE html><html></html>", "text/html")
        assert feishu.detect(site.url + "/campus/position/list") is False
        site.srv.RequestHandlerClass.do_POST = lambda self: self._send(405, b"{}", "application/json")
        assert feishu.detect(site.url + "/") is False
        assert feishu.detect("http://127.0.0.1:9/campus") is None
        hunt.add_company({"name": "小米集团", "sites": [{"url": "http://127.0.0.1:9/campus"}]})
        sc = Scanner(hunt, Postings(hunt), NoTool(), FakeBrowser({}), lambda: None)
        sc.plan(hunt.company("C1"))
        assert "http://127.0.0.1:9/campus" not in sc._not_feishu
        sc.feishu_detect = lambda u: True
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["feishu"]
    finally:
        site.close()


def test_one_portal_found_on_feishu_makes_the_sites_other_portals_feishu(hunt):
    """小米's first scan (2026-11-10): its intern and new-retail portals answered the check, its campus and social
    ones timed out, so the scan read 688 roles and said the rest needed the AI. One API serves every portal of a
    site: the others are read too, with no check of their own."""
    base = "https://xiaomi.jobs.f.mioffice.cn"
    urls = [f"{base}/{p}/position/list" for p in ("index", "campus", "internship", "newretailing")]
    hunt.add_company({"name": "小米集团", "sites": [{"url": u} for u in urls]})
    sc = Scanner(hunt, Postings(hunt), NoTool(), FakeBrowser({}), lambda: None)
    asked = []
    sc.feishu_detect = lambda u: asked.append(u) or (True if "internship" in u else None)   # the rest time out
    assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["feishu"] * 4
    # asked until one answered; and a site elsewhere is still asked about on its own
    assert asked == urls[:3]
    assert sc.unrouted(sc.plan(hunt.company("C1"))) == []


def test_roles_the_ai_read_by_hand_are_kept_when_the_scan_reads_the_site(hunt):
    """小米集团's 10 roles the AI copied off the page have the links the API gives: read by the scan, they stay the
    same roles, never closed and opened anew; and no route may be recorded for a list the scan reads."""
    ps = Postings(hunt)
    base = "https://xiaomi.jobs.f.mioffice.cn"
    url = base + "/campus/position/list"
    hunt.add_company({"name": "小米集团", "sites": [{"url": url, "kind": "campus", "system": "飞书"}]})
    hand = [feishu.posting(base, "campus", role(i)) for i in range(10)]
    ps.merge("C1", hand, how="claude")
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc._providers[url] = None
    sc.feishu_detect = lambda u: True
    sc.feishu_read = lambda u: {"jobs": [feishu.posting(base, "campus", role(i)) for i in range(30)], "total": 30}
    res = sc.scan("小米集团")
    assert (res["found"], res["new"], res["closed"]) == (30, 20, 0), res
    assert len(ps.all()) == 30
    r, errs = sc.save_route("小米集团", {"pages": [{"url": url, "script": "async (h) => ({postings: []})"}]})
    assert not r and "feishu reads this list with no route" in errs[0], errs
