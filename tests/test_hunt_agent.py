"""The tracker finds things itself (docs/JOB_HUNT.md, 2026-10-18): companies fill themselves, a posting's link
is read, and the companies' sites are scanned for open postings."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from jobhunt import api as hunt_api
from jobhunt import logos
from jobhunt.postings import Postings, clean, fingerprint
from jobhunt.scan import Scanner, validate_route
from jobhunt.store import LOGO_TYPES, Hunt
from test_hunt import cli, live  # noqa: F401

def png(side: int) -> bytes:
    """A real png, side × side, solid blue."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + b"\x20\x40\xc0" * side for _ in range(side))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


PNG = png(64)
TINY = png(16)   # a 16×16 favicon: a blur in the tables


@pytest.fixture
def hunt(tmp_path: Path) -> Hunt:
    return Hunt(tmp_path / ".studio")


# ---------------------------------------------------------------- a company fills itself
def test_claude_fills_every_field_and_leaves_the_users_own(hunt):
    c = hunt.add_company({"name": "星河科技"})
    hunt.update_company(c["id"], {"industry": "电商（我的说法）"})          # the user edits one field in the window
    assert hunt.company("C1")["kept"] == ["industry"]
    got, kept = hunt.fill("星河科技", {
        "name": "Xinghe Technology Ltd.", "aliases": ["Xinghe", "Nebula"], "industry": "E-commerce", "strength": "市值约 1,600 亿美元 (NASDAQ: XHT, 2026-10-17)",
        "location": "上海", "size": "约 2.3 万人",
        "sites": [{"url": "careers.xinghe.example/campus", "kind": "campus", "system": "own site"},
                  {"url": "https://careers.xinghe.example/intern", "kind": "intern"}],
        "sources": {"strength": "https://example.com/quote", "industry": "https://example.com/x", "size": "not a link"},
        "about": {"summary": "电商平台", "points": [{"topic": "业务", "text": "星河科技与 Nebula", "sources": ["https://example.com/r"]}]}})
    assert kept == ["industry"] and got["industry"] == "电商（我的说法）"          # the user's stays
    assert got["name"] == "星河科技" and got["aliases"] == ["Xinghe Technology Ltd.", "Xinghe", "Nebula"]   # the official name is an alias
    # the headquarters, sent as `location` (their name before 2026-10-27), are a city from the list of places
    assert got["strength"].startswith("市值约") and got["hq"] == {"area": "Shanghai", "id": "g1796236"} and "location" not in got
    assert got["site"] == "https://careers.xinghe.example/campus"                # the campus site leads
    assert [s["kind"] for s in got["sites"]] == ["campus", "intern"]
    assert got["sources"] == {"strength": "https://example.com/quote"}            # sources of kept fields and non-links dropped
    assert got["about"]["points"][0]["sources"] == ["https://example.com/r"]
    assert got["kept"] == ["industry"]                                            # Claude's fill doesn't make fields the user's
    # the user hands the field back to Claude
    hunt.update_company("C1", {"kept": []})
    assert hunt.fill("C1", {"industry": "E-commerce"})[0]["industry"] == "E-commerce"


def test_a_logo_is_kept_in_the_data_folder(hunt):
    hunt.add_company({"name": "Atlassian"})
    c = hunt.save_logo("Atlassian", PNG, "image/png")
    assert c["logo"] == "C1.png" and hunt.logo_path("C1").read_bytes() == PNG
    hunt.save_logo("C1", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", source="window")
    assert hunt.logo_path("C1").name == "C1.svg" and not (hunt.dir / "logos" / "C1.png").exists()
    assert hunt.save_logo("C1", PNG, "image/png")["logo"] == "C1.svg"            # the user's logo stays
    with pytest.raises(ValueError):
        hunt.save_logo("C1", b"<html>", "text/html", source="window")


def test_a_logo_is_found_on_the_companys_own_site():
    page = ('<html><head><link rel="icon" href="/favicon-16.png" sizes="16x16">'
            '<link rel="mask-icon" href="/mask.svg"><link rel="apple-touch-icon" href="https://cdn.example.com/touch.png">'
            '<link rel="shortcut icon" href="icon.svg"></head></html>')
    assert logos.candidates("https://careers.example.com/campus/", page) == [
        "https://cdn.example.com/touch.png", "https://careers.example.com/campus/icon.svg",
        "https://careers.example.com/favicon-16.png", "https://careers.example.com/favicon.ico"]
    assert logos.candidates("https://x.example.com/a", "<html>") == ["https://x.example.com/favicon.ico"]

    asked = []
    pics = {"https://careers.example.com/favicon.ico": (PNG, "image/png")}

    def download(u):
        asked.append(u)
        if u.endswith("touch.png"):
            return b"<html>blocked</html>", "text/html"          # not a picture: the next one
        if u in pics:
            return pics[u]
        raise ValueError("404")

    pages = {"https://careers.example.com/campus": ("https://careers.example.com/campus", page)}
    got = logos.from_sites(["https://app.mokahr.com/campus/acme", "https://careers.example.com/campus"],
                           page=lambda u: pages[u], download=download, types=LOGO_TYPES)
    assert got == (PNG, "image/png", "https://careers.example.com/favicon.ico")
    assert not any("mokahr" in u for u in asked)                    # a hiring system's icon is the vendor's
    # a site that redirects to a hiring system gives no logo either
    assert logos.from_sites(["https://jobs.example.com"], page=lambda u: ("https://acme.jobs.feishu.cn/", page),
                            download=download, types=LOGO_TYPES) is None
    # a 16x16 icon is passed over for the next one; a blank one too, as the window sees it
    pics["https://careers.example.com/campus/icon.svg"] = (TINY, "image/png")
    got = logos.from_sites(["https://careers.example.com/campus"], page=lambda u: pages[u], download=download, types=LOGO_TYPES)
    assert got[2] == "https://careers.example.com/favicon.ico"
    assert logos.verdict({"width": 64, "height": 64, "visible": 0.0}) and logos.verdict({"error": True})
    assert logos.verdict({"width": 16, "height": 16, "visible": 0.5}, TINY, "image/png")
    assert not logos.verdict({"width": 0, "height": 0, "visible": 0.3}, b"<svg/>", "image/svg+xml")


def test_the_user_changes_or_removes_a_logo(live, tmp_path):
    hunt = live.inst.hunt
    live.req("POST", "/api/hunt/companies", {"name": "星河科技"})
    hunt.save_logo("C1", b"GIF89a", "image/gif")                       # a wrong one, from the site
    pic = tmp_path / "mine.png"
    pic.write_bytes(PNG)
    code, v = live.req("PUT", "/api/hunt/logos/C1", {"path": str(pic)})
    assert code == 200 and v["company"]["logo"] == "C1.png" and "logo" in v["company"]["kept"]
    assert hunt.logo_path("C1").read_bytes() == PNG and not (hunt.dir / "logos" / "C1.gif").exists()
    hunt.save_logo("C1", b"GIF89a", "image/gif")                       # Claude's next fill leaves it
    assert hunt.company("C1")["logo"] == "C1.png"
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert live.req("PUT", "/api/hunt/logos/C1", {"path": str(tmp_path / "notes.txt")})[0] == 400
    code, v = live.req("DELETE", "/api/hunt/logos/C1")
    assert code == 200 and v["company"]["logo"] == "" and hunt.logo_path("C1") is None
    hunt.save_logo("C1", PNG, "image/png")                             # gone stays gone
    assert hunt.company("C1")["logo"] == ""


def test_a_logo_changed_from_outside_shows_without_a_restart(live):
    hunt = live.inst.hunt
    live.req("POST", "/api/hunt/companies", {"name": "星河科技"})
    hunt.save_logo("C1", b"GIF89a", "image/gif")
    before = live.req("GET", "/api/hunt")[1]["companies"][0]["logo_url"]
    told = []
    hunt.on_change = lambda: told.append(1)
    (hunt.dir / "logos" / "C1.gif").write_bytes(b"GIF89a-a-new-picture")   # a script writes over it, around the app
    for _ in range(80):
        if told:
            break
        time.sleep(0.05)
    assert told                                                              # the window is told to reload
    after = live.req("GET", "/api/hunt")[1]["companies"][0]["logo_url"]
    assert after != before                                                   # and asks for the picture anew


def test_a_company_with_a_site_gets_its_logo_by_itself(live, data_file):
    hunt = live.inst.hunt
    looked = []

    def finder(sites, check):
        looked.append(sites)
        return (PNG, "image/png", "https://careers.xinghe.example/icon.png") if "xinghe" in sites[0] else None

    hunt.find_logo = finder
    live.req("POST", "/api/hunt/companies", {"name": "没网站的公司"})
    live.req("POST", "/api/hunt/companies", {"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    for _ in range(100):
        c = hunt.company("星河科技")
        if c.get("logo") and (c.get("sources") or {}).get("logo"):   # the picture, then where it came from
            break
        time.sleep(0.05)
    assert c["logo"] == "C2.png" and hunt.logo_path("C2").read_bytes() == PNG
    assert c["sources"]["logo"] == "https://careers.xinghe.example/icon.png" and c["kept"] == []   # still Claude's to replace
    assert looked == [["https://careers.xinghe.example/campus"]]  # nothing to look at without a site
    tab = live.req("GET", "/api/hunt")[1]
    assert [bool(x["logo_url"]) for x in tab["companies"]] == [True, False]
    # once a company has a logo it isn't looked for again, and one with none found isn't asked twice per run
    hunt.want_logos()
    live.req("PUT", "/api/hunt/companies/C1", {"site": "https://nothing.example.com"})
    hunt.want_logos("C1")
    time.sleep(0.5)
    assert looked == [["https://careers.xinghe.example/campus"], ["https://nothing.example.com"]]


def test_a_companys_deadline_moves_to_its_roles(hunt):
    hunt.add_company({"name": "星河科技"})
    hunt.add_application("星河科技", {"role": "服务端"})
    hunt.add_application("星河科技", {"role": "算法", "apply_by": "2026-11-30"})
    data = json.loads((hunt.dir / "companies.json").read_text(encoding="utf-8"))
    data["companies"][0]["deadline"] = "2026-10-31"                                # a file from before 2026-10-18
    (hunt.dir / "companies.json").write_text(json.dumps(data), encoding="utf-8")
    hunt.migrate()
    assert "deadline" not in hunt.company("C1")
    due = lambda a: next(e["at"] for e in a["schedule"] if e.get("kind") == "apply")  # noqa: E731
    assert {a["role"]: due(a) for a in hunt.applications()} == {"服务端": "2026-10-31", "算法": "2026-11-30"}
    hunt.migrate()                                                                # once only
    assert due(hunt.application("星河科技/服务端")) == "2026-10-31"


def test_places_written_before_are_looked_up_in_the_list_once(hunt):
    hunt.add_company({"name": "星河科技"})
    hunt.add_application("星河科技", {"role": "服务端"})
    cs = json.loads((hunt.dir / "companies.json").read_text(encoding="utf-8"))
    c = cs["companies"][0]
    c.pop("hq")
    c.update(location="北京（海淀区西二旗西街 16 号）", sources={"location": "https://example.com/hq"}, kept=["location"],
             places=[{"kind": "onsite", "area": "悉尼"}, {"kind": "onsite", "area": "Sydney"}, {"kind": "onsite", "area": "滨江园区"}])
    (hunt.dir / "companies.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
    apps = json.loads((hunt.dir / "applications.json").read_text(encoding="utf-8"))
    apps["applications"][0]["places"] = [{"kind": "remote", "area": "中国大陆"}]
    apps["migrated"] = ["builtin-events"]
    (hunt.dir / "applications.json").write_text(json.dumps(apps, ensure_ascii=False), encoding="utf-8")
    hunt.migrate()
    c = hunt.company("C1")
    assert c["hq"] == {"area": "Beijing", "id": "g1816670"} and "location" not in c   # the city; the address goes
    assert c["sources"] == {"hq": "https://example.com/hq"} and c["kept"] == ["hq"]
    # 悉尼 and Sydney are one place now; what the list doesn't know stays as written
    assert c["places"] == [{"kind": "onsite", "area": "Sydney", "id": "g2147714"}, {"kind": "onsite", "area": "滨江园区"}]
    assert hunt.applications()[0]["places"] == [{"kind": "remote", "area": "Mainland China", "id": "r:mainland-china"}]
    before = json.loads((hunt.dir / "companies.before-place-ids.json").read_text(encoding="utf-8"))
    assert before["companies"][0]["location"].startswith("北京（海淀区")          # the files as they were, kept beside them
    hunt.update_company("C1", {"places": ["杭州"]})
    hunt.migrate()                                                                  # once only
    assert hunt.company("C1")["places"] == [{"kind": "onsite", "area": "Hangzhou", "id": "g1808926"}]


def test_claude_fills_a_company_from_its_command(live, data_file, monkeypatch):
    monkeypatch.setattr(hunt_api, "fetch_logo", lambda url: (PNG, "image/png"))
    cli(data_file, "add-company", "星河科技")
    code, out = cli(data_file, "show", "星河科技")
    assert "not filled yet: industry, strength, hq, site, logo" in out
    fields = {"name": "Xinghe Holdings", "industry": "电商", "strength": "市值约 1,600 亿美元", "location": "上海",
              "sites": [{"url": "https://careers.xinghe.example/campus", "kind": "campus", "system": "own site"}],
              "logo_url": "https://example.com/xinghe.png", "sources": {"strength": "https://example.com/q"}}
    code, out = cli(data_file, "fill", "星河科技", "--stdin", stdin=json.dumps(fields, ensure_ascii=False))
    assert code == 0, out
    assert "strength: 市值约 1,600 亿美元  [https://example.com/q]" in out and "logo: saved" in out
    assert "site (campus, own site): https://careers.xinghe.example/campus" in out and "not filled yet" not in out
    tab = live.req("GET", "/api/hunt")[1]
    c = tab["companies"][0]
    assert c["logo_url"].startswith("/api/hunt/logos/C1?v=") and c["strength"] == "市值约 1,600 亿美元"
    code, raw = live.req("GET", c["logo_url"].lstrip("/"))
    assert code == 200 and raw == PNG
    # what the user edits in the window stays: the next fill says so
    live.req("PUT", "/api/hunt/companies/C1", {"strength": "我自己写的"})
    code, out = cli(data_file, "fill", "C1", "--stdin", stdin=json.dumps({"strength": "别的"}, ensure_ascii=False))
    assert "left as the user set them: strength" in out and "strength: 我自己写的" in out
    # deadlines are the roles'
    code, out = cli(data_file, "add-role", "星河科技", "服务端研发工程师", "--apply-by", "2026-10-31", "--url", "https://careers.xinghe.example/job/1")
    assert "apply by 2026-10-31" in out
    code, out = cli(data_file, "edit-role", "星河科技/服务端研发", "--apply-by", "", "--batch", "27秋招")
    assert code == 0 and "apply by" not in out and "batch 27秋招" in out             # "" drops it: nothing known
    code, out = cli(data_file, "edit-role", "星河科技/服务端研发", "--apply-by", "rolling")
    assert code == 0 and "apply by until filled (招满即止)" in out


# ---------------------------------------------------------------- open postings
def test_postings_are_matched_across_scans(hunt):
    ps = Postings(hunt)
    hunt.add_company({"name": "Tencent"})
    first = [{"title": "后台开发工程师", "url": "https://careers.tencent.com/jobdesc.html?postId=1#x", "location": "深圳"},
             {"title": "算法工程师（实习）", "url": "https://careers.tencent.com/jobdesc.html?postId=2", "deadline": "2026-11-01"},
             {"title": "No link here", "location": "Sydney"}, {"title": ""}, "junk"]
    r = ps.merge("Tencent", first, how="tencent")
    assert r["found"] == 3 and len(r["new"]) == 3
    intern = next(p for p in ps.all() if p["title"].startswith("算法"))
    assert intern["batch"] == "intern" and intern["deadline"] == "2026-11-01"
    # the next scan: one gone, one deadline moved, the anchor ignored, one new
    second = [{"title": "后台开发工程师", "url": "https://careers.tencent.com/jobdesc.html?postId=1"},
              {"title": "算法工程师（实习）", "url": "https://careers.tencent.com/jobdesc.html?postId=2", "deadline": "2026-11-15"},
              {"title": "AI Agent 工程师", "url": "https://careers.tencent.com/jobdesc.html?postId=3"}]
    r = ps.merge("Tencent", second, how="tencent")
    assert [p["title"] for p in r["new"]] == ["AI Agent 工程师"]
    assert [p["title"] for p in r["closed"]] == ["No link here"]
    assert r["changed"][0]["changed"]["deadline"] == ["2026-11-01", "2026-11-15"]
    # a partial read (one site of two failed) closes nothing
    r = ps.merge("Tencent", second[:1], how="tencent", complete=False)
    assert not r["closed"] and sum(1 for p in ps.all() if not p["closed"]) == 3
    # keywords fold away what the user doesn't look for, and only matching news counts
    ps.settings(keywords="Agent, 算法")
    assert ps.summary()["C1"]["open"] == 3 and ps.summary()["C1"]["matching"] == 2
    # going for one: it becomes an application, linked, once
    agent = next(p for p in ps.all() if "Agent" in p["title"])
    a = ps.apply(agent["id"])
    assert a["role"] == "AI Agent 工程师" and a["url"].endswith("postId=3") and ps.get(agent["id"])["app"] == a["id"]
    assert ps.apply(agent["id"])["id"] == a["id"] and len(hunt.applications()) == 1
    # its location becomes the role's places; one that can't be read so stays in the note
    ps.merge("Tencent", [{"title": "前端开发", "url": "https://careers.tencent.com/jobdesc.html?postId=4", "location": "北京/深圳"},
                         {"title": "海外研发", "url": "https://careers.tencent.com/jobdesc.html?postId=5", "location": "Remote"}],
             how="tencent", complete=False)
    two = ps.apply(next(p for p in ps.all() if p["title"] == "前端开发")["id"])
    assert two["places"] == [{"kind": "onsite", "area": "Beijing", "id": "g1816670"}, {"kind": "onsite", "area": "Shenzhen", "id": "g1795565"}]
    assert "北京" not in two["note"]
    away = ps.apply(next(p for p in ps.all() if p["title"] == "海外研发")["id"])
    assert away["places"] == [] and away["note"] == "Remote"
    for x in (two, away):
        hunt.delete_application(x["id"])
    # a role added by hand before the scan found it: linked, not offered again
    hunt.add_application("Tencent", {"role": "后台开发工程师"})
    ps.merge("Tencent", second, how="tencent")
    assert next(p for p in ps.all() if p["title"] == "后台开发工程师")["app"] == hunt.application("Tencent/后台开发工程师")["id"]
    assert fingerprint({"url": "HTTPS://Careers.Tencent.com/a/"}) == "https://careers.tencent.com/a"
    # Moka links every posting through its hash router: two ids are two postings, a plain anchor is nothing
    moka = "https://app.mokahr.com/campus-recruitment/example/10001#/job/"
    assert fingerprint({"url": moka + "a1"}) != fingerprint({"url": moka + "b2"})
    assert fingerprint({"url": "https://x.example/jobs#apply"}) == "https://x.example/jobs"
    assert clean({"title": "管培生 2027 校园招聘"})["batch"] == "campus"


class FakeBrowser:
    def __init__(self, pages: dict, off: bool = False):
        self.pages, self.at, self.opened = pages, "", []
        self.off, self.holds, self.max_holds = off, 0, 0
        self.held_while_reading = []

    def ensure(self):
        started, self.off = self.off, False
        return started

    def hold(self):
        fake = self

        class Hold:
            def __enter__(self):
                fake.holds += 1
                fake.max_holds = max(fake.max_holds, fake.holds)

            def __exit__(self, *exc):
                fake.holds -= 1
        return Hold()

    def open(self, url, new_tab=False):
        self.at = url
        self.opened.append(url)
        return {"url": url}

    def run_script(self, script, timeout=60.0):
        return self.pages[self.at]

    def read_page(self, url, script, timeout=60.0):
        self.opened.append(url)
        self.held_while_reading.append(self.holds > 0)
        return self.pages[url]


class NoTool:
    def installed(self):
        return False


def test_a_site_is_scanned_by_its_recorded_route(hunt):
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    page = "https://careers.xinghe.example/campus/jobs"
    browser = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1", "batch": "campus"}]}})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    assert sc.scan("星河科技")["needs"] == "claude"                                 # no provider, no route yet
    assert ps.scan_state("C1")["needs"] == "claude"
    assert validate_route({"pages": [{"url": "ftp://x", "script": "1"}]})[1]
    r, errs = sc.save_route("星河科技", {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]})
    assert not errs
    res = sc.scan("星河科技")
    assert (res["found"], res["new"], res["needs"]) == (1, 1, "") and browser.opened == [page]
    assert ps.scan_state("C1")["how"] == "route" and ps.scan_state("C1")["needs"] == ""
    # the site asks to sign in: the scan says so, the postings stay
    browser.pages[page] = {"signed_out": True}
    res = sc.scan("星河科技")
    assert res["signed_out"] and res["route_broken"] and len(ps.all()) == 1 and not ps.all()[0]["closed"]
    # a route that reads nothing at all is broken (the site changed), never "everything closed"
    browser.pages[page] = {"postings": []}
    res = sc.scan("星河科技")
    assert res["route_broken"] and not res["signed_out"] and not ps.all()[0]["closed"]


def test_a_scan_holds_the_browser_while_it_reads_and_says_what_it_found(hunt):
    """The browser closes itself when nothing holds it (core/browser.py close_if_idle): a scan holds it from the first
    company to the last, so it is never closed between them, and lets go when it is done."""
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    page = "https://careers.xinghe.example/campus/jobs"
    browser = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1"}]}}, off=True)
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.save_route("星河科技", {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]})
    sc.scan_many(["C1"])
    assert browser.opened == [page] and browser.held_while_reading == [True] and browser.holds == 0
    assert {k: sc.last_run[k] for k in ("auto", "companies", "new", "closed", "failed")} == \
        {"auto": False, "companies": 1, "new": 1, "closed": 0, "failed": 0}
    sc.scan("C1")                                   # one company on its own (Claude saving a route) holds it too
    assert browser.held_while_reading[-1] is True and browser.holds == 0
    # a site that asks to sign in counts as a company that couldn't be read, and lets go all the same
    browser.pages[page] = {"signed_out": True}
    sc.scan_many(["C1"])
    assert sc.last_run["failed"] == 1 and browser.holds == 0


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")


@needs_node
def test_career_ops_providers_read_a_site_through_the_bridge(hunt, tmp_path):
    """jobs/co_scan.mjs loads career-ops' providers from its own folder (here a stand-in with its registry's shape)."""
    co = tmp_path / "career-ops"
    (co / "providers").mkdir(parents=True)
    (co / "providers" / "_registry.mjs").write_text("""
import { readdirSync } from 'fs'; import path from 'path'; import { pathToFileURL } from 'url';
export async function loadProviders(dir) { const m = new Map();
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.mjs') && !f.startsWith('_')))
    { const p = (await import(pathToFileURL(path.join(dir, f)).href)).default; m.set(p.id, p); } return m; }
export function resolveProvider(entry, providers) { for (const p of providers.values()) if (p.detect?.(entry)) return { provider: p }; return null; }
""", encoding="utf-8")
    (co / "providers" / "_http.mjs").write_text("export function makeHttpCtx() { return { fetchJson: async (url, opts) => ({ opts }) }; }\n", encoding="utf-8")
    # a Feishu tenant answers for the portal named in the header: the bridge names it from the site's address
    (co / "providers" / "feishu.mjs").write_text("""
export default { id: 'feishu-jobs', detect: (e) => (e.careers_url.includes('.jobs.feishu.cn') ? { url: e.careers_url } : null),
  async fetch(entry, ctx) { const { opts } = await ctx.fetchJson('https://x.jobs.feishu.cn/api', { headers: { a: '1' } });
    return [{ title: opts.headers['website-path'] || 'none', url: 'https://x.jobs.feishu.cn/index/position/7/detail' }]; } };
""", encoding="utf-8")
    (co / "providers" / "demo.mjs").write_text("""
export default { id: 'demo', detect: (e) => (e.careers_url.includes('jobs.example.com') ? { url: e.careers_url } : null),
  async fetch(entry) { console.log('noise on stdout'); return [
    { title: 'Graduate Engineer', url: 'https://jobs.example.com/1', location: 'Sydney', department: 'Eng', postedAt: Date.UTC(2026, 9, 1) },
    { title: '', url: 'https://jobs.example.com/2' }]; } };
""", encoding="utf-8")

    class Tool:
        root = co

        def installed(self):
            return True

        def node(self, *args, timeout=120):
            import subprocess
            return subprocess.run([shutil.which("node"), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout)

    ps = Postings(hunt)
    hunt.add_company({"name": "Example", "sites": [{"url": "https://jobs.example.com", "kind": "campus"},
                                                   {"url": "https://example.com/careers", "kind": "social"}]})
    sc = Scanner(hunt, ps, Tool(), None, lambda: None)
    assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["demo", "claude"]
    res = sc.scan("C1")
    assert (res["found"], res["new"], res["needs"]) == (1, 1, "claude")         # the other site still needs Claude
    p = ps.all()[0]
    assert (p["title"], p["location"], p["category"], p["posted"]) == ("Graduate Engineer", "Sydney", "Eng", "2026-10-01")
    hunt.add_company({"name": "示例基金", "sites": [{"url": "https://gf.jobs.feishu.cn/123456", "kind": "campus"}]})
    sc.scan("示例基金")
    p = next(x for x in ps.all() if x["company"] == "C2")
    assert (p["title"], p["url"]) == ("123456", "https://x.jobs.feishu.cn/123456/position/7/detail")


def test_the_window_and_claude_scan_and_apply(live, data_file, monkeypatch):
    cli(data_file, "add-company", "星河科技", "--site", "https://careers.xinghe.example/campus")
    page = "https://careers.xinghe.example/campus/jobs"
    fake = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1"},
                                            {"title": "算法工程师", "url": page + "/2", "deadline": "2026-10-31"}]},
                        page + "/2": {"jd": "岗位职责\n1、训练模型", "text": "工作地点\n工作地点\n杭州/上海\n截止日期：2026-11-30"}})
    monkeypatch.setattr(live.inst.hunt.scanner, "browser", fake)
    code, out = cli(data_file, "scan")
    assert "C1: needs claude" in out
    route = {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]}
    code, out = cli(data_file, "scan-route", "星河科技", "--stdin", stdin=json.dumps(route))
    assert code == 0 and "C1: 2 open, 2 new, 0 closed, 0 changed" in out and "P2" in out and "closes 2026-10-31" in out
    tab = live.req("GET", "/api/hunt")[1]
    c = tab["companies"][0]
    assert c["postings"]["open"] == 2 and c["postings"]["news"]["new"] == 2 and c["postings"]["needs"] == ""
    assert [len(x["new"]) for x in tab["changes"]] == [2]
    v = live.req("GET", "/api/hunt/companies/C1")[1]
    assert len(v["postings"]) == 2 and v["scan"]["recorded"] and v["scan"]["how"] == "route"
    code, r = live.req("POST", "/api/hunt/postings/P2/apply")
    assert code == 200 and r["application"]["role"] == "算法工程师" and r["application"]["schedule"][0]["at"] == "2026-10-31"
    # going for it reads its page: the description and the cities; the scanned deadline stays
    aid = r["application"]["id"]
    end = time.time() + 10
    while time.time() < end and (r["reading"].get("running") or not r["application"]["jd"]):
        time.sleep(0.05)
        r = live.req("GET", f"/api/hunt/apps/{aid}")[1]
    a = r["application"]
    assert a["jd"] == "岗位职责\n1、训练模型" and [p["area"] for p in a["places"]] == ["Hangzhou", "Shanghai"] and a["jd_read"]
    assert a["schedule"][0]["at"] == "2026-10-31" and not r["reading"]
    # reading it again on request replaces the description; a page with none says so
    fake.pages[page + "/2"]["jd"] = "岗位职责\n2、部署模型"
    live.req("POST", f"/api/hunt/apps/{aid}/read", {})
    end = time.time() + 10
    while time.time() < end and live.req("GET", f"/api/hunt/apps/{aid}")[1]["application"]["jd"] != "岗位职责\n2、部署模型":
        time.sleep(0.05)
    fake.pages[page + "/2"]["jd"] = ""
    live.req("POST", f"/api/hunt/apps/{aid}/read", {})
    end = time.time() + 10
    while time.time() < end and not live.req("GET", f"/api/hunt/apps/{aid}")[1]["reading"].get("error"):
        time.sleep(0.05)
    r = live.req("GET", f"/api/hunt/apps/{aid}")[1]
    assert r["reading"]["error"] and r["application"]["jd"] == "岗位职责\n2、部署模型"
    code, out = cli(data_file, "keywords", "后端, 服务端")
    assert "keywords: 后端, 服务端" in out
    # Claude's own reading, when no route can be recorded
    code, out = cli(data_file, "postings", "星河科技", "--stdin", stdin=json.dumps({"postings": [{"title": "服务端研发工程师", "url": page + "/1"}]}))
    assert "1 open, 0 new, 1 closed" in out
    # the daily scan: once a day, only when it is on
    assert live.req("PUT", "/api/hunt/scan-settings", {"daily": False})[1]["daily"] is False
    assert live.req("POST", "/api/hunt/scan", {"auto": True})[1]["skipped"]



def test_a_posting_page_says_where_and_until_when():
    from jobhunt.reader import details
    # Moka: the label twice, then "-" for nothing
    assert details("工作地点\n工作地点\n-\n发布日期\n2026-09-29") == {}
    # Workday: an English label, the date in Chinese under its own heading
    d = details("locations\nSydney: 1 Market St\ntime left to apply\n结束日期：2026年10月31日 (申请时间还剩 9 天)")
    assert d == {"places": [{"kind": "onsite", "area": "Sydney", "id": "g2147714"}], "deadline": "2026-10-31"}   # the city, not its street
    assert details("Location is flexible\nApplications close 31 Oct 2026") == {"deadline": "2026-10-31"}
    assert details("工作地点：北京、上海")["places"] == [{"kind": "onsite", "area": "Beijing", "id": "g1816670"},
                                                   {"kind": "onsite", "area": "Shanghai", "id": "g1796236"}]


def test_claude_is_told_to_fill_scan_and_read_links():
    from jobhunt import prompts
    note = prompts.tools_note("E:/data/resume.yaml")
    assert "fill <company> --stdin" in note and "Never stop at the About" in note
    assert "scan-route" in note and "A posting's link" in note and "never to the company" in note
