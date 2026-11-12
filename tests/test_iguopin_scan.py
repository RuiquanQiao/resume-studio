"""国聘 company pages read by the scan itself (2026-11-12).

Two companies in the tracker had routes the AI wrote for their 国聘 pages, both calling one API. The server below
answers as the real one did when asked from Python: a page's settings name its company and its menu (the projects
each job list shows); the job list pages up to 100 at a time and says its total. Companies and ids are made up.
Each test failed on the code before 国聘 was read by the scan."""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jobhunt import iguopin
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401

PAGE = "https://acme2027.iguopin.com/job"


def role(i: int, unit: str = "示例银行", name: str = "", nature: str = "校招") -> dict:
    return {"job_id": f"2242880{i:011d}", "job_name": name or f"信息科技类{i}", "company_name": unit,
            "category_cn": "其他职位" if i % 2 else "研发类", "nature_cn": nature, "recruitment_type_cn": "校园招聘",
            "district_list": [{"area_cn": "北京"}, {"area_cn": "上海"}, {"area_cn": "北京"}],
            "start_time": "2026-10-29 17:31:18", "end_time": "2026-11-15 23:59:59"}


class GuopinApi:
    """gp-api.iguopin.com over HTTP: `pages` {subdomain: (company id, menu)}, `rows` {project id: roles}."""

    def __init__(self, pages: dict, rows: dict[str, list[dict]]):
        self.pages, self.rows, self.asked, self.broken = pages, rows, [], set()
        api = self

        class H(BaseHTTPRequestHandler):
            def _json(self, d, code=200):
                body = json.dumps(d, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                sub = dict(urllib.parse.parse_qsl(u.query)).get("domain", "")
                if u.path != iguopin.INFO or sub not in api.pages:
                    return self._json({"code": 404, "msg": "not found", "data": None})
                cid, nav = api.pages[sub]
                self._json({"code": 200, "data": {"company_id": cid, "project_id": "",
                                                  "content": json.dumps({"params": {"nav": nav}}, ensure_ascii=False)}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if urllib.parse.urlsplit(self.path).path != iguopin.API:
                    return self._json({"code": 404}, 404)
                n, size = body["page"], min(100, body["page_size"])   # as the real one: 100 at most whatever is asked
                api.asked.append((tuple(body.get("project_id", ())), n, body.get("company_id_with_sub")))
                if n in api.broken:
                    return self._json({"code": 500, "msg": "busy"}, 502)
                rows = [r for p in body["project_id"] for r in api.rows.get(p, [])] if "project_id" in body \
                    else [r for rs in api.rows.values() for r in rs]   # no project named: all of the company's open roles
                self._json({"code": 200, "data": {"list": rows[(n - 1) * size:n * size], "total": len(rows),
                                                  "page": n, "page_size": size}})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


def nav(job: str) -> list[dict]:
    return [{"route": "/", "name": "首页", "props": {}}, {"route": "/notice", "name": "招聘公告", "props": {"contentType": "editor"}},
            {"route": "/job", "name": "校园招聘", "props": {"projectId": job, "cardType": "default"}}]


def test_a_company_page_on_guopin_is_read_whole_by_the_scan_itself(hunt, monkeypatch):
    """No route, no browser, no AI: the page's job list is read to its last page of 100, checked against its total.
    A bank's head office and branches list the same 信息科技类, so each title names its unit."""
    rows = [role(i, "示例银行" if i < 100 else "示例银行广东省分行") for i in range(130)]
    api = GuopinApi({"acme2027": ("900001", nav("p1"))}, {"p1": rows})
    monkeypatch.setattr(iguopin, "BASE", api.base)
    monkeypatch.setattr(iguopin, "WAITS", (0, 0, 0))
    try:
        ps = Postings(hunt)
        hunt.add_company({"name": "示例银行", "sites": [{"url": PAGE, "kind": "campus"}]})
        browser = FakeBrowser({})
        sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["iguopin"]
        assert sc.site_status(hunt.company("C1"))[PAGE] in ("api", "")   # read built in, never "the AI has to learn it"
        res = sc.scan("示例银行")
        assert (res["found"], res["new"], res["needs"], res["short"]) == (130, 130, "", None) and not res["errors"]
        assert browser.opened == []
        assert [(n, c) for _, n, c in api.asked] == [(1, "900001"), (2, "900001")]   # its company and branches
        p = next(x for x in ps.all() if x["url"].endswith(role(2)["job_id"]))
        assert (p["title"], p["location"], p["category"], p["batch"], p["posted"], p["deadline"]) == \
            ("示例银行 信息科技类2", "北京、上海", "研发类", "campus", "2026-10-29", "2026-11-15")
        assert p["url"] == "https://www.iguopin.com/job/detail?id=" + role(2)["job_id"]
        assert next(x for x in ps.all() if x["url"].endswith(role(1)["job_id"]))["category"] == ""   # "其他职位" says nothing
        assert next(x for x in ps.all() if x["url"].endswith(role(120)["job_id"]))["title"].startswith("示例银行广东省分行 ")
        assert ps.scan_state("C1")["how"] == "iguopin"
        # a role gone from the list is closed: the read was whole
        api.rows["p1"] = rows[1:]
        assert (lambda r: (r["found"], r["closed"]))(sc.scan("示例银行")) == (129, 1)
        # a later page that doesn't come: what came is kept, nothing closed
        api.broken.add(2)
        r = sc.scan("示例银行")
        assert (r["found"], r["closed"]) == (100, 0) and r["errors"]
    finally:
        api.close()


def test_one_units_list_keeps_plain_titles_and_kinds_are_told_apart():
    rows = [role(1, "示例控股", "海外安全护卫员"), role(2, "示例控股", "2027届管培生实习", "校招"), role(3, "示例控股", "财务主管", "社招")]
    api = GuopinApi({"acmeholding": ("900002", nav("p2,p3"))}, {"p2": rows, "p3": [rows[0]]})
    iguopin_base, waits = iguopin.BASE, iguopin.WAITS
    iguopin.BASE, iguopin.WAITS = api.base, (0, 0, 0)
    try:
        r = iguopin.read("https://acmeholding.iguopin.com/")          # its home page: every job list it has
        assert r["total"] == 4 and len(r["jobs"]) == 3                 # a role in two projects once
        assert [(j["title"], j["batch"]) for j in r["jobs"]] == [("海外安全护卫员", "campus"), ("2027届管培生实习", "intern"),
                                                                  ("财务主管", "social")]
        assert api.asked[0][0] == ("p2", "p3")
    finally:
        iguopin.BASE, iguopin.WAITS = iguopin_base, waits
        api.close()


def test_a_route_the_ai_kept_steps_aside_and_its_roles_keep_their_links(hunt, monkeypatch):
    """The AI's route linked roles the same way the scan does: read by the scan now, none is closed and opened
    again, and the route is never run (a page a built-in scan reads is never read twice)."""
    rows = [role(i, "示例银行") for i in range(5)]
    api = GuopinApi({"acme2027": ("900001", nav("p1"))}, {"p1": rows})
    monkeypatch.setattr(iguopin, "BASE", api.base)
    monkeypatch.setattr(iguopin, "WAITS", (0, 0, 0))
    try:
        ps = Postings(hunt)
        hunt.add_company({"name": "示例银行", "sites": [{"url": "https://acme2027.iguopin.com", "kind": "campus"}]})
        old = [{"title": r["job_name"], "url": "https://www.iguopin.com/job/detail?id=" + r["job_id"]} for r in rows]
        ps.merge("C1", old, how="route", complete=True)
        ps.set_scan_state("C1", route={"pages": [{"url": PAGE, "script": "async (h) => ({postings: []})"}]})
        browser = FakeBrowser({PAGE: {"postings": []}})
        sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
        res = sc.scan("示例银行")
        assert (res["found"], res["new"], res["closed"]) == (5, 0, 0) and not res["errors"]
        assert browser.opened == []
        assert len(api.asked) == 1                                     # the site and the route's page: one list, read once
        assert sc.shared_systems() == []                               # its API is a built-in scan's now
    finally:
        api.close()


def test_a_menu_left_on_last_seasons_project_still_reads_this_seasons_roles():
    """中储粮's page menu named a project with no roles while 924 were open under a newer one: read 0, 2026-11-13."""
    api = GuopinApi({"acme2027": ("900001", nav("old"))}, {"old": [], "new": [role(i) for i in range(3)]})
    base, waits = iguopin.BASE, iguopin.WAITS
    iguopin.BASE, iguopin.WAITS = api.base, (0, 0, 0)
    try:
        r = iguopin.read(PAGE)
        assert (r["total"], len(r["jobs"])) == (3, 3)
        assert [a[0] for a in api.asked] == [("old",), ()]
    finally:
        iguopin.BASE, iguopin.WAITS = base, waits
        api.close()


def test_only_a_company_page_is_one():
    assert iguopin.detect("https://starbank2027.iguopin.com/job") and iguopin.detect("https://ACME.iguopin.com")
    for url in ("https://www.iguopin.com/job/detail?id=1", "https://gp-api.iguopin.com/api/jobs/v1/project-job",
                "https://iguopin.com/", "https://acme.iguopin.com.example.org/", "https://careers.example.com/"):
        assert not iguopin.detect(url), url
    assert iguopin.key("https://acme.iguopin.com/") == iguopin.key("https://acme.iguopin.com/job")
