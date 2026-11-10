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
    return Hunt(tmp_path / "resume-studio-data")


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
    code, out = cli(data_file, "edit-role", "星河科技/服务端研发", "--apply-by", "")
    assert code == 0 and "apply by" not in out                                     # "" drops it: nothing known
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

    def read_pages(self, url, scripts, timeout=60.0, open_timeout=30.0):
        out = []
        for s in scripts:
            try:
                out.append(self.read_page(url, s, timeout))
            except Exception as e:
                out.append(e)
        return out


class NoTool:
    def installed(self):
        return False


def test_a_role_follows_its_posting_off_the_site_and_back(hunt):
    ps = Postings(hunt)
    hunt.add_company({"name": "Tencent"})
    url = "https://careers.tencent.com/jobdesc.html?postId="
    found = [{"title": "后台开发工程师", "url": url + "1"}, {"title": "算法工程师", "url": url + "2"}, {"title": "测试开发", "url": url + "3"}]
    # one added by Claude under another name, linked by its link; one by the same name; one applied to already
    want = hunt.add_application("Tencent", {"role": "后台开发（深圳）", "url": "HTTPS://Careers.Tencent.com/jobdesc.html?postId=1"})
    same = hunt.add_application("Tencent", {"role": "算法工程师"})
    sent = hunt.add_application("Tencent", {"role": "测试开发", "url": url + "3", "status": "interview"})
    ps.merge("Tencent", found, how="tencent")
    assert {p["title"]: p["app"] for p in ps.all()} == {"后台开发工程师": want["id"], "算法工程师": same["id"], "测试开发": sent["id"]}
    # a partial read closes nothing, so changes nothing
    ps.merge("Tencent", [], how="tencent", complete=False)
    assert hunt.application(want["id"])["status"] == "saved"
    # all gone from the site: the ones not applied to are closed, the interview goes on with a line in its timeline
    r = ps.merge("Tencent", [{"title": "别的岗位", "url": url + "9"}], how="tencent")
    assert sorted(r["roles_closed"]) == sorted([want["id"], same["id"]])
    assert [hunt.application(x["id"])["status"] for x in (want, same, sent)] == ["closed", "closed", "interview"]
    ev = [e for e in hunt.events(sent["id"]) if e["kind"] == "note"]
    assert len(ev) == 1 and ev[0]["source"] == "scan" and "Tencent" in ev[0]["note"]
    last = hunt.events(want["id"])[-1]
    assert last["to"] == "closed" and last["source"] == "scan"
    # a closed role isn't open, and isn't stamped as applied
    assert hunt.application(want["id"])["applied"] == ""
    # back on the site: back to want to apply
    ps.merge("Tencent", found, how="tencent")
    assert [hunt.application(x["id"])["status"] for x in (want, same, sent)] == ["saved", "saved", "interview"]
    # a role the user withdrew stays withdrawn when its posting goes
    hunt.set_status(same["id"], "withdrawn")
    ps.merge("Tencent", found[:1] + found[2:], how="tencent")
    assert hunt.application(same["id"])["status"] == "withdrawn"
    # a role added after its posting left the site follows it at the next read that finds it still gone
    ps.merge("Tencent", found + [{"title": "运维开发", "url": url + "5"}], how="tencent")
    ps.merge("Tencent", found, how="tencent")
    late = hunt.add_application("Tencent", {"role": "运维（深圳）", "url": url + "5"})
    r = ps.merge("Tencent", found, how="tencent")
    assert r["roles_closed"] == [late["id"]] and hunt.application(late["id"])["status"] == "closed"


def test_a_role_with_a_scanned_postings_link_is_linked_at_once(hunt):
    """User, 2026-11-01: a posting's link is unique; a role given it is that posting now, not at the next scan."""
    ps = Postings(hunt)
    hunt.add_company({"name": "Tencent"})
    hunt.add_company({"name": "星河科技"})
    url = "https://careers.tencent.com/jobdesc.html?postId="
    ps.merge("Tencent", [{"title": "后台开发工程师", "url": url + "1"}], how="tencent")
    assert ps.by_url("HTTPS://Careers.Tencent.com/jobdesc.html?postId=1")["id"] == "P1"   # compared as a scan compares
    assert ps.by_url(url + "1&utm_source=wechat&spm=a.b#top")["id"] == "P1"               # tracking from sharing left out
    assert ps.by_url(url + "2") is None and ps.by_url("") is None and ps.by_url("careers.tencent.com") is None
    other = hunt.add_application("星河科技", {"role": "后台开发", "url": url + "1"})   # another company's role: not it
    ps.link(other)
    assert ps.get("P1")["app"] is None
    a = hunt.add_application("Tencent", {"role": "后台开发（深圳）", "url": url + "1"})
    ps.link(a)
    assert ps.get("P1")["app"] == a["id"]
    b = hunt.add_application("Tencent", {"role": "后台开发（北京）", "url": url + "1"})   # taken: the first one keeps it
    ps.link(b)
    assert ps.get("P1")["app"] == a["id"]


def test_adding_by_link_finds_the_role_or_the_scanned_posting(live, data_file):
    do = lambda cmd, **a: live.req("POST", "/api/hunt/do", {"cmd": cmd, "args": a})
    page = "https://careers.xinghe.example/campus/job"
    do("add-company", name="星河科技")
    do("postings", ref="星河科技", postings=[{"title": "服务端研发工程师", "url": page + "/1", "location": "北京", "deadline": "2030-11-30"},
                                         {"title": "算法工程师", "url": page + "/2"}])
    # a link no posting and no role has: the AI reads it
    assert live.req("POST", "/api/hunt/link", {"url": page + "/9"})[1] == {"found": None}
    # a scanned posting's link: it becomes a role the user wants to apply to, linked to it, with no AI
    code, r = live.req("POST", "/api/hunt/link", {"url": "HTTPS://Careers.Xinghe.example/campus/job/1/"})
    assert code == 200 and r["found"] == "posting"
    a = r["application"]
    assert (a["role"], a["status"], a["url"]) == ("服务端研发工程师", "saved", page + "/1")
    ps = live.req("GET", "/api/hunt/companies/C1")[1]["postings"]
    assert next(p for p in ps if p["url"] == page + "/1")["app"] == a["id"]
    # the same link again: the role on the list, not a second one
    r = live.req("POST", "/api/hunt/link", {"url": page + "/1"})[1]
    assert r["found"] == "app" and r["application"]["id"] == a["id"]
    assert len(live.req("GET", "/api/hunt")[1]["applications"]) == 1
    # the AI adding a role by a posting's link links it at once; by a role's link adds nothing
    r = do("add-role", company="星河科技", role="算法（北京）", url=page + "/2")[1]
    assert r["text"] == "added"
    ps = live.req("GET", "/api/hunt/companies/C1")[1]["postings"]
    assert next(p for p in ps if p["url"] == page + "/2")["app"] == r["app"]["application"]["id"]
    r = do("add-role", company="星河科技", role="后端", url=page + "/1/")[1]
    assert r["text"] == "already on the list" and r["app"]["application"]["id"] == a["id"]
    assert len(live.req("GET", "/api/hunt")[1]["applications"]) == 2


def test_postings_read_by_hand_leave_the_site_still_needing_a_route(live, data_file):
    do = lambda cmd, **a: live.req("POST", "/api/hunt/do", {"cmd": cmd, "args": a})
    page = "https://hr.daxue.example/rczp/index.htm"
    do("add-company", name="某大学", site=page)
    code, out = cli(data_file, "postings", "某大学", "--partial", "--stdin",
                    stdin=json.dumps({"postings": [{"title": "2027年度应届毕业生招聘公告", "url": page + "/1"}]}))
    assert code == 0 and "1 open" in out and "still needs: claude" in out and "Record its route with `scan-route`" in out
    c = live.req("GET", "/api/hunt")[1]["companies"][0]
    assert c["postings"]["open"] == 1 and c["postings"]["needs"] == "claude"   # the window still offers "Let AI learn its site"
    # once its route is recorded, postings sent by hand no longer bring the flag back
    live.inst.hunt.scanner.save_route("某大学", {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]}, check=False)
    r = do("postings", ref="某大学", postings=[{"title": "2027年度应届毕业生招聘公告", "url": page + "/1"}])[1]
    assert r["scanned"][0]["needs"] == "" and live.req("GET", "/api/hunt")[1]["companies"][0]["postings"]["needs"] == ""


def test_an_archive_of_announcements_keeps_old_ones_out_of_the_open_roles(hunt):
    """A university's list keeps every announcement: one past its closing date, or with none and posted over a year
    ago, is kept but never counted as open, and never news."""
    from jobhunt.postings import stale
    assert stale({"deadline": "2026-11-01"}, "2026-11-02") == "deadline"
    assert stale({"deadline": "2026-11-02"}, "2026-11-02") == ""                   # closes today: still open
    assert stale({"posted": "2025-11-01"}, "2026-11-02") == "old"
    assert stale({"posted": "2025-11-02"}, "2026-11-02") == ""                     # a year to the day: still counts
    assert stale({"posted": "2020-02-03", "deadline": "2030-01-01"}, "2026-11-02") == ""   # an old one that says it is open
    assert stale({"title": "no dates at all"}, "2026-11-02") == ""
    ps = Postings(hunt)
    hunt.add_company({"name": "某大学", "site": "https://hr.daxue.example/rczp/index.htm"})
    page = "https://hr.daxue.example/rczp/"
    ps.merge("某大学", [{"title": "2022年度研究技术系列人员招聘启事", "url": page + "1", "posted": "2022-05-30"},
                       {"title": "已截止的招聘公告", "url": page + "2", "posted": "2026-10-01", "deadline": "2026-10-15"},
                       {"title": "2026年教学科研岗位招聘启事", "url": page + "3", "posted": "2026-02-10"},
                       {"title": "行政管理岗", "url": page + "4", "posted": "2026-10-28", "deadline": "2030-11-15"}], how="route")
    s = ps.summary()["C1"]
    assert (s["open"], s["stale"], s["news"]["new"]) == (2, 2, 2)
    assert [r["title"] for r in ps.changes()[0]["new"]] == ["2026年教学科研岗位招聘启事", "行政管理岗"]
    assert not any(p["closed"] for p in ps.all())                                  # kept, only folded away


def test_a_site_is_scanned_by_its_recorded_route(hunt):
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    page = "https://careers.xinghe.example/campus/jobs"
    browser = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1", "batch": "campus"}]}})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    assert sc.scan("星河科技")["needs"] == "claude"                                 # no provider, no route yet
    assert ps.scan_state("C1")["needs"] == "claude"
    assert validate_route({"pages": [{"url": "ftp://x", "script": "1"}]})[1]
    many = {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}] * 21}
    assert "at most 20 pages" in validate_route(many)[1][0] and not validate_route({"pages": many["pages"][:20]})[1]
    sc.empty_wait = 0
    r, errs = sc.save_route("星河科技", {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]})
    assert not errs and browser.opened == [page]                                 # tried once before it is kept
    res = sc.scan("星河科技")
    assert (res["found"], res["new"], res["needs"]) == (1, 1, "") and browser.opened == [page, page]
    assert ps.scan_state("C1")["how"] == "route" and ps.scan_state("C1")["needs"] == ""
    # the site asks to sign in: the scan says so, the postings stay
    browser.pages[page] = {"signed_out": True}
    res = sc.scan("星河科技")
    assert res["signed_out"] and res["route_broken"] and len(ps.all()) == 1 and not ps.all()[0]["closed"]
    # a route that reads nothing at all is broken (the site changed), never "everything closed"
    browser.pages[page] = {"postings": []}
    res = sc.scan("星河科技")
    assert res["route_broken"] and not res["signed_out"] and not ps.all()[0]["closed"]


def test_a_list_a_provider_reads_never_goes_through_the_ai(hunt):
    """星河科技 (2026-11-03): its own pages campus.xinghe.ai and careers.xinghe.ai front Feishu boards. Asked to learn them,
    the AI recorded a route over both Feishu lists, one of which the Feishu provider already read through the company's
    Feishu site: 67 roles came in twice (/index/position/<id> and /xinghe/position/<id>)."""
    from jobhunt.scan import same_once
    ps = Postings(hunt)
    feishu, campus, careers = "https://xinghe.jobs.example.cn/", "https://campus.xinghe.example/", "https://careers.xinghe.example/"
    hunt.add_company({"name": "星河科技", "sites": [{"url": campus, "kind": "campus"}, {"url": feishu, "kind": "social"},
                                                  {"url": careers, "kind": "social"}]})
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    board = {feishu: "feishu-jobs", feishu + "xhcampus": "feishu-jobs", feishu + "xinghe": "feishu-jobs",
             feishu + "xhcampus/?current=1&limit=100": "feishu-jobs", campus: None, careers: None}
    sc._providers.update(board)
    # a route page on a list the provider reads is refused, with what to do instead
    r, errs = sc.save_route("C1", {"pages": [{"url": feishu + "xhcampus/?current=1&limit=100", "script": "async (h) => ({postings: []})"}]})
    assert not r and "feishu-jobs reads this list with no route" in errs[0] and '"list"' in errs[0]
    assert ps.route("C1") is None
    # the AI gives each own page its board instead; a later fill that leaves the list out keeps it
    hunt.fill("C1", {"sites": [{"url": campus, "kind": "campus", "list": feishu + "xhcampus"}, {"url": feishu, "kind": "social"},
                               {"url": careers, "kind": "social", "list": feishu + "xinghe"}]})
    hunt.fill("C1", {"sites": [{"url": campus, "kind": "campus"}, {"url": feishu, "kind": "social"}, {"url": careers, "kind": "social"}]})
    assert [s.get("list") for s in hunt.company("C1")["sites"]] == [feishu + "xhcampus", None, feishu + "xinghe"]
    assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["feishu-jobs"] * 3
    assert sc.site_status(hunt.company("C1")) == {campus: "api", feishu: "api", careers: "api"}
    # the provider reads each board once; the same role under two addresses of one board comes in once
    asked = []
    job = lambda board, n, t: {"title": t, "url": f"{feishu}{board}/position/77034793430420544{n}/detail"}
    def bridge(entries, detect=False):
        asked.extend(e["url"] for e in entries)
        return [{"url": e["url"], "provider": "feishu-jobs", "jobs": {feishu: [job("index", 1, "地图研发"), job("index", 2, "测试")],
                 feishu + "xinghe": [job("xinghe", 1, "地图研发"), job("xinghe", 2, "测试")],
                 feishu + "xhcampus": [job("xhcampus", 3, "实习生")]}[e["url"]]} for e in entries]
    sc._bridge = bridge
    res = sc.scan("C1")
    assert sorted(asked) == sorted([feishu, feishu + "xhcampus", feishu + "xinghe"]) and res["found"] == 3, res
    assert res["needs"] == "" and res["unrouted"] == []
    # one role kept once; two roles that only share a title, or have no id, are two
    same = [{"title": "A", "url": "https://x.example/index/position/12345678901/detail"},
            {"title": "A", "url": "https://x.example/xinghe/position/12345678901/detail"},
            {"title": "B", "url": "https://x.example/index/position/12345678901/detail"},
            {"title": "A", "url": "https://x.example/jobs/2"}, {"title": "A", "url": "https://x.example/jobs/3"}]
    assert [p["url"][-30:] for p in same_once(same)] == [p["url"][-30:] for p in [same[0], same[2], same[3], same[4]]]


def test_a_route_page_on_a_providers_list_is_read_through_the_provider(hunt):
    """Routes recorded before the refusal (8 companies, 2026-11-03: Moka and Workday boards read by the AI's scripts):
    the page a provider reads goes through the provider; only the page no provider reads runs its script."""
    ps = Postings(hunt)
    own, moka = "https://job.yunfan.example/", "https://app.mokahr.example/campus-recruitment/yunfan/46903#/jobs"
    social = "https://wecruit.hotjob.example/pb/social.html"
    hunt.add_company({"name": "云帆通讯", "sites": [{"url": own, "kind": "campus"}]})
    browser = FakeBrowser({social: {"postings": [{"title": "社招 A", "url": social + "#1"}]}})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc._providers.update({own: None, moka: "mokahr", social: None})
    ps.set_scan_state("C1", route={"pages": [{"url": moka, "script": "async (h) => ({postings: []})"},
                                             {"url": social, "script": "async (h) => ({postings: []})"}], "sites": [own]})
    asked = []
    sc._bridge = lambda entries, detect=False: asked.extend(e["url"] for e in entries) or [
        {"url": e["url"], "provider": "mokahr", "jobs": [{"title": "校招 B", "url": "https://app.mokahr.example/job/1"}]} for e in entries]
    res = sc.scan("C1")
    assert asked == [moka] and browser.opened == [social], (asked, browser.opened)
    assert res["found"] == 2 and ps.scan_state("C1")["how"] == "mokahr+route"


def test_a_site_added_after_the_route_was_recorded_is_checked_and_handed_to_the_ai(hunt):
    """上海交大 (2026-11-03): a third careers site was pasted in after the route was recorded for the first two.
    The scan replayed the old route, said "read, nothing missing" and never looked at the new address."""
    from jobhunt.scan import run_summary
    ps = Postings(hunt)
    a, b, new = "https://jobs.sjtu.example/", "https://join.shsmu.example/Home/Index", "https://join.sjtu.example/Default.aspx"
    hunt.add_company({"name": "上海交通大学", "sites": [{"url": a, "kind": "other"}, {"url": b, "kind": "other"}]})
    pa, pb = "https://jobs.sjtu.example/recruit/longterm", "https://join.shsmu.example/JobList"
    browser = FakeBrowser({pa: {"postings": [{"title": "管理岗", "url": pa + "/1"}]},
                           pb: {"postings": [{"title": "辅导员", "url": pb + "/1"}]},
                           new + "#jobs": {"postings": [{"title": "博士后", "url": new + "/1"}]}})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.start = lambda ids, auto=False: started.extend(ids)   # what check_sites would scan, without a thread
    started: list[str] = []
    # a route recorded before routes named their sites: it reads the sites its pages are on
    ps.set_scan_state("C1", route={"pages": [{"url": pa, "script": "async (h) => ({postings: []})"},
                                             {"url": pb, "script": "async (h) => ({postings: []})"}]})
    assert sc.scan("C1")["needs"] == ""
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": new}]})
    assert hunt.company("C1")["sites"][2]["kind"] == ""                       # no kind guessed for it
    assert [s["how"] for s in sc.plan(hunt.company("C1"))] == ["route", "route", "claude"]
    # added in the window: found out at once that no scan can read it, nothing scanned for nothing
    assert sc.check_sites("C1", [new]) == [new] and started == []
    assert ps.scan_state("C1")["needs"] == "claude" and ps.scan_state("C1")["unrouted"] == [new]
    res = sc.scan("C1")
    assert (res["found"], res["needs"], res["unrouted"]) == (2, "claude", [new])
    assert ps.scan_state("C1")["unrouted"] == [new]
    assert run_summary([res], auto=False)["unrouted"] == [new]                # the toast names the site, not "a company"
    # each site says on its row whether it is scanned automatically
    assert sc.site_status(hunt.company("C1")) == {a: "route", b: "route", new: "no"}
    # the site taken off the company outside the window (the AI's set-company): its warning goes with it
    hunt.update_company("C1", {"sites": [s for s in hunt.company("C1")["sites"] if s["url"] != new]})
    assert (sc.live_state(hunt.company("C1"))["needs"], sc.live_state(hunt.company("C1"))["unrouted"]) == ("", [])
    assert ps.scan_state("C1")["unrouted"] == [new]                              # stored as the last scan left it
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": new}]})
    # the AI learns only the new site: its pages join the route, and the route names the sites it reads
    r, errs = sc.save_route("C1", {"add": True, "pages": [{"url": new + "#jobs", "script": "async (h) => ({postings: []})"}]})
    assert not errs and [p["url"] for p in r["pages"]] == [pa, pb, new + "#jobs"] and set(r["sites"]) == {a, b, new}
    res = sc.scan("C1")
    assert (res["found"], res["new"], res["needs"], res["unrouted"]) == (3, 1, "", [])
    # one more address on a host the route already reads is still a list nobody has looked at
    other = "https://jobs.sjtu.example/campus"
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": other}]})
    assert sc.check_sites("C1", [other]) == [other]
    assert sc.scan("C1")["unrouted"] == [other]
    # removed again: its warning goes with it, at once
    hunt.update_company("C1", {"sites": hunt.company("C1")["sites"][:3]})
    assert sc.check_sites("C1", []) == [] and ps.scan_state("C1")["needs"] == "" and ps.scan_state("C1")["unrouted"] == []
    assert started == []                        # nothing new a scan can read, so none was started
    # one a provider reads (Moka, say) is scanned at once
    moka = "https://app.mokahr.example/campus-recruitment/sjtu/1"
    sc._providers[moka] = "moka"
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": moka}]})
    assert sc.check_sites("C1", [moka]) == [] and started == ["C1"]


def test_a_new_companys_sites_are_scanned_at_once_whoever_adds_them(live, data_file, monkeypatch):
    """2026-11-04: a company added with its careers site (or filled in by the AI) waited for a click on Scan before any
    of its roles came in. Every way a site arrives now checks it at once (check_sites scans what a scan can read)."""
    checked: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(live.inst.hunt.scanner, "check_sites", lambda cid, urls: checked.append((cid, list(urls))))
    monkeypatch.setattr(hunt_api, "fetch_logo", lambda url: (PNG, "image/png"))
    def wait(n: int) -> bool:   # the check runs on a thread of its own
        for _ in range(100):
            if len(checked) >= n:
                return True
            time.sleep(0.05)
        return False
    site = "https://careers.xinghe.example/campus"
    # the window's form, with a site: checked at once
    code, r = live.req("POST", "/api/hunt/companies", {"name": "鏄熸渤绉戞妧", "site": site})
    assert code == 200 and wait(1) and checked == [(r["company"]["id"], [site])]
    # without one there is nothing to check yet
    live.req("POST", "/api/hunt/companies", {"name": "Kestrel Labs"})
    time.sleep(0.3)
    assert len(checked) == 1
    # the AI's fill gives it its sites: only the new ones are checked
    intern = "https://jobs.kestrel.example/intern"
    fields = {"sites": [{"url": intern, "kind": "intern"}]}
    assert cli(data_file, "fill", "Kestrel Labs", "--stdin", stdin=json.dumps(fields))[0] == 0
    assert wait(2) and checked[1] == ("C2", [intern])
    # the AI's own add-company and edit-company
    assert cli(data_file, "add-company", "Acme Robotics", "--site", "https://acme.example/careers")[0] == 0
    assert wait(3) and checked[2] == ("C3", ["https://acme.example/careers"])
    # a fill that changes no site starts no check
    assert cli(data_file, "fill", "Kestrel Labs", "--stdin", stdin=json.dumps({"industry": "AI"}))[0] == 0
    time.sleep(0.3)
    assert len(checked) == 3


def test_postings_are_written_short_and_read_back_whole(hunt):
    """2026-11-04: 53,609 postings took 31 MB, indented, every empty field written, the link twice (as `key` too).
    One a line now, empty fields and a key that is the link left out; read back, every posting is whole again."""
    ps = Postings(hunt)
    hunt.add_company({"name": "阿里云"})
    ps.merge("C1", [{"title": "算法工程师", "url": "https://talent.example/job/1"},
                    {"title": "无链接岗", "location": "杭州", "deadline": "2030-01-01"}], how="alibaba")
    before = ps.all()
    raw = (hunt.dir / "postings.json").read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert lines[0] == '{"next":3,"postings":[' and lines[-1] == "]}" and len(lines) == 4   # one posting a line
    assert '"category"' not in raw and '"app"' not in raw and '"changed"' not in raw and ": " not in raw
    one, two = json.loads(lines[1].rstrip(",")), json.loads(lines[2])
    assert "key" not in one and one["url"] == "https://talent.example/job/1"            # its key is its link
    assert two["key"] == "t:无链接岗|杭州" and "url" not in two                          # a key that isn't one stays
    fresh = Postings(hunt).all()
    assert fresh == before and fresh[0]["key"] == fresh[0]["url"] and fresh[0]["changed"] == {} and fresh[0]["app"] is None
    # the next scan matches them as before: nothing new, nothing closed
    res = ps.merge("C1", [{"title": "算法工程师", "url": "https://talent.example/job/1"},
                          {"title": "无链接岗", "location": "杭州", "deadline": "2030-02-01"}], how="alibaba")
    assert (res["found"], len(res["new"]), len(res["closed"]), len(res["changed"])) == (2, 0, 0, 1)
    assert ps.get("P2")["changed"]["deadline"] == ["2030-01-01", "2030-02-01"]
    # a linked application survives the round trip
    a = ps.apply("P1")
    assert Postings(hunt).get("P1")["app"] == a["id"]


def test_an_older_postings_file_is_written_short_once(hunt):
    ps = Postings(hunt)
    hunt.add_company({"name": "快手"})
    old = {"next": 2, "postings": [{"id": "P1", "company": "C1", "key": "https://ks.example/j/1", "title": "后端", "url": "https://ks.example/j/1",
                                    "location": "", "category": "", "batch": "", "deadline": "", "posted": "2026-10-01",
                                    "first_seen": "2026-10-01T10:00:00", "last_seen": "2026-11-01T10:00:00", "closed": "",
                                    "changed": {}, "app": None, "source": "route"}]}
    (hunt.dir / "postings.json").write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    size = (hunt.dir / "postings.json").stat().st_size
    assert ps.all() == old["postings"]                                     # read as it is
    assert ps.slim_file() is True and (hunt.dir / "postings.json").stat().st_size < size * 0.6
    assert Postings(hunt).all() == old["postings"] and ps.all() == old["postings"]
    assert ps.slim_file() is False                                         # once
    (hunt.dir / "postings.json").unlink()
    assert ps.slim_file() is False and ps.all() == []                      # no file: nothing to do


def test_the_table_matches_each_title_once_and_counts_again_only_on_a_change(hunt, monkeypatch):
    """53,000 postings took 12 s a look at the table (2026-11-08): every keyword folded the title again, and every
    look counted them all again. A title is folded once per set of tracks; an unchanged count is kept."""
    import jobhunt.postings as P
    ps = Postings(hunt)
    hunt.add_company({"name": "网易"})
    ps.settings(tracks=[{"name": "AI", "keywords": ["AI", "算法", "Agent"]}])
    ps.merge("C1", [{"title": f"AI 工程师 {i}", "url": f"https://hr.163.example/{i}"} for i in range(30)]
             + [{"title": "前端开发", "url": "https://hr.163.example/fe"}], how="route")
    folds = []
    real = P._fold
    monkeypatch.setattr(P, "_fold", lambda x: folds.append(x) or real(x))
    P._TRACKED.clear()
    s = ps.summary()["C1"]
    assert (s["open"], s["matching"]) == (31, 30)
    assert folds.count("前端开发") == 1                                     # not once per keyword it misses
    folds.clear()
    again = ps.summary()["C1"]
    assert again["matching"] == 30 and not folds                          # nothing changed: nothing counted again
    again["needs"] = "claude"
    assert "needs" not in ps.summary()["C1"]                              # what the table adds stays the table's
    ps.settings(tracks=[{"name": "前端", "keywords": ["前端"]}])
    assert ps.summary()["C1"]["matching"] == 1                            # other tracks: counted again
    ps.merge("C1", [{"title": "前端开发", "url": "https://hr.163.example/fe"}, {"title": "前端实习", "url": "https://hr.163.example/fe2"}], how="route")
    s = ps.summary()["C1"]
    assert (s["open"], s["matching"]) == (2, 2)                           # a scan's write: counted again
    # the same answers as matching one title at a time
    assert P.tracks_of("AI 前端", ps.tracks()) == ["T1"] and P.tracks_of("后端", ps.tracks()) == []
    assert P.matches("AI 产品经理", ["AI+产品"]) and not P.matches("AI 应用工程师", ["AI+产品"])
    assert not P.matches("Maintenance", ["AI"]) and not P.matches("AI 工程师", ["AI"], ["工程师"]) and P.matches("x", [])
    assert not P.keyword_hits("AI 工程师", " + ")                           # a keyword with nothing in it hits nothing


def test_scans_are_parsed_once_per_change(hunt, monkeypatch):
    """The table asks for each company's scan state: 200 parses of a 700 KB scans.json a look (2026-11-08)."""
    ps = Postings(hunt)
    hunt.add_company({"name": "网易"})
    ps.set_scan_state("C1", ok=True)
    loads = []
    real = hunt._load
    monkeypatch.setattr(hunt, "_load", lambda name: loads.append(name) or real(name))
    for _ in range(20):
        ps.scan_state("C1"); ps.tracks(); ps.route("C1")
    assert loads.count("scans.json") <= 1
    ps.set_scan_state("C1", ok=False, error="timed out")
    assert ps.scan_state("C1")["error"] == "timed out"                     # a write here is seen at once
    f = hunt.dir / "scans.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    d["companies"]["C1"]["error"] = "edited elsewhere"
    time.sleep(0.02)
    f.write_text(json.dumps(d), encoding="utf-8")
    assert ps.scan_state("C1")["error"] == "edited elsewhere"              # so is one from outside
    ps.set_scan_state("C1", ok=True)                                       # a change starts from the file, not the kept copy
    assert ps.scan_state("C1")["error"] == "edited elsewhere" and ps.scan_state("C1")["ok"] is True


def test_postings_are_parsed_once_per_change_and_never_changed_by_a_reader(hunt, monkeypatch):
    ps = Postings(hunt)
    hunt.add_company({"name": "网易"})
    ps.merge("C1", [{"title": "游戏策划", "url": "https://hr.163.example/1"}], how="route")
    reads = []
    real = hunt._read
    monkeypatch.setattr(hunt, "_read", lambda name, key: reads.append(name) or real(name, key))
    ps.all(); ps.summary(); ps.changes(); ps.get("P1"); ps.by_url("https://hr.163.example/1")
    assert reads.count("postings.json") == 0                               # the merge kept what it wrote: no parse
    other = Postings(hunt)                                                 # another reader (the app started again)
    other.all(); other.summary(); other.changes(); other.get("P1")
    assert reads.count("postings.json") == 1                               # the table, the news, a page: one parse
    # what a reader gets is a copy
    got = ps.get("P1")
    got["title"] = "改掉了"; got["changed"]["x"] = 1
    ps.all()[0]["closed"] = "2030"
    assert ps.get("P1")["title"] == "游戏策划" and ps.get("P1")["changed"] == {} and ps.get("P1")["closed"] == ""
    # a write here is seen at once
    ps.merge("C1", [{"title": "游戏策划", "url": "https://hr.163.example/1"}, {"title": "美术", "url": "https://hr.163.example/2"}], how="route")
    assert len(ps.all()) == 2
    # so is one from outside (another editor, a restore): the file changed, so it is read again
    f = hunt.dir / "postings.json"
    text = f.read_text(encoding="utf-8").replace("美术", "美术设计")
    time.sleep(0.02)
    f.write_text(text, encoding="utf-8")
    assert [p["title"] for p in ps.all()] == ["游戏策划", "美术设计"]


def test_a_check_of_new_sites_waits_for_a_scan_of_the_same_company(hunt, monkeypatch):
    """A site the AI adds while the first scan of a new company runs: that scan read the old list, so the check
    waits for it instead of giving up (it used to return at once, and the new site was never read)."""
    import threading
    ps = Postings(hunt)
    hunt.add_company({"name": "Moka Co"})
    moka = "https://app.mokahr.example/campus-recruitment/moka/1"
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc._providers[moka] = "moka"
    started: list[str] = []
    sc.start = lambda ids, auto=False: started.extend(ids)
    sc.running["C1"] = "scan"                                   # a scan of it is going on
    hunt.update_company("C1", {"sites": [{"url": moka}]})
    t = threading.Thread(target=sc.check_sites, args=("C1", [moka]))
    t.start()
    time.sleep(0.4)
    assert t.is_alive() and started == []                      # waiting, not given up
    sc.running.pop("C1")
    t.join(5)
    assert not t.is_alive() and started == ["C1"]
    # it doesn't wait forever
    monkeypatch.setattr("jobhunt.scan.CHECK_WAIT", 0.3)
    sc.running["C1"] = "scan"
    assert sc.check_sites("C1", [moka]) == [] and started == ["C1"]


def test_a_site_gets_its_kind_only_from_someone_who_looked(hunt):
    """A pasted address is not 校招 because the company had no campus site yet (2026-11-03): its kind stays empty
    until the user picks one or the AI reads the site; the AI never changes a kind the user set."""
    hunt.add_company({"name": "星河科技", "sites": [{"url": "https://careers.xinghe.example/campus", "kind": "campus"}]})
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": "careers.xinghe.example/x/"},
                                         {"url": "https://hr.xinghe.example", "kind": "nonsense"}]})
    assert [s["kind"] for s in hunt.company("C1")["sites"]] == ["campus", "", ""]
    c, _ = hunt.fill("C1", {"sites": [{"url": "https://careers.xinghe.example/campus", "kind": "social"},
                                      {"url": "http://www.careers.xinghe.example/x", "kind": "intern", "system": "Moka"},
                                      {"url": "https://elsewhere.example", "kind": "campus"}]})
    assert [(s["url"], s["kind"], s["system"]) for s in c["sites"]] == [
        ("https://careers.xinghe.example/campus", "campus", ""),       # the user's kind stays
        ("https://careers.xinghe.example/x/", "intern", "Moka"),       # an empty one is the AI's to say
        ("https://hr.xinghe.example", "", "")]                         # and the user's list keeps its sites
    assert c["site"] == "https://careers.xinghe.example/campus"


def test_one_page_that_times_out_keeps_what_the_other_pages_read(hunt):
    """A slow site (长鑫's social list) timing out on one page of the route: the campus and intern pages it read
    still come in, nothing is closed for the page it couldn't read, and the scan says which page didn't."""
    from core.browser import BrowserError
    from core.cdp import CdpError
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    campus, intern, social = (f"https://careers.xinghe.example/{k}/jobs" for k in ("campus", "intern", "social"))

    class SlowSocial(FakeBrowser):
        def read_page(self, url, script, timeout=60.0):
            if url == social and self.slow:
                self.opened.append(url)
                raise BrowserError("Runtime.evaluate timed out")
            if url == intern and self.slow_to_open:
                raise CdpError("Page.navigate timed out")
            return super().read_page(url, script, timeout)
    browser = SlowSocial({campus: {"postings": [{"title": "存储芯片设计", "url": campus + "/1"}]},
                          intern: {"postings": [{"title": "芯片验证实习", "url": intern + "/1"}]},
                          social: {"postings": [{"title": "DRAM 工艺专家", "url": social + "/1"}]}})
    browser.slow = browser.slow_to_open = False
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.save_route("星河科技", {"pages": [{"url": u, "script": "async (h) => ({postings: []})"} for u in (campus, intern, social)]})
    assert sc.scan("星河科技")["found"] == 3
    browser.slow = True
    browser.pages[campus]["postings"].append({"title": "封装设计", "url": campus + "/2"})
    res = sc.scan("星河科技")
    assert (res["found"], res["new"], res["closed"]) == (3, 1, 0) and social in res["errors"][0]
    st = ps.scan_state("C1")
    assert st["ok"] and "Runtime.evaluate timed out" in st["error"] and st["how"] == "route"
    assert sorted(p["title"] for p in ps.all() if not p["closed"]) == ["DRAM 工艺专家", "存储芯片设计", "封装设计", "芯片验证实习"]
    # a page that doesn't even open in time is one page that didn't read too, never the end of the scan
    browser.slow_to_open = True
    res = sc.scan("星河科技")
    assert res["found"] == 2 and len(res["errors"]) == 2 and "Page.navigate timed out" in res["errors"][0]
    # every page timing out is still a scan that didn't work, saying why
    browser.pages = {}
    browser.read_page = lambda url, script, timeout=60.0: (_ for _ in ()).throw(BrowserError("Runtime.evaluate timed out"))
    res = sc.scan("星河科技")
    assert res["route_broken"] and "timed out" in res["error"] and not ps.scan_state("C1")["ok"]


def test_a_slow_network_gets_time_and_more_tries_before_a_page_counts_as_unread(hunt):
    """User, 2026-11-02: a student in Europe reading a company's site in China waits long for an answer, and 30 s
    then nothing more threw away 100 of 长鑫's roles. A scan gives a page 90 s to open, opens it twice more after
    a pause before giving up, and opens an address the route reads 17 times only once."""
    from core.browser import PageNotOpened
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    campus, social = "https://careers.xinghe.example/campus/jobs", "https://careers.xinghe.example/social/jobs"

    class FarAway(FakeBrowser):
        def read_pages(self, url, scripts, timeout=60.0, open_timeout=30.0):
            self.calls.append((url, len(scripts), open_timeout))
            if self.fails.get(url, 0) > 0:
                self.fails[url] -= 1
                return [PageNotOpened(f"{url} didn't open: Page.navigate timed out")] * len(scripts)
            return [{"postings": [{"title": f"{url.split('/')[3]} {s[-2:]}", "url": f"{url}/{s[-2:].strip()}"}]} for s in scripts]
    browser = FarAway({})
    browser.calls, browser.fails = [], {}
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.retry_waits = (0, 0)
    pages = [{"url": campus, "script": "() => 1"}] + [{"url": social, "script": f"() => {n}"} for n in range(2, 19)]
    assert not sc.save_route("星河科技", {"pages": pages})[1]
    browser.calls = []
    assert sc.scan("星河科技")["found"] == 18
    assert [(u, n) for u, n, _ in browser.calls] == [(campus, 1), (social, 17)] and {w for *_, w in browser.calls} == {90}
    # opening twice in a row too slowly is a slow network, not a page that didn't read
    browser.calls, browser.fails = [], {social: 2}
    res = sc.scan("星河科技")
    assert res["found"] == 18 and not res["errors"] and len(browser.calls) == 4
    st = ps.scan_state("C1")
    assert st["ok"] and not st["error"] and not st.get("unread")
    # three times: those pages didn't read, said once for the address, and how many of the route's pages they were
    browser.fails = {social: 3}
    res = sc.scan("星河科技")
    assert res["found"] == 1 and len(res["errors"]) == 1 and social in res["errors"][0] and "3" in res["errors"][0]
    st = ps.scan_state("C1")
    assert st["ok"] and st["unread"] == [17, 18] and len(ps.all()) == 18 and not any(p["closed"] for p in ps.all())
    # the next scan that reads them all clears it
    browser.fails = {}
    sc.scan("星河科技")
    assert not ps.scan_state("C1").get("unread") and not ps.scan_state("C1")["error"]


def test_postings_kept_without_a_link_are_the_same_ones_once_a_scan_gives_links(hunt):
    """长鑫's first route gave no links: the route recorded again gives them, and its 80 postings stay the same 80
    (their ids and applications kept), not 80 closed and 80 new."""
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技"})
    ps.merge("C1", [{"title": "FPGA开发(J20770)", "location": "安徽省·合肥市, 上海市"}, {"title": "IO设计(J20772)"},
                    # an old route read the kind of job as its place: matched by title alone, as the only one so named
                    {"title": "DRAM电路设计工程师(J21562)", "location": "全职"},
                    # two kept by one title: which one is which can't be told, so neither is taken
                    {"title": "工艺工程师", "location": "全职"}, {"title": "工艺工程师", "location": "兼职"}], how="route")
    old = {p["title"]: p["id"] for p in ps.all()}
    res = ps.merge("C1", [{"title": "FPGA开发(J20770)", "location": "安徽省·合肥市, 上海市", "url": "https://cxmt.example/campus/detail?jobAdId=1"},
                          {"title": "封装设计(J20773)", "url": "https://cxmt.example/campus/detail?jobAdId=3"},
                          {"title": "DRAM电路设计工程师(J21562)", "location": "安徽省·合肥市", "url": "https://cxmt.example/social/detail?jobAdId=4"},
                          {"title": "工艺工程师", "location": "上海市", "url": "https://cxmt.example/social/detail?jobAdId=5"}], how="route")
    assert sorted(p["title"] for p in res["new"]) == ["封装设计(J20773)", "工艺工程师"]
    assert sorted(p["title"] for p in res["closed"]) == ["IO设计(J20772)", "工艺工程师", "工艺工程师"]
    dram = next(p for p in ps.all() if p["title"] == "DRAM电路设计工程师(J21562)")
    assert dram["id"] == old["DRAM电路设计工程师(J21562)"] and dram["location"] == "安徽省·合肥市" and not dram["closed"]
    fpga = next(p for p in ps.all() if p["title"] == "FPGA开发(J20770)")
    assert fpga["id"] == old["FPGA开发(J20770)"] and fpga["url"].endswith("jobAdId=1") and not fpga["closed"]
    assert ps.merge("C1", [{"title": "FPGA开发(J20770)", "location": "安徽省·合肥市, 上海市", "url": "https://cxmt.example/campus/detail?jobAdId=1"},
                           {"title": "封装设计(J20773)", "url": "https://cxmt.example/campus/detail?jobAdId=3"}], how="route")["new"] == []


def test_a_second_scan_of_a_company_still_being_read_says_for_how_long(hunt):
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc.running["C1"], sc.started["C1"] = "route", time.time() - 95
    res = sc.scan("星河科技")
    assert res["busy"] and 94 <= res["since"] <= 97


def test_a_scan_asked_for_while_one_runs_answers_with_that_scans_result(hunt):
    """货拉拉 (2026-11-10): the AI's scan found the window's scan running, was told "still being scanned, wait", and
    ended its turn there; the scan then said a site couldn't be read, after the work had been judged done. The
    command now waits for the running scan and answers with what it found."""
    import threading
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc.running["C1"], sc.started["C1"] = "scan", time.time()

    def end():
        time.sleep(1.2)
        ps.set_scan_state("C1", ok=True, error="", found=56, new=["P1", "P2"], closed=[], changed=[],
                          needs="claude", unrouted=["https://careers.xinghe.example/campus"])
        with sc.lock:
            sc.running.pop("C1")
    threading.Thread(target=end).start()
    t0 = time.time()
    res = sc.wait_scan("C1")
    assert time.time() - t0 >= 1
    assert res == {"company": "C1", "found": 56, "new": 2, "closed": 0, "changed": 0, "needs": "claude",
                   "unrouted": ["https://careers.xinghe.example/campus"], "short": None}
    sc.running["C1"] = "scan"                                  # one that never ends still answers, as busy
    assert sc.wait_scan("C1", timeout=0.5)["busy"]
    sc.running.pop("C1")


def test_a_scan_holds_the_browser_while_it_reads_and_says_what_it_found(hunt):
    """The browser closes itself when nothing holds it (core/browser.py close_if_idle): a scan holds it from the first
    company to the last, so it is never closed between them, and lets go when it is done."""
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    page = "https://careers.xinghe.example/campus/jobs"
    browser = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1"}]}}, off=True)
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.save_route("星河科技", {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]})
    browser.opened, browser.held_while_reading = [], []
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


def test_a_run_shows_how_far_it_got_from_the_click_to_the_end(hunt, monkeypatch):
    """The window's Scan buttons spin for the whole run: it is there before the first company is read, between one
    company and the next, and a second start while it goes on joins its queue instead of a run of its own."""
    import jobhunt.scan as scan_mod
    ps = Postings(hunt)
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    hunt.add_company({"name": "示例基金"})
    page = "https://careers.xinghe.example/campus/jobs"
    browser = FakeBrowser({page: {"postings": [{"title": "服务端研发工程师", "url": page + "/1"}]}})
    seen = []
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: seen.append((dict(sc.batch) if sc.batch else None, list(sc.queue), dict(sc.running))))
    sc.save_route("星河科技", {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]})
    threads = []
    monkeypatch.setattr(scan_mod.threading, "Thread", lambda target, **kw: threads.append(target) or type("T", (), {"start": lambda self: None})())
    sc.start(["C1"])
    assert seen[-1][:2] == ({"total": 1, "done": 0, "auto": False}, ["C1"])     # at once, before anything is read
    sc.start(["C2", "C1"])                                                        # C1 is queued already
    assert len(threads) == 1 and sc.queue == ["C1", "C2"] and sc.batch["total"] == 2
    threads[0]()
    between = [s for s in seen if s[0] and not s[2]]                              # no company being read just then
    assert any(s[0]["done"] == 1 for s in between)                                # still a run, one of two done
    assert seen[-1] == (None, [], {}) and sc.last_run["companies"] == 2
    sc.start([])                                                                  # nothing to scan: no run at all
    assert sc.batch is None and len(threads) == 1


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")


@needs_node
def test_a_moka_campus_apply_list_is_read_through_its_api(hunt, tmp_path):
    """思摩尔国际 (2026-11-04): its campus list is at app.mokahr.com/campus_apply/smoore/150918, a spelling career-ops'
    Moka provider doesn't know (it knows /campus-recruitment/...), so the site read as "no scan can read it" though
    its 53 roles come through the same API. The bridge hands the provider the spelling it knows."""
    import subprocess
    co = tmp_path / "career-ops"
    (co / "providers").mkdir(parents=True)
    (co / "providers" / "_registry.mjs").write_text("""
export async function loadProviders() { return new Map([['mokahr', (await import('./mokahr.mjs')).default]]); }
export function resolveProvider(entry, providers) { const p = providers.get('mokahr'); return p.detect(entry) ? { provider: p } : null; }
""", encoding="utf-8")
    (co / "providers" / "_http.mjs").write_text("export function makeHttpCtx() { return {}; }\n", encoding="utf-8")
    (co / "providers" / "mokahr.mjs").write_text(r"""
const RE = /^\/(?:social-recruitment|campus-recruitment|apply)\/([^/]+)\/(\d+)\/?$/;   // career-ops' own
export default { id: 'mokahr', detect: (e) => { try { const u = new URL(e.careers_url);
    return u.hostname === 'app.mokahr.com' && RE.test(u.pathname); } catch { return false; } },
  async fetch(entry) { return [{ title: '产品专员', url: entry.careers_url + '#/job/1' }]; } };
""", encoding="utf-8")
    entries = tmp_path / "entries.json"
    sites = ["https://app.mokahr.com/campus_apply/smoore/150918#/jobs", "https://app.mokahr.com/social_apply/smoore/1/",
             "https://app.mokahr.com/apply/smoore/126055", "https://example.com/campus_apply/a/1"]
    entries.write_text(json.dumps({"entries": [{"key": str(i), "name": "思摩尔国际", "url": u} for i, u in enumerate(sites)]}), encoding="utf-8")
    bridge = Path(hunt_api.__file__).resolve().parent.parent / "jobs" / "co_scan.mjs"
    out = subprocess.run([shutil.which("node"), str(bridge), str(co), str(entries)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    got = {r["url"]: r for r in json.loads(out.stdout.strip().splitlines()[-1])["results"]}
    assert got[sites[0]]["provider"] == "mokahr"                                # keyed by the site as the company has it
    assert got[sites[0]]["jobs"][0]["url"] == "https://app.mokahr.com/campus-recruitment/smoore/150918#/job/1"
    assert got[sites[1]]["provider"] == "mokahr"                                # its social twin too
    assert got[sites[2]]["jobs"][0]["url"].startswith("https://app.mokahr.com/apply/smoore/126055")   # the known spelling as it was
    assert got[sites[3]]["provider"] is None                                    # only Moka's own addresses


def test_a_site_the_ai_tried_to_learn_says_so_until_a_scan_reads_it(live, data_file):
    """2026-11-04: after the AI was asked to learn a site and the scan still couldn't read it, every place still said
    "the AI has to learn it first", as if nothing had been tried. The window's check keeps it as tried."""
    sc = live.inst.hunt.scanner
    hunt = live.inst.hunt
    a, b = "https://careers.smoore.example/campus", "https://www.smoore.example/social"
    hunt.add_company({"name": "思摩尔国际", "sites": [{"url": a}, {"url": b}]})
    sc.postings.set_scan_state("C1", needs="claude", unrouted=[a, b], at="2026-11-04T10:00:00", ok=True)
    code, v = live.req("POST", "/api/hunt/companies/C1/learn-tried", {"sites": [a, "https://elsewhere.example"]})
    assert code == 200 and list(v["scan"]["tried"]) == [a]                       # only a site the scan can't read
    row = next(c for c in live.req("GET", "/api/hunt")[1]["companies"] if c["id"] == "C1")
    assert list(row["postings"]["tried"]) == [a] and row["postings"]["unrouted"] == [a, b]
    # once a scan reads it, it is neither unreadable nor tried any more
    sc.postings.set_scan_state("C1", unrouted=[b])
    assert live.req("GET", "/api/hunt/companies/C1")[1]["scan"]["tried"] == {}
    # a site taken off the company takes its mark with it
    sc.postings.set_scan_state("C1", unrouted=[a, b])
    hunt.update_company("C1", {"sites": [{"url": b}]})
    assert live.req("GET", "/api/hunt/companies/C1")[1]["scan"]["tried"] == {}


def test_a_site_from_the_form_is_on_the_list_and_removing_the_first_site_removes_it(hunt):
    """The "Add a company" form's site was kept only as `site`, never on the list the window shows; and a company's
    first site taken off the list stayed as `site`, so the scan went on reading it and warning about it."""
    sc = Scanner(hunt, Postings(hunt), NoTool(), None, lambda: None)
    c = hunt.add_company({"name": "思摩尔国际", "site": "careers.smoore.example/campus"})
    assert [s["url"] for s in c["sites"]] == ["https://careers.smoore.example/campus"] and c["site"] == c["sites"][0]["url"]
    c = hunt.update_company("C1", {"sites": [*c["sites"], {"url": "https://www.smoore.example/social"}]})
    assert [s["url"] for s in c["sites"]] == ["https://careers.smoore.example/campus", "https://www.smoore.example/social"]
    c = hunt.update_company("C1", {"sites": [{"url": "https://www.smoore.example/social"}]})   # the first one taken off
    assert c["site"] == "https://www.smoore.example/social"
    assert [s["url"] for s in sc.sites_of(c)] == ["https://www.smoore.example/social"]
    c = hunt.update_company("C1", {"sites": []})
    assert c["site"] == "" and sc.sites_of(c) == []


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
                                            {"title": "算法工程师", "url": page + "/2", "deadline": "2030-10-31"}]},
                        page + "/2": {"jd": "岗位职责\n1、训练模型", "text": "工作地点\n工作地点\n杭州/上海\n截止日期：2026-11-30"}})
    monkeypatch.setattr(live.inst.hunt.scanner, "browser", fake)
    code, out = cli(data_file, "scan")
    assert "C1: needs claude" in out
    route = {"pages": [{"url": page, "script": "async (h) => ({postings: []})"}]}
    code, out = cli(data_file, "scan-route", "星河科技", "--stdin", stdin=json.dumps(route))
    assert code == 0 and "C1: 2 open, 2 new, 0 closed, 0 changed" in out and "P2" in out and "closes 2030-10-31" in out
    # a route that never says how many roles the site lists can't be checked: the AI is told to add it
    assert "not checked: no page of the route says how many roles" in out
    tab = live.req("GET", "/api/hunt")[1]
    c = tab["companies"][0]
    assert c["postings"]["open"] == 2 and c["postings"]["news"]["new"] == 2 and c["postings"]["needs"] == ""
    assert [len(x["new"]) for x in tab["changes"]] == [2]
    v = live.req("GET", "/api/hunt/companies/C1")[1]
    assert len(v["postings"]) == 2 and v["scan"]["recorded"] and v["scan"]["how"] == "route"
    code, r = live.req("POST", "/api/hunt/postings/P2/apply")
    assert code == 200 and r["application"]["role"] == "算法工程师" and r["application"]["schedule"][0]["at"] == "2030-10-31"
    # going for it reads its page: the description and the cities; the scanned deadline stays
    aid = r["application"]["id"]
    end = time.time() + 10
    while time.time() < end and (r["reading"].get("running") or not r["application"]["jd"]):
        time.sleep(0.05)
        r = live.req("GET", f"/api/hunt/apps/{aid}")[1]
    a = r["application"]
    assert a["jd"] == "岗位职责\n1、训练模型" and [p["area"] for p in a["places"]] == ["Hangzhou", "Shanghai"] and a["jd_read"]
    assert a["schedule"][0]["at"] == "2030-10-31" and not r["reading"]
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
    # the AI reads it instead (docs/AI_BUTTON_CHECKS.md): the role has its JD, so "its page didn't read" is over
    code, out = cli(data_file, "jd", "星河科技/算法工程师", "--stdin", stdin="岗位职责\n3、AI 读到的")
    assert code == 0, out
    r = live.req("GET", f"/api/hunt/apps/{aid}")[1]
    assert r["application"]["jd"] == "岗位职责\n3、AI 读到的" and r["reading"] == {}
    code, out = cli(data_file, "keywords", "后端, 服务端")
    assert "keywords: 后端, 服务端" in out
    # tracks: the AI sets the whole list, and reads it back
    code, out = cli(data_file, "tracks", "--stdin", stdin=json.dumps({"tracks": [
        {"name": "后端", "keywords": ["后端", "服务端"], "exclude": ["测试"]}, {"name": "AI 产品", "keywords": ["AI+产品"]}]}))
    assert code == 0 and "T1 后端: 后端, 服务端; not: 测试" in out and "T2 AI 产品: AI+产品" in out, out
    code, out = cli(data_file, "tracks")
    assert "T2 AI 产品" in out
    assert [t["name"] for t in live.req("GET", "/api/hunt/matches")[1]["tracks"]] == ["后端", "AI 产品"]
    # one that says, and reads fewer: never kept, and the AI is told why in so many words (鹏芯微, 2026-11-05)
    fake.pages[page]["total"] = 9
    short = {"pages": [{"url": page, "script": "async (h) => ({total: 9, postings: []})"}]}
    code, out = cli(data_file, "scan-route", "星河科技", "--stdin", stdin=json.dumps(short))
    assert code != 0 and "read 2 different roles and the site says it lists 9. Nothing was kept" in out, out
    assert live.inst.hunt.scanner.postings.route("C1")["pages"] == route["pages"]   # the route it had stays
    del fake.pages[page]["total"]
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
    # learning a site ends with a route; reading postings by hand is only for a route that failed, said why
    assert "done only once `scan-route` has run" in note and "always gets a route" in note
    assert "only after a route really failed" in note and "When no script can do it" not in note
    assert "posted in the last 12 months" in note
    assert str(prompts.OPENINGS) in note and prompts.OPENINGS.is_file()       # where to find employers worth adding
    assert str(prompts.RISKS) in note and prompts.RISKS.is_file()             # the traps in a posting, a company, an offer


def test_a_logo_a_site_wont_hand_a_script_comes_through_the_browser(live, data_file, monkeypatch):
    # 2026-11-03: 九牧's site closed the connection on the download, and the AI gave up on its logo
    import base64

    class Browser:
        def __init__(self):
            self.read = []

        def read_page(self, url, script, timeout=60):
            self.read.append(url)
            return {"type": "image/png", "b64": base64.b64encode(PNG).decode()}

    b = Browser()
    monkeypatch.setattr(live.inst.hunt.scanner, "browser", b)
    cli(data_file, "add-company", "九牧集团")
    url = "http://127.0.0.1:9/logo.png"   # nothing listens: the script's download fails
    code, out = cli(data_file, "fill", "九牧集团", "--stdin", stdin=json.dumps({"logo_url": url}))
    assert code == 0 and b.read == [url] and "logo not saved" not in out
    assert live.inst.hunt.logo_path("九牧集团").read_bytes() == PNG
    # when the browser can't either, the AI is told where else to look rather than to give up
    b.read_page = lambda url, script, timeout=60: {"error": "HTTP 403"}
    code, out = cli(data_file, "fill", "九牧集团", "--stdin", stdin=json.dumps({"logo_url": url}))
    assert "logo not saved" in out and "Wikimedia" in out and "only after several" in out


# ---------------------------------------------------------------- a read that stops short (2026-11-04)
def test_roles_sharing_the_companys_number_in_their_links_are_all_kept(hunt):
    """2026-11-04: Hotjob puts the company's tenant id (/SU12345678…/) in every posting's address, so same_once took
    every title that came twice (a trainee role in two cities) for one role read twice: 448 read, 381 kept."""
    from jobhunt.scan import same_once
    base = "https://wecruit.hotjob.example/SU12345678abcdef00000000/pb/posDetail.html?postId={}&postType=campus"
    rows = [{"title": f"岗位 {i % 30}", "url": base.format(f"6a8e{i:020x}")} for i in range(60)]
    assert len(same_once(rows)) == 60
    # the same role under two addresses of one board is still kept once
    feishu = [{"title": "A", "url": "https://x.example/index/position/12345678901/detail"},
              {"title": "A", "url": "https://x.example/xinghe/position/12345678901/detail"}]
    assert len(same_once(feishu)) == 1


def test_a_route_that_reads_fewer_roles_than_its_site_lists_is_not_done(hunt):
    """A cheaper model's route stopped early and only the AI's own last line said so (381 of 448). The site's
    own count (`total`) is checked on every scan: short is said, and the roles not read are never closed."""
    ps = Postings(hunt)
    url = "https://careers.example.com/campus/jobs"
    hunt.add_company({"name": "Example Corp", "site": url})
    rows = [{"title": f"岗位 {i}", "url": f"{url}/{i}"} for i in range(100)]
    page = {"total": 100, "postings": rows}
    browser = FakeBrowser({url: page})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc._providers[url] = None
    sc.save_route("C1", {"pages": [{"url": url, "script": "async (h) => ({total: 100, postings: []})"}]})
    res = sc.scan("C1")
    assert res["found"] == 100 and not res["short"] and not ps.scan_state("C1")["short"]
    # next time the route stops at 70: short is said, and the 30 it didn't read stay open
    page["postings"] = rows[:70]
    res = sc.scan("C1")
    assert res["short"] == [70, 100] and res["closed"] == 0
    assert ps.scan_state("C1")["short"] == [70, 100] and ps.summary()["C1"]["scan"]["short"] == [70, 100]
    assert sum(1 for p in ps.all() if not p["closed"]) == 100
    # a role or two taken down between pages is no gap
    page["postings"] = rows[:99]
    assert not sc.scan("C1")["short"] and not ps.scan_state("C1")["short"]
    # one list split over three route pages: each says the whole list's count, which is counted once
    page.update(total=100, postings=rows)
    pages = [{"url": url, "script": f"async (h) => ({{total: 100, part: {i}}})"} for i in range(3)]
    sc.save_route("C1", {"pages": pages})
    parts = iter([{"total": 100, "postings": rows[:40]}, {"total": 100, "postings": rows[40:80]}, {"total": 100, "postings": rows[80:]}])
    browser.read_page = lambda u, s, timeout=60.0: next(parts)
    assert sc.scan("C1")["short"] is None


def test_tracks_pick_the_roles_for_each_kind_of_role(hunt):
    """2026-11-04: scanned roles weren't narrowed to the ones they'd want, and they go for more than one kind
    of role (an engineering and a product role). Tracks: keywords per kind, matched like career-ops' title filter."""
    from jobhunt.postings import keyword_hits
    assert keyword_hits("AI全栈工程师", "AI") and keyword_hits("Applied AI Engineer", "ai")
    assert not keyword_hits("Maintenance Engineer", "AI") and not keyword_hits("Detail Designer", "AI")
    assert keyword_hits("AI 产品经理", "AI+产品") and not keyword_hits("AI 应用工程师", "AI+产品")
    assert keyword_hits("Développeur", "developpeur")
    ps = Postings(hunt)
    # the one list of keywords there was before becomes the first track
    hunt._write("scans.json", {"companies": {}, "keywords": ["算法"], "daily": True, "last_all": ""})
    assert [(t["id"], t["keywords"]) for t in ps.tracks()] == [("T1", ["算法"])]
    hunt.add_company({"name": "星河科技"})
    titles = ["AI 应用工程师", "大模型算法工程师", "AI 产品经理", "产品经理（电商）", "AI 售前工程师", "财务专员", "Maintenance Engineer"]
    ps.merge("C1", [{"title": x, "url": f"https://x.example/{i}"} for i, x in enumerate(titles)], how="route")
    r = ps.settings(tracks=[{"name": "AI 应用工程师", "keywords": "AI, 大模型, Agent", "exclude": "售前, 产品", "subset": "SUB-001"},
                            {"name": "AI 产品经理", "keywords": ["AI+产品"]}, {"name": "", "keywords": ""}])
    assert [t["id"] for t in r["tracks"]] == ["T1", "T2"] and r["tracks"][0]["subset"] == "SUB-001"
    s = ps.summary()["C1"]
    assert s["open"] == 7 and s["matching"] == 3 and s["tracks"] == {"T1": 2, "T2": 1}
    m = ps.matched()
    assert m["total"] == 3 and m["counts"] == {"T1": 2, "T2": 1}
    assert {p["title"]: p["tracks"] for p in m["postings"]} == {"AI 应用工程师": ["T1"], "大模型算法工程师": ["T1"], "AI 产品经理": ["T2"]}
    assert all(p["company_name"] == "星河科技" for p in m["postings"])
    # no track has keywords: every title counts, and nothing is "picked"
    ps.settings(tracks=[])
    assert ps.summary()["C1"]["matching"] == 7 and ps.matched()["total"] == 0
    # the old `keywords` command still sets the first track
    ps.settings(keywords="Agent, 算法")
    assert [t["keywords"] for t in ps.tracks()] == [["Agent", "算法"]]


# ---------------------------------------------------------------- picked roles ranked by fit (2026-11-04)
def test_a_role_link_is_its_key_and_only_a_boards_two_sections_make_one_role(hunt):
    """A link is a role's key. Two links count as one role only when they differ in their first path segment alone
    (a Feishu board's /index/ and /<site>/ sections) and carry the same title."""
    from jobhunt.scan import same_once
    rows = [{"title": "A", "url": "https://x.example/index/position/1/detail"},
            {"title": "A", "url": "https://x.example/campus/position/1/detail"},     # same role, other section
            {"title": "A", "url": "https://x.example/index/position/2/detail"},      # same title, other role
            {"title": "B", "url": "https://x.example/campus/position/1/detail?x=1"},  # other query: other role
            {"title": "A", "url": "https://y.example/index/position/1/detail"}]      # other site
    assert len(same_once(rows)) == 4


def test_picked_roles_get_their_descriptions_read_and_ranked_by_their_tracks_resume(server):
    """Tracks filter by title; a title can't say whether the role suits the user. Picked roles' pages are read in
    the background and each description's skills are compared with the resume its track goes out with."""
    import time as _t
    hunt = server.inst.hunt
    posts = hunt.scanner.postings
    hunt.add_company({"name": "星河科技"})
    base = "https://careers.example.com/job/"
    posts.merge("C1", [{"title": "机器学习工程师", "url": base + "1"}, {"title": "前端工程师", "url": base + "2"},
                       {"title": "AI 研究员", "url": base + "3"}, {"title": "财务专员", "url": base + "4"}], how="route")
    posts.settings(tracks=[{"name": "ML", "keywords": "机器学习, 前端, AI", "subset": "RES-TECH-EN"}])
    jds = {base + "1": "职位描述 岗位职责 熟悉 Python 和 PyTorch，了解 scikit-learn，有深度学习经验 任职要求 本科以上",
           base + "2": "岗位职责 负责 Vue、Angular、Webpack 和 Flutter 开发 任职要求 熟悉 Kotlin, Swift",
           base + "3": ""}

    class Pages:
        def read_page(self, url, script, timeout=60.0):
            if url == base + "3":
                raise RuntimeError("the page didn't open")
            return {"jd": jds[url], "text": "截止日期：2026-12-01" if url == base + "1" else ""}
    hunt.scanner.browser = Pages()
    hunt.descriptions.pause = 0
    m = server.req("GET", "/api/hunt/matches")[1]
    assert m["total"] == 3 and m["unread"] == 3 and m["ranked"] and all(p["fit"] is None for p in m["postings"])
    r = server.req("POST", "/api/hunt/matches/read")[1]
    assert r["started"] == 3
    end = _t.time() + 10
    while hunt.descriptions.status() and _t.time() < end:
        _t.sleep(0.05)
    m = server.req("GET", "/api/hunt/matches")[1]
    by = {p["title"]: p for p in m["postings"]}
    ml, fe, ai = by["机器学习工程师"], by["前端工程师"], by["AI 研究员"]
    assert ml["jd"] == "read" and ml["deadline"] == "2026-12-01"
    assert ml["fit"]["score"] > fe["fit"]["score"] and {"python", "pytorch", "scikit-learn"} <= set(ml["fit"]["hit"])
    assert "flutter" in fe["fit"]["miss"] and ml["fit"]["subset"] == "RES-TECH-EN"
    assert ai["jd"] == "failed" and ai["fit"] is None and m["unread"] == 0
    # a failed page isn't read again the same day; a read one never again
    assert server.req("POST", "/api/hunt/matches/read")[1]["started"] == 0
    # a track with no resume: nothing to rank against
    posts.settings(tracks=[{"name": "ML", "keywords": "机器学习, 前端, AI"}])
    m = server.req("GET", "/api/hunt/matches")[1]
    assert not m["ranked"] and all(p["fit"] is None for p in m["postings"])


def test_a_scan_never_opens_role_pages_and_want_to_apply_reads_one_and_ranks_it(server):
    """User, 2026-11-07: scanning one company opened other companies' role pages in the browser. A scan only reads
    job lists; a role's page is read when the user clicks Want to apply, and its fit comes from that description."""
    import time as _t
    hunt = server.inst.hunt
    posts = hunt.scanner.postings
    hunt.add_company({"name": "星河科技"})
    hunt.add_company({"name": "万象科技"})
    base = "https://careers.example.com/job/"
    posts.merge("C1", [{"title": "机器学习工程师", "url": base + "1"}, {"title": "AI 研究员", "url": base + "2"}], how="route")
    posts.settings(tracks=[{"name": "ML", "keywords": "机器学习, AI", "subset": "RES-TECH-EN"}])
    opened = []

    class Pages:
        def read_page(self, url, script, timeout=60.0):
            opened.append(url)
            return {"jd": "岗位职责 熟悉 Python 和 PyTorch，了解 scikit-learn 任职要求", "text": ""}

        def status(self):
            return {"state": "running"}
    hunt.scanner.browser = Pages()
    hunt.descriptions.pause = 0
    hunt.scanner.plan = lambda c: []          # the other company's scan reads nothing: no site
    hunt.scanner.start(["C2"])                # the user scans 万象科技 with the browser open
    end = _t.time() + 10
    while (hunt.scanner.batch or hunt.descriptions.status()) and _t.time() < end:
        _t.sleep(0.05)
    _t.sleep(0.2)
    assert opened == [] and hunt.descriptions.status() is None
    assert server.req("GET", "/api/hunt/matches")[1]["unread"] == 2
    # Want to apply reads that one role's page, and the picked list ranks it by that description
    r = server.req("POST", "/api/hunt/postings/P1/apply")[1]
    aid = r["application"]["id"]
    end = _t.time() + 10
    while not server.req("GET", f"/api/hunt/apps/{aid}")[1]["application"]["jd"] and _t.time() < end:
        _t.sleep(0.05)
    assert opened == [base + "1"]
    by = {p["title"]: p for p in server.req("GET", "/api/hunt/matches")[1]["postings"]}
    assert by["机器学习工程师"]["jd"] == "read" and {"python", "pytorch"} <= set(by["机器学习工程师"]["fit"]["hit"])
    assert by["AI 研究员"]["fit"] is None and by["AI 研究员"]["jd"] == ""


def test_a_description_counts_only_the_skills_it_names():
    from jobhunt.rank import fit, skill_words
    assert {"python", "c++", "rag", "机器学习"} <= skill_words("Strong Python and C++ skills, RAG pipelines, 机器学习")
    assert not skill_words("We are a great team with strong communication and the ability to work across teams")
    assert fit("about our company", {"python"}) is None
    f = fit("Python, PyTorch, Docker, Kubernetes", {"python", "pytorch"})
    assert f["score"] == 50 and f["miss"] == ["docker", "kubernetes"]
