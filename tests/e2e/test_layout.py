"""Layout checks: every screen and every state of it, at several window sizes and languages, looked at
by tests/e2e/layout_check.js for squeezed text, labels broken over two lines, content cut off or sticking
out, panes scrolling sideways and controls on top of each other. See TESTING.md, "Layout checks".

The states are a catalogue (STATES below). Each one brings something that only shows in some situations
(a notice, a banner, a warning, a conflict, an empty list) onto the screen, names the selectors that prove
it is there, and is then checked like everything else. test_every_notice_is_in_the_catalogue fails when the
web code gains such a piece that no state shows.

Run:  .venv/Scripts/python -m pytest tests/e2e/test_layout.py
Each finding saves a screenshot of the window as it was to tests/artifacts/layout/.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from conftest import ROOT
from test_ui import FILL_NOW, add_long_bullet, choose, connected, open_entry, open_facts, open_job_search, state, wait_until  # noqa: F401

pytestmark = pytest.mark.e2e
WEB = ROOT / "web"
SHOTS = ROOT / "tests" / "artifacts" / "layout"
CHECK = (Path(__file__).resolve().parent / "layout_check.js").read_text(encoding="utf-8")

# The window sizes and interface languages every state is looked at in. German has the longest words,
# Chinese the narrowest lines; 1280 is a laptop, 1920 a desktop.
# 800 is half a screen (the window snapped beside another): the narrow layout, the chat floating closed.
LOOKS = [((1280, 800), "zh"), ((1440, 900), "zh"), ((800, 860), "zh"), ((1280, 800), "en"), ((1920, 1080), "en"),
         ((1280, 800), "de")]

# Meant to break a rule, with the reason. Keep this short: each line is a place the checker can't see.
SKIP = {
    "#pages": "the preview is pictures of pages; it zooms and scrolls on purpose",
    "#jobspace iframe": "career-ops' own interface, not ours",
}


# ---------------------------------------------------------------- the window
@pytest.fixture(scope="session")
def layout_ctx():
    from playwright.sync_api import sync_playwright

    profile = ROOT / ".cache" / "pw-profile-layout"     # not test_ui's: both contexts live for the whole run
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), channel="msedge", headless=True,
                                                   viewport={"width": 1440, "height": 900})
        yield ctx
        ctx.close()


@pytest.fixture
def win(layout_ctx, server):
    # looked at as in the window (scripts/_titlebar.py): the header's right end belongs to the window's buttons,
    # so the row has 138px less than in a browser tab
    server.inst.titlebar["on"] = True
    pg = layout_ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto(server.url)
    pg.evaluate("localStorage.clear()")
    yield pg
    pg.close()
    assert not errors, errors


def open_window(pg, server, lang):
    server.req("PUT", "/api/prefs", {"ui_lang": lang})
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.evaluate("localStorage.clear()")               # the last state's panes and workspace are not this one's
    pg.reload()
    pg.wait_for_selector("body[data-ws=resume] #middle")
    pg.wait_for_function("() => typeof S !== 'undefined' && S")


def settle(pg):
    """Let the layout finish: two frames, then whatever transition is running."""
    pg.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
    pg.wait_for_timeout(250)


def look(pg, where):
    settle(pg)
    found = pg.evaluate(CHECK, {"skip": list(SKIP)})
    lines = [f"{where}: {f['kind']}: {f['el']} — {f['detail']}" for f in found]
    if found:
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / (re.sub(r"[^\w.-]+", "_", where) + ".png")))
        with (SHOTS / "findings.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return lines


# ---------------------------------------------------------------- the catalogue
@dataclass
class Ctx:
    server: object
    data: dict = field(default_factory=dict)


@dataclass
class State:
    name: str
    show: Callable                        # (pg, ctx): brings it on screen; runs again after each language's reload
    covers: list[str]                     # selectors that prove it is on screen
    prepare: Callable | None = None       # (ctx): data changes, made once
    needs: tuple = ()                     # fixtures it needs set up before the server starts ("connected")


def nothing(pg, c):
    pass


def js(code):
    return lambda pg, c: pg.evaluate(code)


def then(*steps):
    def run(pg, c):
        for s in steps:
            s(pg, c)
    return run


# a wait ends as soon as the thing shows, so its limit costs nothing when all is well; 60 s, because on a busy
# machine (groups side by side, a game running) a render or a conflict took more than 20 (2026-11-10)
WAIT_MS = 60000


def wait(sel):
    return lambda pg, c: pg.wait_for_selector(sel, timeout=WAIT_MS)


def click(sel):
    return lambda pg, c: pg.click(sel)


def key(k):
    return lambda pg, c: pg.keyboard.press(k)


def say(text):
    def run(pg, c):
        pg.wait_for_function("() => ui.chat.available")
        pg.fill("#chat-input", text)
        pg.keyboard.press("Enter")
    return run


# -- Resume
def long_entry(c):
    s = state(c.server)
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    e["title"] = {"en": "Multimodal retrieval-augmented generation platform for enterprise knowledge bases",
                  "zh": "面向企业知识库的多模态检索增强生成平台（含评测与推理服务）"}
    e["subtitle"] = {"en": "Founding full-stack engineer, later technical lead for the inference and evaluation teams",
                     "zh": "创始全栈工程师，后任推理与评测团队技术负责人"}
    e["location"] = "Harbour City, New South Wales"
    c.server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})


def evidence(states: dict):
    """The project-folder state the library shows for some entries (normally from git; set here)."""
    # the window's own first check would overwrite these: wait until it has run
    return js(f"""async () => {{ const st = {json.dumps(states)};
        while (!ui.started) await new Promise(r => setTimeout(r, 20));
        if (evidenceBusy) await evidenceBusy;
        for (const [id, s] of Object.entries(st)) ui.evidence[id] = {{state: s, paths: [{{path: 'E:/Forge/demo', state: s}}]}};
        renderLibrary(); }}""")


def conflict(pg, c):
    open_entry(pg, "EXP-002")
    pg.type("#active input[data-key=location]", "Harbour City")        # unsaved here...
    c.data["n"] = c.data.get("n", 0) + 1
    f = Path(c.server.inst.studio.store.path)
    f.write_text(re.sub(r"Solo developer|Changed elsewhere \d+", f"Changed elsewhere {c.data['n']}", f.read_text(encoding="utf-8")),
                 encoding="utf-8")                                    # ...while the same entry changes on disk


def empty_library(c):
    for e in state(c.server)["doc"]["entries"]:
        c.server.req("DELETE", f"/api/entries/{e['id']}")


def broken_subset(c):
    """A subset listing entries the library doesn't have: the library warns about each."""
    v = next(x for x in state(c.server)["doc"]["subsets"] if x["id"] == "RES-TECH-EN")
    v["entries"] = [*v.get("entries", []), "EXP-099-A-VERY-LONG-ENTRY-ID-THAT-NOBODY-SHORTENED", "EXP-100"]
    s = state(c.server)
    c.server.req("PUT", "/api/item", {"kind": "subset", "id": v["id"], "data": v, "base": s["hashes"]["subset"][v["id"]]})


def long_subset_name(c):
    """A subset whose name runs long: the chips that quote it ("not in …") must stay on one line."""
    v = next(x for x in state(c.server)["doc"]["subsets"] if x["id"] == "RES-TECH-EN")
    v["label"] = "Graduate software engineering roles at Sydney and Melbourne fintechs, second round"
    v["entries"] = v.get("entries", [])[1:]   # one entry left out, so its card can be unfolded
    s = state(c.server)
    c.server.req("PUT", "/api/item", {"kind": "subset", "id": v["id"], "data": v, "base": s["hashes"]["subset"][v["id"]]})


def keep_version(c):
    c.server.req("POST", "/api/subsets/RES-TECH-EN/versions", {"note": "Sent to the first round of applications"})


# -- Job search
# kept through the job search's own refreshes of /api/jobs (one can land after this, and would take them away)
JOBS_TROUBLE = js("""() => { const trouble = (st) => {
      st.checks = [{id: 'tracker', ok: false}, {id: 'reports', ok: false}];
      st.cv = {...(st.cv || {}), sync: true, conflicts: ['cv.md']}; };
    // a refresh already on its way when this runs lands after it: whatever J.data becomes keeps the trouble
    let data = J.data;
    Object.defineProperty(J, 'data', { configurable: true, get: () => data,
      set: (v) => { if (v && v.status) trouble(v.status); data = v; } });
    trouble(J.data.status);
    paintJobspace(); }""")


# -- Applying
def companies(c):
    for name, roles in (("星河科技 Xinghe Technology (Nebula, Xinghe) — campus recruiting 2027", ["服务端研发工程师（上海）", "算法工程师 — 推荐与广告方向"]),
                        ("Atlassian", ["Graduate Software Engineer"])):
        co = c.server.req("POST", "/api/hunt/companies", {"name": name, "site": "careers.example.com/campus", "subset": "RES-TECH-EN"})[1]
        cid = (co.get("company") or co)["id"]
        for r in roles:
            c.server.req("POST", f"/api/hunt/companies/{cid}/roles", {"role": r})


def company_profiles(c):
    """The first company's 网申 profile copied and read once, on 北森; the second on 北森 too, starting from it."""
    companies(c)
    for cid in ("C1", "C2"):
        c.server.req("PUT", f"/api/hunt/companies/{cid}", {"sites": [{"url": "https://careers.example.com/campus", "kind": "campus", "system": "北森"}]})
    sid = c.server.req("POST", "/api/autofill/sites", {"company": "C1"})[1]["site"]["id"]
    c.server.req("POST", f"/api/autofill/{sid}/current", {
        "url": "https://careers.example.com/campus/resume", "basics": {"about": "热爱编程，" * 40},
        "items": [{"section": "education", "key": None, "fields": {"org": "Hilltop Capital University", "degree": "MSc Computer Science", "start": "2025-07"}}],
        "site": {"sections": ["education", "experience", "projects"], "basics": ["about"], "limits": {"basics.about": 500}}})
    c.server.req("POST", "/api/autofill/sites", {"company": "C2"})


LOGO = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                     "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def ai_changes(c):
    """What a chat changed (jobhunt/history.py): long values, a URL with nothing to break on, a role, a status, one undone."""
    companies(c)
    do = lambda cmd, args: c.server.req("POST", "/api/hunt/do", {"cmd": cmd, "args": args, "chat": "layout"})[1]  # noqa: E731
    do("fill", {"ref": "C1", "fields": {
        "industry": "智能卫浴及家居制造、商用车自动驾驶、人工智能与物流科技、跨境电商、消费电子配件" * 2,
        "aliases": ["苏州智加科技有限公司", "九牧集团有限公司", "JOMOO", "Shenzhen Sinexcel Electric Co., Ltd."],
        "sources": {"industry": "https://www.example.com/front/95fb9a2da3de485698f23b46b101a99f/" + "a" * 120}}})
    aid = do("add-role", {"company": "C1", "role": "算法工程师（自动驾驶规划控制方向，2027届校园招聘，苏州 / 上海 / 北京）"})["app"]["application"]["id"]
    do("status", {"ref": aid, "status": "interview"})
    do("edit-company", {"ref": "C1", "note": "零售岗位先在厦门培训，再分配地区；具体岗位、薪酬和工作地点需向招聘方确认。"})
    first = c.server.req("GET", "/api/hunt/history")[1]["history"][-1]["id"]   # the fill: oldest last
    assert c.server.req("POST", f"/api/hunt/history/{first}/undo")[0] == 200


def filled_companies(c):
    """Companies the way Claude's fill and a scan leave them: logos, strength, roles, open postings, news."""
    companies(c)
    hunt = c.server.inst.hunt
    hunt.fill("C1", {"industry": "电商 / 互联网平台", "strength": "市值约 1,600 亿美元 (NASDAQ: XHT, 2026-10-17)", "location": "上海",
                     "size": "约 2.3 万人", "sites": [{"url": "https://careers.xinghe.example/campus", "kind": "campus", "system": "own site"},
                                                   {"url": "https://careers.xinghe.example/intern", "kind": "intern", "system": "own site"}],
                     "sources": {"strength": "https://example.com/quote"}})
    hunt.save_logo("C1", LOGO, "image/png")
    hunt.fill("C2", {"industry": "Software", "strength": "Market cap about US$45 bn (NASDAQ: TEAM, 2026-10-17)", "location": "Sydney"})
    hunt.add_company({"name": "Moonshot AI 月之暗面"})
    posts = hunt.scanner.postings
    roles = ["服务端研发工程师（上海）", "算法工程师 — 推荐与广告方向", "AI Agent 研发工程师（新设岗位）", "数据分析师", "客户端研发工程师 iOS / Android",
             "前端研发工程师", "产品经理培训生", "安全工程师", "运营管培生", "视觉设计师"]
    posts.merge("C1", [{"title": r, "url": f"https://careers.xinghe.example/campus/job/{i}", "location": "上海", "category": "研发" if i < 7 else "产品",
                        "batch": "campus", "deadline": "2030-10-31" if i % 3 == 0 else ""} for i, r in enumerate(roles)], how="route")
    posts.set_scan_state("C1", route={"pages": [{"url": "https://careers.xinghe.example/campus/jobs", "script": "async () => ({postings: []})"}]})
    posts.merge("C2", [{"title": "Graduate Software Engineer", "url": "https://jobs.lever.co/atlassian/1", "location": "Sydney, Australia"}], how="lever")
    posts.set_scan_state("C3", needs="claude", at="")
    posts.settings(keywords="研发, 算法, Agent, Engineer")


def picked_roles(c):
    """Two tracks, long names, and a company read in part: what "Matching roles" shows at its fullest."""
    filled_companies(c)
    posts = c.server.inst.hunt.scanner.postings
    posts.settings(tracks=[{"name": "研发与算法（包括推荐、广告、搜索和 AI Agent 方向的所有岗位）", "keywords": "研发, 算法, Agent", "exclude": "安全"},
                           {"name": "Graduate engineering", "keywords": "Engineer"}])
    posts.set_scan_state("C1", short=[10, 448])


def picked_ranked(c):
    """The track goes out with a resume and some roles' descriptions were read: fit badges, the sort, what's unread."""
    picked_roles(c)
    hunt = c.server.inst.hunt
    hunt.scanner.postings.settings(tracks=[{"name": "研发与算法（包括推荐、广告、搜索和 AI Agent 方向的所有岗位）", "keywords": "研发, 算法, Agent",
                                            "exclude": "安全", "subset": "RES-TECH-EN"}, {"name": "Graduate engineering", "keywords": "Engineer"}])
    # P1 and P2 are in the applications already (hidden until "Show added"): the badges go on roles still to add
    hunt._write("jds.json", {"P3": {"jd": "Python, PyTorch, scikit-learn, React, Docker", "read": "2026-11-04 10:00"},
                             "P5": {"jd": "C++, CUDA, Kubernetes, Spark, Hadoop, Flink, Kafka, Scala", "read": "2026-11-04 10:00"},
                             "P6": {"error": "the page didn't open", "read": "2026-11-04 10:00"}})


def tracks_by_nature(c):
    """A track for state employers only, one for any company; one company's nature filled in, one's not (2026-11-13)."""
    filled_companies(c)
    hunt = c.server.inst.hunt
    hunt.update_company("C2", {"nature": "foreign"})
    hunt.scanner.postings.settings(tracks=[
        {"name": "信息科技 / 软件开发（国央企，含研究院与各地分公司）", "keywords": "研发, 开发工程师", "natures": ["soe", "public", "academic"]},
        {"name": "Graduate engineering", "keywords": "Engineer, 算法, 前端, 客户端, 产品"}])


def work_status(c):
    """The profile says where the user may work (a citizen of China, a work visa for Australia), and an Australian
    employer takes citizens only: the company's page and its picked roles say so."""
    tracks_by_nature(c)
    st = c.server.inst.studio
    prof = dict(st.state()["doc"]["profile"])
    prof["work"] = {"rights": [
        {"country": "CN", "status": "citizen"},
        {"country": "AU", "status": "work", "visa": "Temporary Graduate visa (subclass 485), Post-Higher Education Work stream, full work rights",
         "from": "2027-07~", "until": "2029-07~"},
        {"country": "GB", "status": "study", "from": "2026-09", "until": "2027-12"}],
        "targets": ["CN", "AU", "SG"]}
    st.update_item("profile", None, prof, None)
    c.server.inst.hunt.update_company("C1", {"nature": "au.aps"})


def work_in_australia(c):
    """The profile names Australia only: China's own kinds of employer fold under "Other countries"."""
    tracks_by_nature(c)
    st = c.server.inst.studio
    prof = dict(st.state()["doc"]["profile"])
    prof["work"] = {"rights": [{"country": "AU", "status": "citizen"}], "targets": ["AU"]}
    st.update_item("profile", None, prof, None)


def no_tracks(c):
    filled_companies(c)
    c.server.inst.hunt.scanner.postings.settings(tracks=[])


def picked(pg, c):
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=picked]")
    pg.wait_for_selector(".picked-row, .trk-empty", timeout=20000)


def long_table(c):
    """Enough roles that the progress table scrolls inside its frame."""
    filled_companies(c)
    for i in range(30):
        c.server.req("POST", "/api/hunt/companies/C1/roles", {"role": f"岗位 {i:02d} — 一个很长的岗位名字，看它换行"})


def failed_scan(c):
    filled_companies(c)
    c.server.inst.hunt.scanner.postings.failed("C1", "the site asks to sign in", how="route", signed_out=True)


def scan_read_in_part(c):
    """One page of the route timed out (a slow site): what the others read is kept, and the error is shown."""
    filled_companies(c)
    url = "https://careers.xinghe.example/social/jobs/一个很长的地址/" + "x" * 120
    c.server.inst.hunt.scanner.postings.set_scan_state("C1", how="route", unread=[17, 20],
                                                       error=f"the page {url} didn't open in 3 tries: Page.navigate timed out")


def a_site_read_nothing(c):
    """货拉拉, 2026-11-10: one site read nothing this scan, another only some pages, both with long addresses: the
    status names them, and their rows say it beside "scanned automatically"."""
    filled_companies(c)
    hunt = c.server.inst.hunt
    none = "https://join.xinghe-technology-group-holdings.example/#/" + "y" * 80
    part = "https://xinghe-technology-group-holdings.zhiye.example/social/jobs/" + "z" * 80
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": none, "kind": "social"}, {"url": part, "kind": "intern"}]})
    hunt.scanner.postings.set_scan_state("C1", how="mokahr", missed={none: "none", part: "part"},
                                         error=f"{none}: its list gave no answer for page 1: The read operation timed out")


def sites_the_ai_tried(c):
    """A company with open roles whose other sites can't be read: one the AI was asked to learn and couldn't (思摩尔国际,
    2026-11-04), one never tried, with a long address. The table says so beside the count, every place tells them apart."""
    filled_companies(c)
    hunt = c.server.inst.hunt
    intern = "https://careers.xinghe.example/intern"
    long = "https://app.mokahr.example/campus_apply/xinghe-technology-group-holdings/150918/" + "x" * 90
    hunt.update_company("C1", {"sites": [*hunt.company("C1")["sites"], {"url": long, "kind": "social", "list": long}]})
    hunt.scanner.postings.set_scan_state("C1", needs="claude", unrouted=[intern, long], tried={intern: "2026-11-04T11:44:12"})


def systems_to_report(c):
    """Two companies whose AI-recorded routes call one API no built-in scan reads, a long path among them: the
    companies table offers to report the system to the developer (jobhunt/report.py, 2026-11-12)."""
    filled_companies(c)
    hunt = c.server.inst.hunt
    long = "/api/recruitment-platform/v3/campus-and-experienced/positions/search-with-filters-and-pagination"
    for cid in ("C1", "C2"):
        hunt.scanner.postings.set_scan_state(cid, route={"pages": [{"url": f"https://{cid.lower()}.joinus.example/job",
            "script": f"async (h) => {{ await fetch('https://api.joinus.example{long}?t=8812{cid[1:]}3456'); "
                      "await fetch('/api/v2/job/search'); return {postings: []}; }"}]})


def read_failed(c):
    """A role added from the open postings (its row says so), whose page then didn't read."""
    filled_companies(c)

    class NoPage:
        def read_page(self, url, script, timeout=60.0):
            raise RuntimeError("couldn't open https://careers.xinghe.example/campus/job/2: net::ERR_NAME_NOT_RESOLVED")
    c.server.inst.hunt.scanner.browser = NoPage()
    aid = c.server.req("POST", "/api/hunt/postings/P3/apply")[1]["application"]["id"]
    end = time.time() + 10
    while time.time() < end and not c.server.req("GET", f"/api/hunt/apps/{aid}")[1]["reading"].get("error"):
        time.sleep(0.05)


def applying(tab):
    # the tables arrive with the tracker's data: wait for them before a state reaches into DBV
    return then(key("Control+3"), click(f".hunt-tab[data-tab={tab}]"),
                lambda pg, c: pg.wait_for_function(f"() => typeof DBV !== 'undefined' && DBV.tables && DBV.tables['{tab}']"))


def open_platform(pg, c, sid="linkedin"):
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=platforms]")
    pg.click(f".dbv-view tr[data-id='{sid}'] .dbv-title")
    pg.wait_for_selector(f".fs-site.on[data-site='{sid}']")


def read_linkedin(c):
    c.server.inst.browser.headless, c.server.inst.browser.pace = True, 0
    assert c.server.req("POST", "/api/autofill/linkedin/current", FILL_NOW)[0] == 200


def linkedin_conflict(pg, c):
    pg.locator("[data-edit='basics|about']").click()
    pg.keyboard.type("Open to ML roles.")
    pg.locator(".fill-scroll").evaluate("e => e.focus()")      # out of the field: the window repaints it
    wait_until(lambda: c.server.req("GET", "/api/autofill/linkedin")[1]["local"]["basics"].get("about"), msg="typing saved")
    for _ in range(3):   # under load the site's reading can land before the window settled the edit: send it again
        c.data["n"] = c.data.get("n", 0) + 1
        now = {**FILL_NOW, "basics": {"headline": "Student at HCU", "about": f"Hello from LinkedIn {c.data['n']}."}}
        c.server.req("POST", "/api/autofill/linkedin/current", now)
        try:
            pg.wait_for_selector(".ff.conflict .ff-conflict", timeout=6000)
            return
        except Exception:  # noqa: BLE001
            continue


FILL_TROUBLE = js("""() => { const v = F.view[F.site];
    v.route = {...(v.route || {}), recorded: true, pages: 2, broken: 'the selector .pv-top-card no longer matches anything'};
    v.last_check = {...(v.last_check || {}), notes: [{kind: 'removed_there_changed_here', label: 'Graduate Teaching Assistant, School of Computing'}]};
    v.signed_out = true;
    F.browser = {...(F.browser || {}), found: true, state: 'error', error: 'Microsoft Edge closed before DevTools answered (exit code 1)'};
    paintPlatform(); paintFillBrowser(); }""")


# ---- the plan (docs/PLAN.md): a long document, with versions
PLAN_DOC = """# 2026-11 到 2027-02：找到一份实习，一个很长很长的标题看它会不会好好换行而不是撑出去

## 现在的处境
- 时间：离春季实习招聘还有大约 **4 个月**，期间还有期末考试（11 月中）。
- 缺的是底子：算法题练得少，数学基础还要补。
- 一个没有空格的长链接 https://leetcode.cn/problem-list/2cktkvj/very-long-path-without-any-spaces-at-all-to-break-it

> 先补底子，再谈投递。

| 要过的关 | 现在 | 2 月前要到 | 备注 | 另一列 | 再一列 |
| --- | --- | --- | --- | --- | --- |
| 算法题 | 刚开始 | hot 100 中等题 30 分钟写出 | 每天一道，周日复盘 | 很长的一格内容很长的一格内容 | x |

---

## 这周
- [x] 选定方向：全栈 / AI 应用
- [ ] 每天 2 道 LeetCode（数组、双指针），做不出就看题解再默写一遍，第二天再做一次
- [ ] 读懂一个开源项目的数据库 schema

```
for (let i = 0; i < n; i++) { /* 一行很长的代码，看它在代码块里横向滚动而不是撑出页面 */ }
```

## 进展
- 2026-10-19 和 Claude 理了一遍处境，定下方向
"""


def plugin_records(c):
    """Two plugins on, records at their longest; the market at an address that refuses (nothing goes out)."""
    os.environ["RESUME_STUDIO_MARKET"] = "http://127.0.0.1:9/market.json"
    s = c.server
    for pid in ("civil-service", "phd-applications"):
        s.req("PUT", f"/api/plugins/{pid}", {"on": True})
    long_unit = "中华人民共和国某某部某某司局下属某某某某某某某某事业单位（含二级机构及派出机构）"
    for i, (title, exam, stage) in enumerate([
            ("综合管理岗（一级主任科员及以下）——统计数据处理与分析方向，需熟悉 Python、R、SQL 与抽样调查方法", "national", "registered"),
            ("信息技术岗", "selected", "watching"), ("数据分析岗", "institution", "written"), ("Data officer", "other", "dropped")]):
        st, _ = s.req("POST", "/api/plugins/civil-service/records/positions", {
            "title": title, "exam": exam, "stage": stage, "unit": long_unit if i == 0 else "江苏省委组织部", "region": "南京",
            "code": "300110001001" + str(i), "quota": 2 + i, "apply_by": f"2026-1{i % 2}-2{i}", "written": "2026-11-29",
            "notice": "https://bm.scs.gov.cn/pp/gkweb/core/web/ui/business/home/gkhome.html?very=long&query=" + "x" * 80,
            "requirements": "硕士研究生及以上；" + "统计学类、计算机科学与技术类、数学类、应用经济学类；" * 6})
        assert st == 200


def phd_records(c):
    """PhD applications as linked tables (2026-11-12): a university with a long name, programmes, supervisors and a
    referee linked to them, so relation chips, a record's backlinks and the picker meet long text."""
    plugin_records(c)
    s = c.server
    add = lambda col, f: s.req("POST", f"/api/plugins/phd-applications/records/{col}", f)[1]["id"]
    long_uni = "Eidgenössische Technische Hochschule Zürich (ETH Zürich) — Department of Computer Science"
    uni = add("universities", {"title": long_uni, "country": "Switzerland", "city": "Zürich", "shortlist": "applying"})
    anu = add("universities", {"title": "Australian National University", "country": "Australia", "shortlist": "considering"})
    sup = [add("supervisors", {"title": n, "university": u, "contact": "emailed", "first_email": "2026-11-02"})
           for n, u in (("Prof. Maximilian Alexander von Hohenzollern-Sigmaringen", uni), ("Dr. Lin", anu))]
    progs = [add("programs", {"title": t, "university": u, "supervisors": sup, "stage": st, "apply_by": d})
             for t, u, st, d in (("Doctoral position in trustworthy multi-agent systems for large language models", uni, "preparing", "2026-12-15"),
                                 ("PhD in Computer Science (RTP scholarship round 2)", anu, "researching", "2027-03-31"))]
    add("referees", {"title": "Prof. Wang", "programs": progs, "stage": "asked"})


def phd_one_programme(c):
    """A programme and no universities yet: its university picker says how to add one."""
    plugin_records(c)
    c.server.req("POST", "/api/plugins/phd-applications/records/programs", {"title": "PhD in Computer Science"})


def plugin_ws(pid="civil-service", collection="positions"):
    """A plugin's own workspace (a button in the top bar), at one of its tables."""
    tab = f"p.{pid}.{collection}"
    return then(lambda pg, c: pg.wait_for_function(f"() => wsPlugin('p.{pid}')"), js(f"() => setWorkspace('p.{pid}')"),
                lambda pg, c: pg.wait_for_function(f"() => typeof DBV !== 'undefined' && DBV.tables && DBV.tables['{tab}']"))


def plan_versions(c):
    st = c.server.req("GET", "/api/plan")[1]
    st = c.server.req("PUT", "/api/plan", {"text": PLAN_DOC, "why": "第一次盘点：定下全栈 / AI 应用方向", "base": st["digest"]})[1]
    c.server.req("PUT", "/api/plan", {"text": PLAN_DOC.replace("每天 2 道", "每天 1 道"),
                                       "why": "期末周，每天两道太多，先减到一道；这条原因写得很长，看版本列表里会不会好好换行", "base": st["digest"]})


def plan(pg, c):
    pg.keyboard.press("Control+4")
    pg.wait_for_selector("#planspace .plan-empty:not(.loading), #planspace .plan-doc", timeout=20000)


STATES = [
    # ---- Resume: the document, its rail and the panes around it
    State("resume, ALL", nothing, ["#library .lib-hint", "#editor .doc-card", "#editor .doc-empty"]),   # the sample has no Experience
    State("resume, a subset", lambda pg, c: choose(pg, "#subset-select", "RES-TECH-EN"), ["#library .lib-hint.job"]),
    State("resume, outline folded", js("() => setOutline(false)"), [".outline-strip"]),
    State("resume, outline popover", then(js("() => setOutline(false)"), click(".outline-strip")), [".outline-pop"]),
    State("resume, no preview", js("() => setPreview(false)"), ["#editor .preview-toggle"]),
    State("resume, only the document", js("() => { setChatPane(false); setPreview(false); setOutline(false); }"), [".outline-strip"]),
    State("resume, progress in several projects", evidence({"EXP-002": "changed", "EXP-003": "changed", "EXP-006": "changed"}),
          ["#library .lib-update .btn"]),
    State("resume, progress in one project, a folder gone", evidence({"EXP-002": "changed", "EXP-003": "missing"}),
          ["#library .lib-update .btn", "#library .e-dot.bad"]),
    State("resume, a card with its facts open", lambda pg, c: (open_entry(pg, "EXP-002"), open_facts(pg)), ["#evidence .ev-empty"]),
    # the card's own flag on its folded facts (2026-10-31: "new progress" broke onto two lines there)
    State("resume, a card whose project has new progress",
          lambda pg, c: (open_entry(pg, "EXP-002"), evidence({"EXP-002": "changed"})(pg, c), js("() => paintEvidence()")(pg, c)),
          ["#active .ff-flag .chip.new"]),
    State("resume, a card whose project folder is gone",
          lambda pg, c: (open_entry(pg, "EXP-002"), evidence({"EXP-002": "missing"})(pg, c), js("() => paintEvidence()")(pg, c)),
          ["#active .ff-flag .chip.bad"]),
    State("resume, a subset with a long name, a card that isn't in it, unfolded",
          then(lambda pg, c: choose(pg, "#subset-select", "RES-TECH-EN"), click(".doc-fold .df-title >> nth=0"),
               wait(".doc-card .ed-head .chip"), js("() => document.querySelector('.ed-head .chip').scrollIntoView({block: 'center'})")),
          [".doc-card .ed-head .chip"], prepare=long_subset_name),
    State("resume, a card's mock interview, empty", lambda pg, c: (open_entry(pg, "EXP-002"), open_mock(pg)), ["#active .mock-empty"]),
    State("resume, a card's mock interview, answers hidden",
          lambda pg, c: (open_entry(pg, "EXP-002"), open_mock(pg), pg.click("#active .mock-hide"), pg.wait_for_selector("#active .mq-show")),
          ["#active .mq.is-follow .mq-show"], prepare=lambda c: add_mock(c.server)),
    State("resume, a card's conflict", conflict, ["#active .banner.warn"]),
    State("resume, long titles", lambda pg, c: open_entry(pg, "EXP-002"), ["#active"], prepare=long_entry),
    State("resume, line chips and issues", then(lambda pg, c: open_entry(pg, "EXP-002"), wait("#bullets .line-chip.long")),
          ["#issues .issue.warn"], prepare=lambda c: add_long_bullet(c.server)),
    # one record per country: a citizen's, a work visa with a long name and rough dates, a student visa
    State("resume, the profile's where you can work", then(js("() => select('profile')"), wait("[data-key='work-rights'] .wr-row .cp-chip"),
          js("() => document.querySelector('[data-key=work-rights]').scrollIntoView({block: 'center'})")),
          ["[data-key='work-rights'] .wr-row[data-status=work] .wr-dates .wr-rough", "[data-key='work-rights'] .wr-row[data-status=citizen] .cp-ed.single .cp-chip",
           "[data-key='work-targets'] .cp-input"], prepare=work_status),
    State("resume, the data file's warnings", nothing, ["#library .warnings"], prepare=broken_subset),
    State("resume, an empty library", nothing, ["#library .empty", "#editor .doc-empty-lib"], prepare=empty_library),
    State("resume, sections & settings", js("() => select('sections')"), ["#editor .is-page"]),
    State("resume, subset settings", js("() => select('subset', 'RES-TECH-EN')"), ["#editor .is-page"]),
    State("resume, versions", js("() => select('versions', 'RES-TECH-EN')"), ["#editor .is-page"], prepare=keep_version),
    State("resume, versions that went out to roles, one previewed",
          then(js("async () => { await switchSubset('RES-TECH-EN'); await select('versions', 'RES-TECH-EN'); }"), wait(".vrow .vsent .role-chip"), click(".vrow[data-n='1'] .vline .btn.sm"),
               wait("#viewing:not(.hidden)")),
          ["#editor .vrow .vsent .role-chip", "#viewing:not(.hidden)"],
          prepare=lambda c: (companies(c), c.server.req("POST", "/api/hunt/companies/C1/sent"), c.server.req("POST", "/api/hunt/companies/C2/sent"))),
    State("resume, a deleted subset's versions", js("() => select('versions', 'RES-TECH-EN')"), ["#editor .banner.info"],
          prepare=lambda c: (keep_version(c), c.server.req("DELETE", "/api/subsets/RES-TECH-EN"))),
    State("resume, the layout panel", click("#layout-toggle"), ["#layout-panel:not(.hidden)"]),
    State("resume, the settings dialog", js("() => openSettings()"), [".modal"]),
    # ---- the chat
    State("chat, a reply with steps", then(say("帮我看看这一条"), wait(".msg-ai .md strong")), ["#chat-log .msg-ai"]),
    State("chat, an error", then(say("FAIL 试试"), wait(".msg-error")), ["#chat-log .msg-error"]),
    State("chat, asks permission", then(say("PERM 写个文件"), wait(".ask.ask-perm.pending")), [".ask.ask-perm"]),
    State("chat, asks a question", then(say("QUESTION 颜色"), wait(".ask.ask-q.pending")), [".ask.ask-q"]),
    State("chat, proposes a plan", then(say("PLAN 改一下"), wait(".ask.ask-plan.pending")), [".ask.ask-plan"]),
    # every block kind with over-long content, every row opened: diffs, a long command and its output, a subagent's
    # steps, a table, a code block with its copy bar, a long bare link
    State("chat, every block opened", then(say("SHOWCASE"), wait(".msg-ai .code-block"), js("() => new Promise((ok) => { const t = setInterval(() => { if (!ui.chat.running) { clearInterval(t); setTimeout(ok, 300); } }, 50); })"),
                                         js("() => document.querySelectorAll('#chat-log details').forEach((d) => { d.open = true; })")),
          ["#chat-log .trow-diff", "#chat-log .trow-cmd", "#chat-log .trow-steps", "#chat-log .code-block", "#chat-log .ai-tools"]),
    # steps folding into a group while the reply runs: the summary wraps, the step under way sits on its own line
    State("chat, a group of steps running", then(wait("#chat-log"), js("""() => {
            const long = "E:/Forge/resume-studio/.cache/a-very-long-folder-name-for-testing/another-very-long-folder-name/file.yaml";
            ui.chat.running = true;
            ui.chat.live = { started_at: Date.now() / 1000, blocks: [
              { type: "tool", id: "g1", name: "Read", hint: long, status: "done", result: "x", input: {} },
              { type: "tool", id: "g2", name: "Edit", hint: long, status: "error", result: "no match", input: { edits: [{ old: "a", new: "b" + String.fromCharCode(10) + "c"}] } },
              { type: "tool", id: "g3", name: "WebFetch", hint: "https://www.example.com/" + "a".repeat(120), status: "done", result: "y", input: {} },
              { type: "tool", id: "g4", name: "Bash", hint: "python scripts/check.py " + "--flag ".repeat(30), status: "running", result: "", input: { command: "python" } }] };
            renderChat(); }""")),
          ["#chat-log .tool-group .tg-now"]),
    State("chat, tasks and a queue", then(say("TASKS SLOW"), wait("#chat-tasks:not(.hidden)"), say("排队的一条"),
                                          wait("#chat-queue:not(.hidden)")), ["#chat-tasks", "#chat-queue"]),
    # the agent picker (in Settings since 2026-10-28) with one agent not installed (its npm command) and one
    # signed out (how to sign in)
    State("settings, the agent picker", then(js("""async () => {
            // the agents load once the window is on screen (app.js onScreen), a moment after the page
            while (!(ui.claude && ui.claude.agents)) await new Promise((r) => setTimeout(r, 50));
            ui.claude.agents = ui.claude.agents.map((a) => a.id === "pi" ? { ...a, installed: false } : a.id === "qwen" ? { ...a, signed_in: false } : a);
            openSettings(); }"""), click(".modal .agent-provider-select"), wait(".listbox .lb-hint.warn")),
          [".listbox .lb-hint.warn", ".listbox .lb-desc"]),
    # a chat on Codex whose sign-in ran out: the reply says how to sign in again
    # (after startup: a chat loaded by startup once replaced these messages under load, 2026-11-12)
    State("chat, another agent signed out", then(wait("#chat-input"), lambda pg, c: pg.wait_for_function("() => ui.started"), js("""async () => {
            await pickAgent(agentOf("codex"));
            ui.claude.agents = ui.claude.agents.map((a) => a.id === "codex" ? { ...a, signed_in: false } : a);
            ui.chat.messages = [{ role: "user", text: "你好", files: [] }, { role: "assistant", blocks: [], error: "Not logged in. Run codex login." }];
            renderChat(); renderPickers(); }"""), wait(".msg-help")),
          ["#chat-log .msg-help"]),
    # every kind of step a reply shows, opened, with names and output too long for the pane (a URL as a tool's
    # name, four browser actions in one card): each stays inside the pane and reads top to bottom
    State("chat, long steps opened", then(wait("#chat-log"), lambda pg, c: pg.wait_for_function("() => ui.started"), js("""() => {
            const url = "https://cxmt.zhiye.com/campus-recruitment/metax-tech/58123/jobs?page=1&size=100&verylongparameter=abcdefghijklmnopqrstuvwxyz";
            const res = "ok: 长鑫存储技术有限公司 – " + url + "\\n" + "x".repeat(300);
            ui.chat.messages = [{ role: "user", text: "看看 " + url, files: [] }, { role: "assistant", blocks: [
              { type: "tool", name: url, status: "done", hint: "Get-Content " + url, result: res },
              { type: "tool", name: "Bash", status: "done", input: { browser: [{ cmd: "open", url }, { cmd: "text" },
                { cmd: "open", url: "https://cxmt.zhiye.com/social/jobs" }, { cmd: "type", ref: "e1", text: "长".repeat(100) }] }, result: res },
              { type: "text", text: "Done." }] }];
            renderChat(); document.querySelectorAll("#chat-log details").forEach((d) => { d.open = true; }); }""")),
          ["#chat-log .tool.bstep[open] .step-list", "#chat-log .tool[open] pre"]),
    # ---- Job search
    State("job search, not connected", key("Control+2"), ["#jobspace .connect"]),
    State("job search, git and Node missing", then(key("Control+2"), wait("#jobspace .connect"),
                                                    # the status the page loads on its own lands first, then is overridden
                                                    lambda pg, c: pg.wait_for_timeout(800),
                                                    js("() => { J.data.status.git = false; J.data.status.node = false; renderJobs(); }")),
          ["#jobspace .note-bar.warn"]),
    State("job search, its interface with warnings", then(lambda pg, c: open_job_search(pg), JOBS_TROUBLE),
          ["#jobspace .js-banners .jobs-banner"], needs=("connected",)),
    State("job search, evaluate a posting", then(lambda pg, c: open_job_search(pg), click("#evaluate-btn")), [".eval-float"],
          needs=("connected",)),
    # ---- Applying
    State("applying, no applications", applying("apps"), ["#fillspace .hunt-empty"]),
    State("applying, progress", applying("apps"), [".htable tbody tr"], prepare=companies),
    State("applying, companies", applying("companies"), [".htable tbody tr"], prepare=companies),
    State("applying, a company's page", then(applying("companies"), click(".dbv-title >> nth=0")), [".hunt-wrap.is-page"],
          prepare=companies),
    State("applying, a company's page with its 网申 profile copied", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title"),
          lambda pg, c: (pg.wait_for_selector("#company-profile-open"), pg.evaluate("() => document.querySelector('#company-profile').scrollIntoView({block: 'center'})"))),
          ["#company-profile .co-profile", "#company-profile-open"], prepare=company_profiles),
    State("applying, a company's 网申 profile copy", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title"),
          click("#company-profile-open"), wait(".fill-card.fill-sync")), [".fill-head h1", ".fill-card.fill-sync", ".hunt-back", ".fill-compare"],
          prepare=company_profiles),
    State("applying, a 网申 profile copy that starts from another company's", then(applying("companies"), click(".dbv-view tr[data-id='C2'] .dbv-title"),
          click("#company-profile-open"), wait(".fill-card.fill-start")), [".fill-head .fh-text p.muted", ".fill-card.fill-start"],
          prepare=company_profiles),
    State("applying, a company that may be on the list already",
          then(applying("companies"), click("#hunt-new-company"), lambda pg, c: pg.fill("#fm-name", "Atlassian Pty Ltd"),
               click(".modal .btn.primary"), wait(".modal .fm-notice-hint")),
          [".modal .fm-notice-slot .fm-notice", ".modal .fm-notice-hint"], prepare=companies),
    State("applying, an application's page", then(applying("apps"), click(".dbv-title >> nth=0")), ["#app-status"],
          prepare=companies),
    State("applying, a recruiting system offered to report", then(applying("companies"), wait("#hunt-report")),
          ["#hunt-report", "#hunt-report-open", "#hunt-report-ignore"], prepare=systems_to_report),
    State("applying, the report to the developer", then(applying("companies"), wait("#hunt-report"), click("#hunt-report-open"),
                                                       wait(".modal .report-text")),
          [".modal .report-text", ".modal #report-copy", ".modal .report-alt .muted"], prepare=systems_to_report),
    State("applying, what the AI changed, nothing yet", then(applying("companies"), click("#hunt-history"), wait(".hist-modal .hist-empty")),
          [".hist-modal .hist-empty"], prepare=companies),
    State("applying, what the AI changed, long changes, one undone",
          then(applying("companies"), click("#hunt-history"), wait(".hist-modal .hist-row"),
               js("() => document.querySelectorAll('.hist-diff').forEach((d) => { d.open = true; })")),
          [".hist-row .hist-field .hist-before", ".hist-row .hist-after", ".hist-row.is-undone .hist-done", ".hist-before.is-blank",
           ".hist-row .hist-act"], prepare=ai_changes),
    State("applying, companies filled in, with today's changes", applying("companies"),
          [".htable .co-logo img", ".htable .role-chip.open-chip", ".scan-news .news-row", ".scan-news .note-bar"], prepare=filled_companies),
    State("applying, picked for you, no tracks yet", picked, [".trk-empty .trk-ex-list", ".trk-empty .trk-empty-do", ".trk-empty .trk-empty-hint"], prepare=no_tracks),
    State("applying, picked for you, two tracks, one company read in part", picked,
          [".picked-row .trk-tag", ".trk-segs .trk-seg.on", ".picked-tools ~ .note-bar.warn", "#picked-no-subset"], prepare=picked_roles),
    State("applying, picked for you, ranked by fit, some descriptions not read", picked,
          [".picked-row .pk-fit.hi", ".picked-row .pk-fit.lo", "#picked-sort", "#picked-unread"], prepare=picked_ranked),
    State("applying, editing tracks", then(picked, click("#picked-edit-tracks"), wait(".trk-modal .trk-row")),
          [".trk-modal .trk-row .trk-kw", ".trk-modal .trk-subset", ".trk-modal .trk-more"], prepare=picked_roles),
    State("applying, editing tracks for some kinds of company only", then(picked, click("#picked-edit-tracks"), wait(".trk-modal .trk-row")),
          [".trk-modal .trk-nats .trk-nat.on", ".trk-modal .trk-nat-any:not([hidden])"], prepare=tracks_by_nature),
    State("applying, a company that takes citizens only", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          ["#company-short-of", "#company-nature-pick"], prepare=work_status),
    State("applying, picked for you, an employer that takes citizens only", picked, [".picked-row .pk-short"], prepare=work_status),
    State("applying, editing tracks with the profile's countries", then(picked, click("#picked-edit-tracks"), wait(".trk-modal .trk-row"),
                                                                       click(".trk-modal .trk-nat-more >> nth=0")),
          [".trk-modal .trk-nat-group .trk-nat-country", ".trk-modal .trk-nat-more[aria-expanded=true]"], prepare=work_in_australia),
    State("applying, companies, one with its nature", applying("companies"), [".htable td[data-key='nature'] .state-chip"],
          prepare=tracks_by_nature),
    State("applying, a company whose nature isn't filled in", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          ["#company-nature-unknown", "#company-nature-pick"], prepare=tracks_by_nature),
    State("applying, a company's open roles", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          [".post-list .post-row", ".co-sites .co-site-edit .co-site-input", ".post-row .new-chip"], prepare=filled_companies),
    State("applying, a company's open roles, folded", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title"),
          click("section.card:has(#company-scan) .card-fold")), ["section.card.folded #company-scan", "section.card.folded .card-n"],
          prepare=filled_companies),
    State("applying, a company's open roles, one added", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          [".post-row .pr-added"], prepare=read_failed),
    State("applying, a role whose page didn't read", then(applying("apps"), click(".dbv-view tr[data-id='A4'] .dbv-title"),
          lambda pg, c: (pg.wait_for_selector(".note-bar.warn #app-read, #app-read"), pg.evaluate("() => document.querySelector('#app-read').scrollIntoView({block: 'center'})"))), ["section.card .note-bar.warn", "#app-read"], prepare=read_failed),
    State("applying, a company whose scan failed", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          [".card-acts .fs-status.bad", ".note-bar.warn"], prepare=failed_scan),
    State("applying, companies with roles and a site no scan reads", applying("companies"),
          [".htable .role-chip.open-chip", ".htable .scan-gap", ".scan-news .note-bar"], prepare=sites_the_ai_tried),
    State("applying, a company with a site the AI tried and one it didn't", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          ["#company-no-route", ".co-sites .site-scan.warn", ".post-row"], prepare=sites_the_ai_tried),
    State("applying, a company whose scan read only some pages", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          [".card-acts .fs-status.warn", ".note-bar.warn", ".post-row"], prepare=scan_read_in_part),
    State("applying, a company whose scan read nothing off one site", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          ["#company-read-in-part", ".co-sites .site-missed"], prepare=a_site_read_nothing),
    State("applying, a long table scrolled both ways, two columns frozen", then(applying("apps"),
          js("""() => { const tb = DBV.tables.apps, v = tb.views[0]; v.freeze = 2; paintHunt(); }"""),
          js("() => { document.querySelector('.hunt-scroll').scrollTop = 400; document.querySelector('.dbv-body').scrollLeft = 300; }")),
          [".dbv-head .dbv-th.frz-last", ".dbv-body td.frz-last"], prepare=long_table),
    State("applying, a long companies table scrolled to its end", then(applying("companies"),
          js("() => { const s = document.querySelector('.hunt-scroll'); s.scrollTop = s.scrollHeight; }")),
          [".dbv-view[data-table=companies] .dbv-body tbody tr"], prepare=long_table),
    State("applying, progress as a board", then(applying("apps"), click(".dbv-tab[data-view=v3]")), [".dbv-board .dbv-card"], prepare=companies),
    State("applying, progress grouped by status", then(applying("apps"), js("""() => { const tb = DBV.tables.apps, v = tb.views[1];
        tb.active = v.id; v.group = 'status'; v.sorts = [{key: 'role', dir: 'desc'}]; paintHunt(); }""")), [".dbv-group .dbv-fold", ".dbv-tool.on"],
          prepare=companies),
    State("applying, a status picked in its cell", then(applying("apps"), click(".dbv-view tbody tr >> nth=0 >> td[data-key=status]")),
          [".float.dbv-options"], prepare=companies),
    State("applying, a cell typed over", then(applying("apps"), click(".dbv-view tbody tr >> nth=0 >> td[data-key=referral]")),
          [".float.dbv-textpanel"], prepare=companies),
    State("applying, the resume sent picked in its cell", then(applying("apps"),
                                                               lambda pg, c: pg.click(".dbv-view tbody tr >> nth=0 >> td[data-key=sent]", position={"x": 6, "y": 6}),
                                                               wait(".float.sentpop #sentpop-versions .lb-opt")),
          [".float.sentpop #sentpop-subsets .lb-opt.on", ".float.sentpop #sentpop-versions .lb-opt"], prepare=companies),
    State("applying, the calendar", then(applying("apps"), click(".dbv-tab[data-view=v4]")), [".dbv-cal"], prepare=companies),
    State("applying, the Gantt chart", then(applying("apps"), click(".dbv-tab[data-view=v5]")), [".dbv-gantt"], prepare=companies),
    State("applying, the world map", then(applying("apps"), click(".dbv-tab[data-view=v6]")), [".dbv-map .dbv-mapstage"], prepare=companies),
    State("applying, the companies' map", then(applying("companies"), click(".dbv-tab[data-view=v2]")), [".dbv-map .dbv-mapstage"], prepare=companies),
    State("applying, a filter being set", then(applying("apps"), click(".dbv-chip[data-key=status]")), [".float.dbv-filterpanel .dbv-check"],
          prepare=companies),
    State("applying, sorts being set", then(applying("apps"), js("() => { DBV.tables.apps.views[0].sorts = [{key: 'events', dir: 'desc'}]; paintHunt(); }"),
                                            click("#dbv-apps-sort"), click(".float .select.dbv-sortkey")), [".float.dbv-sortpanel .dbv-inlist"],
          prepare=companies),
    State("applying, the properties panel", then(applying("apps"), click("#dbv-apps-props")), [".float.dbv-propspanel .dbv-proprow"], prepare=companies),
    State("applying, companies with every property, wrapped", then(applying("companies"), js("""() => {
        const v = DBV.tables.companies.views[0]; v.columns = companyProps().map((p) => ({key: p.key, width: null, hidden: false, wrap: true})); paintHunt(); }""")),
          [".dbv-td.wrap"], prepare=filled_companies),
    State("applying, platforms, one signed out", then(applying("platforms"), wait(".dbv-view tbody tr"),
                                                      js("() => { F.sites[0].signed_out = true; paintHunt(); }")),
          [".htable .state-chip.s-warn"]),
    State("applying, a platform not read yet", then(open_platform, wait("#fillspace .fill-start")), ["#fillspace .fill-start"]),
    State("applying, a platform's copy in conflict", then(open_platform, wait("#fill-tabs .fv-tab.on"), linkedin_conflict,
                                                          wait(".ff.conflict .ff-conflict")),
          [".ff-conflict", "#fill-tabs .fs-n.warn", ".fill-banner.warn"], prepare=read_linkedin),
    State("applying, a platform in trouble", then(open_platform, wait("#fill-tabs .fv-tab.on"), FILL_TROUBLE),
          [".fill-banner.warn"], prepare=read_linkedin),
    State("plan, empty", plan, ["#planspace .plan-empty", "#chat .chat-welcome"]),
    State("plan, a long document", then(plan, wait("#planspace .plan-doc")), ["#planspace .plan-doc li.task .check", "#planspace .plan-doc table"],
          prepare=plan_versions),
    State("plan, being edited while Claude changed it", then(plan, click("#plan-edit"), wait("#planspace .plan-text"),
          js("() => { P.stale = true; paintPlanEditBar(); }")), ["#planspace .plan-text", "#planspace .pe-stale", "#planspace .plan-why"],
          prepare=plan_versions),
    State("plan, its versions and a comparison", then(plan, click("#plan-versions"), click(".plan-side .ps-item >> nth=2"),
          wait(".plan-vbar"), click(".plan-vbar [role=radio] >> nth=1")), [".plan-side .ps-why", ".plan-diff .dl-add"], prepare=plan_versions),
    # ---- plugins (docs/PLUGINS.md): last, since turning them on adds workspaces to the top bar
    State("plugins, a table with long records", plugin_ws(), ["#pluginspace .dbv-view[data-table='p.civil-service.positions'] tbody tr"],
          prepare=plugin_records),
    State("plugins, a table as a board", then(plugin_ws(), js("() => { DBV.tables['p.civil-service.positions'].active = 'v2'; paintPluginspace(); }"),
          wait("#pluginspace .dbv-view[data-layout=board]")), ["#pluginspace .dbv-view[data-layout=board] .dbv-card"], prepare=plugin_records),
    State("plugins, a record's page", then(plugin_ws(), click("#pluginspace .dbv-title >> nth=0"), wait("#pluginspace .plugin-pair")),
          ["#pluginspace .plugin-pair .select", "#pluginspace .plugin-pair input[type=date]"], prepare=plugin_records),
    State("plugins, a plugin's empty table", then(plugin_ws("phd-applications", "programs")), ["#pluginspace .plugin-empty"], prepare=plugin_records),
    State("plugins, linked records in a table", then(js("() => { PW.col['phd-applications'] = 'programs'; }"), plugin_ws("phd-applications", "programs")),
          ["#pluginspace .dbv-view[data-table='p.phd-applications.programs'] tbody tr", "#pluginspace .dbv-td .plugin-rel"], prepare=phd_records),
    State("plugins, a record's page with what links to it",
          then(js("() => { PW.col['phd-applications'] = 'universities'; }"), plugin_ws("phd-applications", "universities"),
               click("#pluginspace .dbv-title >> nth=0"), wait("#pluginspace .plugin-backlinks .plugin-linkrow")),
          ["#pluginspace .plugin-backlinks .plugin-linkrow", "#pluginspace .plugin-backlinks .state-chip"], prepare=phd_records),
    State("plugins, picking linked records",
          then(js("() => { PW.col['phd-applications'] = 'programs'; }"), plugin_ws("phd-applications", "programs"),
               click("#pluginspace .dbv-title >> nth=0"), click("#pluginspace .plugin-relbtn[data-key=supervisors]"), wait(".plugin-relpick .lb-opt.on")),
          [".plugin-relpick .lb-opt", ".plugin-relpick input"], prepare=phd_records),
    State("plugins, picking from a table with nothing in it",
          then(js("() => { PW.col['phd-applications'] = 'programs'; }"), plugin_ws("phd-applications", "programs"),
               click("#pluginspace .dbv-title >> nth=0"), click("#pluginspace .plugin-relbtn[data-key=university]"), wait(".plugin-relpick .lb-empty")),
          [".plugin-relpick .lb-empty"], prepare=phd_one_programme),
    State("plugins, in Settings", then(js("() => openSettings()"), wait("#plugins-settings .plugin-row"),
          js("() => document.querySelector('#plugins-settings').scrollIntoView()")), ["#plugins-settings .plugin-row .check-row"],
          prepare=plugin_records),
    State("plugins, the market while it can't be reached", then(js("() => openSettings()"), click("#plugin-market-open"),
          wait(".modal.market .market-row")), [".modal.market .market-row", ".modal.market .fs-status.warn"], prepare=plugin_records),
]


def run_state(pg, server, st: State):
    c = Ctx(server)
    if st.prepare:
        st.prepare(c)
    problems: list[str] = []
    for lang in dict.fromkeys(lg for _, lg in LOOKS):
        open_window(pg, server, lang)
        st.show(pg, c)
        for sel in st.covers:
            try:
                pg.wait_for_selector(sel, state="visible", timeout=WAIT_MS)
            except Exception as e:  # noqa: BLE001  says what is on screen instead, so a red run explains itself
                seen = {s: pg.locator(s).count() for s in st.covers}
                raise AssertionError(f"{st.name} [{lang}]: {sel} never showed; on screen: {seen}") from e
        for (w, h), lg in LOOKS:
            if lg == lang:
                pg.set_viewport_size({"width": w, "height": h})
                problems += look(pg, f"{st.name} [{lang} {w}]")
    return problems


PLAIN = [s for s in STATES if not s.needs]
CONNECTED = [s for s in STATES if "connected" in s.needs]


DRAG_MAP = r"""
async () => {
  const out = [];
  for (const ws of ['resume', 'jobs', 'fill', 'plan']) {
    document.querySelector(`.ws-btn[data-ws=${ws}]`).click();
    await new Promise(r => setTimeout(r, 400));
    // app-region is inherited: what a point does is what the box drawn on top there says
    const end = innerWidth - parseFloat(getComputedStyle(document.querySelector('.topbar')).paddingRight);
    const dead = new Set(), grabbed = new Set();
    for (let y = 2; y < 50; y += 6) for (let x = 2; x < end; x += 4) {
      const el = document.elementFromPoint(x, y);
      if (!el) continue;
      const control = el.closest('button, a, input, select, textarea, [role=button], [role=tab], [role=combobox], .select');
      const moves = getComputedStyle(el).appRegion === 'drag';
      const name = (control || el).id || String((control || el).className).split(' ')[0] || el.tagName;
      if (!control && !moves) dead.add(name);
      if (control && moves) grabbed.add(name);
    }
    if (dead.size) out.push(`${ws} ${innerWidth}: empty spots that don't move the window, over ${[...dead].join(', ')}`);
    if (grabbed.size) out.push(`${ws} ${innerWidth}: controls that would move the window: ${[...grabbed].join(', ')}`);
  }
  return out;
}
"""


def test_every_empty_spot_of_the_header_moves_the_window(win, server):
    """The header row is the title bar: anywhere between its controls drags the window, a control never does
    (user report 2026-11-11: most of 网申's header couldn't be dragged)."""
    open_window(win, server, "zh")
    problems = []
    for w in (1100, 1440, 1920):
        win.set_viewport_size({"width": w, "height": 900})
        settle(win)
        problems += win.evaluate(DRAG_MAP)
    assert not problems, "\n" + "\n".join(problems)


MANY_PLUGINS = r"""
async () => {
  // fifteen plugins on, with long names: each a workspace with its button in the top bar
  const base = PL.list.find((p) => p.manifest);
  const names = ['各国博士申请（欧洲方向）', '海外人才计划', '选调生', '教师编制考试', '国企校招', '军队文职', '银行秋招', '研究生推免',
                 '博士后进站', '高校人才招聘', '医疗卫生系统招聘', '事业单位联考'];
  PL.list = [...PL.list.map((p) => ({ ...p, on: !!p.manifest })), ...names.map((n, i) => ({ ...base, id: 'many-' + i, on: true,
    manifest: { ...base.manifest, id: 'many-' + i, name: { zh: n, en: n } } }))];
  paintPluginButtons();
  const out = [];
  const bar = document.querySelector('.topbar');
  const last = pluginsOn()[pluginsOn().length - 1];
  for (const ws of ['resume', 'jobs', 'fill', 'plan', 'p.' + base.id, 'p.' + last.id]) {
    setWorkspace(ws);
    await new Promise((r) => setTimeout(r, 300));
    // the empty width along the bar's middle line: points with no control under them
    let room = 0;
    const end = innerWidth - parseFloat(getComputedStyle(bar).paddingRight);
    for (let x = 0; x < end; x += 2) {
      const el = document.elementFromPoint(x, 25);
      if (el && !el.closest('button, a, input, select, textarea, [role=button], [role=tab], [role=combobox], .select, .save-status:not(:empty)')) room += 2;
    }
    const on = document.querySelector(`.ws-btn[data-ws="${ws}"]`);
    if (room < 120) out.push(`${ws} ${innerWidth}: only ${room}px of the title bar left to drag the window by`);
    if (bar.scrollWidth > bar.clientWidth + 1) out.push(`${ws} ${innerWidth}: the top bar overflows`);
    if (!on || !on.getClientRects().length) out.push(`${ws} ${innerWidth}: the workspace on screen has no button`);
    const hidden = document.querySelectorAll('.plugin-ws.hidden').length;
    if (hidden && !document.querySelector('.ws-more:not(.hidden)')) out.push(`${ws} ${innerWidth}: ${hidden} plugins hidden, no menu for them`);
  }
  return out;
}
"""


def test_many_plugins_keep_room_to_drag_the_window(win, server):
    """Each plugin is a workspace in the top bar, which is the window's title bar: however many are on, what doesn't
    fit goes into a menu, the one on screen keeps its button, and room to drag the window stays (user 2026-11-12)."""
    open_window(win, server, "zh")
    win.wait_for_function("() => PL.loaded")
    problems = []
    for w in (1100, 1440, 1920):
        win.set_viewport_size({"width": w, "height": 900})
        settle(win)
        problems += win.evaluate(MANY_PLUGINS)
    assert not problems, "\n" + "\n".join(problems)


def test_an_empty_country_list_in_the_profile_shows_nothing(win, server):
    """2026-11-13: a country list with nothing picked showed the word "false"; a record just added has none."""
    st = next(s for s in STATES if s.name == "resume, the profile's where you can work")
    work_status(Ctx(server))
    open_window(win, server, "en")
    st.show(win, Ctx(server))
    win.click("[data-key='work-rights'] .wr-add")
    text = win.inner_text("[data-key='work-rights'] .wr-row >> nth=-1")
    assert "false" not in text and "true" not in text, text


def test_where_you_can_work_is_one_record_per_country_with_rough_dates(win, server):
    """2026-11-13: one record per country (China: citizen; Australia: a 485 visa from about 2027-07), each date
    marked when only roughly known; the first shape (a list per status) is read as records and saved as them."""
    st = next(s for s in STATES if s.name == "resume, the profile's where you can work")
    studio = server.inst.studio
    prof = dict(studio.state()["doc"]["profile"])
    prof["work"] = {"citizen": ["CN"], "targets": ["AU"]}                 # the first shape
    studio.update_item("profile", None, prof, None)
    open_window(win, server, "en")
    st.show(win, Ctx(server))
    assert win.inner_text("[data-key='work-rights'] .wr-row >> nth=0 >> .cp-chip").startswith("China")
    win.click("[data-key='work-rights'] .wr-add")
    row = "[data-key='work-rights'] .wr-row >> nth=1"
    win.locator(f"{row} >> .cp-input").type("Austral")
    win.wait_for_selector("[data-key='work-rights'] .pl-sugg .pl-opt")
    win.keyboard.press("Enter")
    assert win.locator(f"{row} >> .wr-status").get_attribute("data-value") == "work"   # a second record starts as a work visa
    win.locator(f"{row} >> .wr-visa").fill("485")
    win.click(f"{row} >> .wr-month[data-key=from]")
    win.click(".month-pop .mp-next")
    win.click(".month-pop .mp-m >> nth=6")
    win.click(f"{row} >> .wr-rough >> nth=0")
    year = win.evaluate("() => new Date().getFullYear()") + 1
    work = lambda: studio.state()["doc"]["profile"].get("work") or {}
    wait_until(lambda: work().get("rights") == [{"country": "CN", "status": "citizen"},
                                                {"country": "AU", "status": "work", "visa": "485", "from": f"{year}-07~"}],
               msg="saved as records")
    assert "citizen" not in work() and work()["targets"] == ["AU"]
    assert win.get_attribute(f"{row} >> .wr-rough >> nth=0", "aria-checked") == "true"


def test_a_country_is_found_by_the_start_of_its_name_and_kept_in_the_profile(win, server):
    """2026-11-13: typing 日 offered 尼日利亚 before 日本 (names that start with what was typed came last)."""
    st = next(s for s in STATES if s.name == "resume, the profile's where you can work")
    work_status(Ctx(server))
    open_window(win, server, "zh")
    st.show(win, Ctx(server))
    inp = win.locator("[data-key='work-targets'] .cp-input")
    inp.click()
    inp.type("日")
    win.wait_for_selector("[data-key='work-targets'] .pl-sugg .pl-opt")
    assert win.inner_text("[data-key='work-targets'] .pl-sugg .pl-opt >> nth=0").startswith("日本")
    inp.press("Enter")
    wait_until(lambda: "JP" in (server.inst.studio.state()["doc"]["profile"].get("work") or {}).get("targets", []))
    inp = win.locator("[data-key='work-targets'] .cp-input")
    inp.type("new zea")                                                  # by its English name too
    win.wait_for_selector("[data-key='work-targets'] .pl-sugg .pl-opt")
    assert "新西兰" in win.inner_text("[data-key='work-targets'] .pl-sugg")


def test_a_long_tracks_button_is_cut_at_its_end_not_its_start(win, server):
    """2026-11-13: the tracks named in the head of Today were a bare text node in a centred button, so a long name
    stuck out of both sides and the start of the first track's name was cut off."""
    st = next(s for s in STATES if s.name == "applying, companies, one with its nature")
    tracks_by_nature(Ctx(server))
    open_window(win, server, "en")
    st.show(win, Ctx(server))
    win.wait_for_selector("#hunt-tracks", state="visible")
    for w in (800, 1280):
        win.set_viewport_size({"width": w, "height": 800})
        win.wait_for_timeout(150)
        # the names are in an element of their own, inside the button, cut at its end with an ellipsis
        got = win.evaluate("""() => {
            const btn = document.querySelector('#hunt-tracks'), el = btn.querySelector(':scope > span');
            if (!el) return {text: 'a bare text node'};
            const b = btn.getBoundingClientRect(), r = el.getBoundingClientRect();
            return {left: Math.round(r.left - b.left), right: Math.round(b.right - r.right), cut: el.scrollWidth > el.clientWidth,
                    ellipsis: getComputedStyle(el).textOverflow === 'ellipsis'};
        }""")
        assert got.get("left", -1) >= 0 and got["right"] >= 0 and got["ellipsis"], (w, got)


def test_right_hand_tools_sit_next_to_the_settings_button(win, server):
    """Applying's browser chip stood halfway along the header, neither left nor right (user report 2026-11-11):
    the workspace's tools and the row each had a spacer, and they split the free room."""
    open_window(win, server, "zh")
    gaps = {}
    for w in (1100, 1920):
        win.set_viewport_size({"width": w, "height": 900})
        for ws in ("jobs", "fill"):
            win.click(f".ws-btn[data-ws={ws}]")
            settle(win)
            gaps[f"{ws} {w}"] = win.evaluate("""() => {
                const tools = [...document.querySelectorAll('#ws-tools > *')].filter(e => e.getBoundingClientRect().width);
                const last = tools[tools.length - 1].getBoundingClientRect();
                return Math.round(document.querySelector('#settings-btn').getBoundingClientRect().left - last.right);
            }""")
    assert all(g <= 24 for g in gaps.values()), gaps


@pytest.mark.parametrize("st", PLAIN, ids=[s.name for s in PLAIN])
def test_layout(st, win, server):
    problems = run_state(win, server, st)
    assert not problems, "\n" + "\n".join(problems)


@pytest.mark.parametrize("st", CONNECTED, ids=[s.name for s in CONNECTED])
def test_layout_with_career_ops(st, connected, win, server):   # noqa: F811  (connected before the server starts)
    problems = run_state(win, server, st)
    assert not problems, "\n" + "\n".join(problems)


# ---------------------------------------------------------------- the catalogue covers the code
# Class names that mark a piece of UI shown only in some situations. When the web code gains one, a
# state above must show it (its selector in some `covers`), or it goes in NOT_SHOWN with the reason.
NOTICE = re.compile(r"(?:^|[\s.-])(banner|warn|warnings|update|conflict|empty|issue|error|strip|notice|toast|alert)(?:$|[\s-])")
GENERIC = {"muted"}                     # styling words that say nothing about which piece it is
NOT_SHOWN = {
    "plan-empty loading": "the plan's 'Loading…' before it arrives: one muted line, drawn like the empty plan, which is covered",
    "muted vdiff-empty": "the comparison's 'Comparing…' / 'No differences' line: one muted sentence in a wide page",
    "fill-banner ok": "the 'done' line after Claude pushed changes: needs a full browser run (test_autofill_copy_edits_conflicts_and_push)",
    "fi-op op-update": "the 'Edited' tag on a copy item: a short chip, shown in the conflict state's neighbours",
    "fi-op op-conflict": "the 'Conflict' tag: shown with the conflict state when the item is a list item; ours is a basics field",
    "ff-warn": "the over-the-limit line under a copy field: plain text under a full-width field",
    "issue": "the plain issue chip: drawn like 'issue warn', which is covered",
    "st error": "a failed tool step's icon: a fixed-size icon",
    "banner warn": "covered as '#active .banner.warn'; the other 'banner warn's are job-search pages' error lines",
    "outline-strip": "covered by 'resume, outline folded'",
}


def open_mock(pg):
    pg.click("#active .mock-fold > summary")
    pg.wait_for_selector("#active .mock-body", state="visible")


def add_mock(server):
    """EXP-002 with a mock interview: long questions, an answer, a follow-up with none yet."""
    s = state(server)
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    e["interview"] = [{"q": "Why did you split the pipeline into stages instead of writing one long prompt that does everything at once?",
                       "a": "One prompt drifted: questions stopped matching the slides after a few pages. " * 3,
                       "follow_ups": [{"q": "How did you notice the drift, and how did you measure it?", "a": ""}]},
                      {"q": "为什么拆成多阶段？", "a": "【待补充：一个具体例子】"}]
    server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})


def classes_in_code() -> set[str]:
    found = set()
    for f in WEB.glob("*.js"):
        for m in re.finditer(r'class: "([^"]+)"', f.read_text(encoding="utf-8")):
            if NOTICE.search(m.group(1)):
                found.add(m.group(1).strip())
    return found


def test_every_notice_is_in_the_catalogue():
    covered = " ".join(sel for s in STATES for sel in s.covers)
    missing = []
    for cls in sorted(classes_in_code()):
        if cls in NOT_SHOWN:
            continue
        if not all("." + part in covered for part in cls.split() if part not in GENERIC):
            missing.append(cls)
    assert not missing, ("These conditional pieces of UI are shown by no state in STATES; add one that brings each on "
                         "screen (or a line in NOT_SHOWN saying why it needs none): " + ", ".join(missing))
