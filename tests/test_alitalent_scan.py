"""阿里's talent sites read by the scan itself (2026-11-11).

Five companies of one group each had routes the AI wrote for their talent sites, 12 route pages calling one API
(found by jobhunt/systems.py), each waiting in the browser for the page's own request to carry a token. The site
below answers as the real ones did when asked from Python: a double-submit token, the language's own API under
/zh (the plain one answered 8 of 301 roles), the group's campus site split by company code, a role listed in two
batches. Companies and addresses are made up. Each test failed on the code before these sites were read by the scan."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jobhunt import alitalent
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401

GRAD, INTERN, INTERN2 = 100000760001, 100000560002, 100000560001


def role(i: int, circle: str = "60006", batch: int = 0) -> dict:
    return {"id": 199900000000 + i, "name": f"算法工程师{i}", "workLocations": ["杭州", "北京"], "categoryName": "技术类",
            "categories": ["技术类"], "publishTime": 1794000000000, "circleCodeList": [circle], "batchId": batch}


class TalentSite:
    """One of the group's talent sites over HTTP. `social`: its own social list (under /zh only, as one real company's;
    the plain API answers `plain_only` of them). `campus`: the group's campus roles, by batch."""

    def __init__(self, social: list[dict] | None = None, campus: dict[int, list[dict]] | None = None,
                 plain_only: int | None = None, count_more: int = 0):
        self.social, self.campus, self.plain_only, self.count_more = social or [], campus or {}, plain_only, count_more
        self.asked: list[tuple[str, dict]] = []
        site = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                cookie = dict(x.strip().split("=", 1) for x in (self.headers.get("Cookie") or "").split(";") if "=" in x)
                if not cookie.get("XSRF-TOKEN") or cookie["XSRF-TOKEN"] != self.headers.get("x-xsrf-token"):
                    return self._send(403, b"{}")
                site.asked.append((self.path, body))
                ok = lambda c: self._send(200, json.dumps({"success": True, "content": c}, ensure_ascii=False).encode())
                if self.path == "/en/position/search" or self.path.endswith("/searchCondition/listBatch") and self.path.startswith("/en"):
                    return self._send(404, b"")
                if self.path.endswith(alitalent.BATCHES):
                    names = {GRAD: "graduate", INTERN: "internship", INTERN2: "internship"}
                    out: dict = {"sequence": ["graduate", "internship", "topTalentPlan"], "graduate": [], "internship": []}
                    for b in site.campus:
                        out[names[b]].append({"id": b, "type": names[b]})
                    out["topTalentPlan"] = [{"id": GRAD, "type": "aliStar"}] if GRAD in site.campus else []
                    return ok(out)
                if not self.path.endswith(alitalent.API):
                    return self._send(404, b"")
                if body["channel"] == "campus_group_official_site":
                    if not body.get("batchId"):
                        return ok({"datas": None, "totalCount": 0})
                    rows = site.campus.get(int(body["batchId"]), [])
                    if body.get("circleCodes"):
                        rows = [r for r in rows if set(r["circleCodeList"]) & set(body["circleCodes"])]
                else:
                    rows = site.social if self.path.startswith("/zh/") or site.plain_only is None else site.social[:site.plain_only]
                n, size = body["pageIndex"], body["pageSize"]
                ok({"datas": rows[(n - 1) * size:n * size], "totalCount": len(rows) + site.count_more})

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()


def _scanner(hunt):
    return Scanner(hunt, Postings(hunt), NoTool(), FakeBrowser({}), lambda: None)


def test_a_social_list_is_read_whole_under_its_language(hunt, monkeypatch):
    """No route, no browser, no AI, no page opened for a token: every page of the list, under /zh, where the
    plain API answers only some of it."""
    site = TalentSite(social=[role(i) for i in range(120)], plain_only=8)
    monkeypatch.setenv("STUDIO_ALITALENT_DETECT", "on")
    monkeypatch.setattr(alitalent, "WAITS", (0, 0, 0))
    try:
        url = site.url + "/zh/off-campus/position-list"
        hunt.add_company({"name": "远洋数字", "sites": [{"url": url, "kind": "social"}]})
        sc = _scanner(hunt)
        assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["alitalent"]
        res = sc.scan("远洋数字")
        assert (res["found"], res["new"], res["needs"], res["short"]) == (120, 120, "", None) and not res["errors"]
        assert sc.browser.opened == []
        assert sorted(b["pageIndex"] for p, b in site.asked if p == "/zh/position/search" and b["pageSize"] == 50) == [1, 2, 3]
        p = next(x for x in sc.postings.all() if x["title"] == "算法工程师3")
        assert (p["url"], p["location"], p["category"], p["batch"], p["posted"]) == \
            (f"{site.url}/zh/off-campus/position-detail?positionId=199900000003", "杭州 / 北京", "技术类", "social", "2026-11-07")
        assert sc.postings.scan_state("C1")["how"] == "alitalent"
        site.social = site.social[1:]   # a role gone: closed, the read was whole
        assert (lambda r: (r["found"], r["closed"]))(sc.scan("远洋数字")) == (119, 1)
    finally:
        site.close()


def test_a_company_reads_its_own_part_of_the_groups_campus_site_every_batch_once(hunt, monkeypatch):
    """The group's campus site lists every company's roles: a company reads the ones its code names, in every batch
    (its address names only the batch its page shows first), a role in two batches once, and the old route's links
    are kept. The site with no company named is the whole group's, never one company's."""
    shared = [role(1, batch=GRAD), role(2, batch=GRAD), role(3, "60003", GRAD)]
    site = TalentSite(campus={GRAD: shared, INTERN: [role(10, batch=INTERN), role(2, batch=INTERN)], INTERN2: [role(11, batch=INTERN2)]})
    monkeypatch.setenv("STUDIO_ALITALENT_DETECT", "on")
    monkeypatch.setattr(alitalent, "WAITS", (0, 0, 0))
    monkeypatch.setattr(alitalent, "SHARED", {"127.0.0.1"})
    try:
        url = f"{site.url}/campus/position?circleCode=60006&batchId={GRAD}"
        assert alitalent.detect(f"{site.url}/campus/position?batchId={GRAD}") is False   # the whole group's
        assert site.asked == []
        hunt.add_company({"name": "远洋物流", "sites": [{"url": url, "kind": "campus"},
                                                    {"url": f"{site.url}/campus/position?circleCode=60006&batchId={INTERN}"}]})
        sc = _scanner(hunt)
        old = f"{site.url}/campus/position/199900000001?positionId=199900000001&batchId={GRAD}"
        sc.postings.merge("C1", [{"title": "算法工程师1", "url": old}], how="route", complete=True)
        res = sc.scan("远洋物流")
        assert (res["found"], res["closed"], res["short"]) == (4, 0, None) and not res["errors"]
        got = {p["title"]: p for p in sc.postings.all() if not p["closed"]}
        assert sorted(got) == ["算法工程师1", "算法工程师10", "算法工程师11", "算法工程师2"]
        assert got["算法工程师1"]["url"] == old                                  # the link it had
        assert got["算法工程师10"]["batch"] == "intern" and got["算法工程师2"]["batch"] == "campus"
        assert got["算法工程师10"]["url"] == f"{site.url}/campus/position/199900000010"
        # the two addresses are one list, read once
        assert len([1 for p, b in site.asked if p.endswith(alitalent.API) and b.get("batchId") == GRAD]) == 1
    finally:
        site.close()


def test_a_count_the_site_gives_no_rows_for_is_not_left_unread(hunt, monkeypatch):
    """One company's /zh list said 8 and gave 7 on its one page, nothing after: the read is whole, said as 7, so
    the scan isn't 'not done' for ever and a role gone from it is closed."""
    site = TalentSite(social=[role(i) for i in range(7)], count_more=1)
    monkeypatch.setattr(alitalent, "WAITS", (0, 0, 0))
    try:
        r = alitalent.read(site.url + "/off-campus/position-list")
        assert (len(r["jobs"]), r["total"]) == (7, 7) and "failed" not in r
        site.social = [role(i) for i in range(60)]   # a full page and a short one: still whole
        r = alitalent.read(site.url + "/off-campus/position-list")
        assert (len(r["jobs"]), r["total"]) == (60, 60)
    finally:
        site.close()


def test_what_is_one_list_and_which_api(monkeypatch):
    k = alitalent.key
    assert k("https://x.example/campus/position?circleCode=60006&batchId=1") == k("https://x.example/campus/position?circleCode=60006&batchId=2")
    assert k("https://x.example/campus/position?circleCode=60006") != k("https://x.example/campus/position?circleCode=60003")
    assert alitalent.scope('https://x.example/campus/position?filterParams={"customDept":["YQNHYU","60005"]}') == \
        {"circleCodes": ["60005"], "customDeptCode": "YQNHYU"}
    assert alitalent.channel("https://x.example/en/off-campus/position-list?lang=en") == "group_overseas_official_site"
    assert alitalent.channel("https://x.example/off-campus/position-list") == "group_official_site"
    assert alitalent.channel("https://x.example/campus/position-list") == "campus_group_official_site"
    assert alitalent.detect("https://campus-talent.alibaba.com/campus/position") is False


def test_a_companys_campus_page_with_no_batch_of_its_own_closes_nothing(hunt, monkeypatch):
    """A company's talent.<x>/campus/home has no batch of its own: its campus roles are on the group's campus site,
    which its route read. Taken as read by the scan, it read none, and the scan closed the 82 campus roles the route
    had read (on a copy of the tracker, 2026-11-11). It stays with its route; the roles stay open."""
    own = TalentSite(social=[role(i) for i in range(3)])                          # no campus batches
    shared = TalentSite(campus={GRAD: [role(20, "80265", GRAD), role(21, "80265", GRAD)]})
    monkeypatch.setenv("STUDIO_ALITALENT_DETECT", "on")
    monkeypatch.setattr(alitalent, "WAITS", (0, 0, 0))
    monkeypatch.setattr(alitalent, "SHARED", {"127.0.0.1"})
    try:
        home = own.url.replace("127.0.0.1", "localhost") + "/campus/home"
        assert alitalent.detect(home) is False
        hunt.add_company({"name": "兰舟游戏", "sites": [{"url": home, "kind": "campus"}]})
        page = f"{shared.url}/campus/position?circleCode=80265&batchId={GRAD}"
        sc = _scanner(hunt)
        sc.postings.set_scan_state("C1", route={"pages": [{"url": page, "script": "async (h) => ({postings: []})"}], "sites": [home]})
        sc.postings.merge("C1", [{"title": f"算法工程师{i}", "url": f"{shared.url}/campus/position/{199900000000 + i}"}
                                 for i in (20, 21)], how="route", complete=True)
        res = sc.scan("兰舟游戏")
        assert res.get("closed", 0) == 0 and len([p for p in sc.postings.all() if not p["closed"]]) == 2
    finally:
        own.close()
        shared.close()
