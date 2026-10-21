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

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


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
    assert got["strength"].startswith("市值约") and got["location"] == "上海"
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


def test_a_company_with_a_site_gets_its_logo_by_itself(live, data_file):
    hunt = live.inst.hunt
    looked = []

    def finder(sites):
        looked.append(sites)
        return (PNG, "image/png", "https://careers.xinghe.example/icon.png") if "xinghe" in sites[0] else None

    hunt.find_logo = finder
    live.req("POST", "/api/hunt/companies", {"name": "没网站的公司"})
    live.req("POST", "/api/hunt/companies", {"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    for _ in range(100):
        c = hunt.company("星河科技")
        if c.get("logo"):
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
    hunt.add_application("星河科技", {"role": "算法", "deadline": "2026-11-30"})
    data = json.loads((hunt.dir / "companies.json").read_text(encoding="utf-8"))
    data["companies"][0]["deadline"] = "2026-10-31"                                # a file from before 2026-10-18
    (hunt.dir / "companies.json").write_text(json.dumps(data), encoding="utf-8")
    hunt.migrate()
    assert "deadline" not in hunt.company("C1")
    due = lambda a: next(e["at"] for e in a["schedule"] if e.get("kind") == "apply")  # noqa: E731
    assert {a["role"]: due(a) for a in hunt.applications()} == {"服务端": "2026-10-31", "算法": "2026-11-30"}
    hunt.migrate()                                                                # once only
    assert due(hunt.application("星河科技/服务端")) == "2026-10-31"


def test_claude_fills_a_company_from_its_command(live, data_file, monkeypatch):
    monkeypatch.setattr(hunt_api, "fetch_logo", lambda url: (PNG, "image/png"))
    cli(data_file, "add-company", "星河科技")
    code, out = cli(data_file, "show", "星河科技")
    assert "not filled yet: industry, strength, location, site, logo" in out
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
    code, out = cli(data_file, "add-role", "星河科技", "服务端研发工程师", "--deadline", "2026-10-31", "--url", "https://careers.xinghe.example/job/1")
    assert "deadline 2026-10-31" in out
    code, out = cli(data_file, "edit-role", "星河科技/服务端研发", "--deadline", "rolling", "--batch", "27秋招")
    assert code == 0 and "deadline rolling (招满即止)" in out and "batch 27秋招" in out


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
    assert two["places"] == [{"kind": "onsite", "area": "北京"}, {"kind": "onsite", "area": "深圳"}] and "北京" not in two["note"]
    away = ps.apply(next(p for p in ps.all() if p["title"] == "海外研发")["id"])
    assert away["places"] == [] and away["note"] == "Remote"
    for x in (two, away):
        hunt.delete_application(x["id"])
    # a role added by hand before the scan found it: linked, not offered again
    hunt.add_application("Tencent", {"role": "后台开发工程师"})
    ps.merge("Tencent", second, how="tencent")
    assert next(p for p in ps.all() if p["title"] == "后台开发工程师")["app"] == hunt.application("Tencent/后台开发工程师")["id"]
    assert fingerprint({"url": "HTTPS://Careers.Tencent.com/a/"}) == "https://careers.tencent.com/a"
    assert clean({"title": "管培生 2027 校园招聘"})["batch"] == "campus"


class FakeBrowser:
    def __init__(self, pages: dict):
        self.pages, self.at, self.opened = pages, "", []

    def ensure(self):
        pass

    def open(self, url, new_tab=False):
        self.at = url
        self.opened.append(url)
        return {"url": url}

    def run_script(self, script, timeout=60.0):
        return self.pages[self.at]


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
    (co / "providers" / "_http.mjs").write_text("export function makeHttpCtx() { return { fetchJson: async () => ({}) }; }\n", encoding="utf-8")
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


def test_the_window_and_claude_scan_and_apply(live, data_file, monkeypatch):
    cli(data_file, "add-company", "星河科技", "--site", "https://careers.xinghe.example/campus")
    page = "https://careers.xinghe.example/campus/jobs"
    fake = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1"},
                                            {"title": "算法工程师", "url": page + "/2", "deadline": "2026-10-31"}]}})
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
    code, out = cli(data_file, "keywords", "后端, 服务端")
    assert "keywords: 后端, 服务端" in out
    # Claude's own reading, when no route can be recorded
    code, out = cli(data_file, "postings", "星河科技", "--stdin", stdin=json.dumps({"postings": [{"title": "服务端研发工程师", "url": page + "/1"}]}))
    assert "1 open, 0 new, 1 closed" in out
    # the daily scan: once a day, only when it is on
    assert live.req("PUT", "/api/hunt/scan-settings", {"daily": False})[1]["daily"] is False
    assert live.req("POST", "/api/hunt/scan", {"auto": True})[1]["skipped"]



def test_claude_is_told_to_fill_scan_and_read_links():
    from jobhunt import prompts
    note = prompts.tools_note("E:/data/resume.yaml")
    assert "fill <company> --stdin" in note and "Never stop at the About" in note
    assert "scan-route" in note and "A posting's link" in note and "never to the company" in note
