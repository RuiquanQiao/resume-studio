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
import re
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
LOOKS = [((1280, 800), "zh"), ((1440, 900), "zh"), ((1280, 800), "en"), ((1920, 1080), "en"), ((1280, 800), "de")]

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


def wait(sel):
    return lambda pg, c: pg.wait_for_selector(sel, timeout=20000)


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
    e["location"] = "Canberra, Australian Capital Territory"
    c.server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})


def evidence(states: dict):
    """The project-folder state the library shows for some entries (normally from git; set here)."""
    return js(f"""() => {{ const st = {json.dumps(states)};
        for (const [id, s] of Object.entries(st)) ui.evidence[id] = {{state: s, paths: [{{path: 'E:/Forge/demo', state: s}}]}};
        renderLibrary(); }}""")


def conflict(pg, c):
    open_entry(pg, "EXP-002")
    pg.type("#active input[data-key=location]", "Canberra")        # unsaved here...
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


def keep_version(c):
    c.server.req("POST", "/api/subsets/RES-TECH-EN/versions", {"note": "Sent to the first round of applications"})


# -- Job search
JOBS_TROUBLE = js("""() => { const st = J.data.status;
    st.checks = [{id: 'tracker', ok: false}, {id: 'reports', ok: false}];
    st.cv = {...(st.cv || {}), sync: true, conflicts: ['cv.md']};
    paintJobspace(); }""")


# -- Applying
def companies(c):
    for name, roles in (("星河科技 Xinghe Technology (Nebula, Xinghe) — campus recruiting 2027", ["服务端研发工程师（上海）", "算法工程师 — 推荐与广告方向"]),
                        ("Atlassian", ["Graduate Software Engineer"])):
        co = c.server.req("POST", "/api/hunt/companies", {"name": name, "site": "careers.example.com/campus", "subset": "RES-TECH-EN"})[1]
        cid = (co.get("company") or co)["id"]
        for r in roles:
            c.server.req("POST", f"/api/hunt/companies/{cid}/roles", {"role": r})


LOGO = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                     "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


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
                        "batch": "campus", "deadline": "2026-10-31" if i % 3 == 0 else ""} for i, r in enumerate(roles)], how="route")
    posts.set_scan_state("C1", route={"pages": [{"url": "https://careers.xinghe.example/campus/jobs", "script": "async () => ({postings: []})"}]})
    posts.merge("C2", [{"title": "Graduate Software Engineer", "url": "https://jobs.lever.co/atlassian/1", "location": "Sydney, Australia"}], how="lever")
    posts.set_scan_state("C3", needs="claude", at="")
    posts.settings(keywords="研发, 算法, Agent, Engineer")


def failed_scan(c):
    filled_companies(c)
    c.server.inst.hunt.scanner.postings.failed("C1", "the site asks to sign in", how="route", signed_out=True)


def applying(tab):
    return then(key("Control+3"), click(f".hunt-tab[data-tab={tab}]"))


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
        now = {**FILL_NOW, "basics": {"headline": "Student at ANU", "about": f"Hello from LinkedIn {c.data['n']}."}}
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


def plan_versions(c):
    st = c.server.req("GET", "/api/plan")[1]
    st = c.server.req("PUT", "/api/plan", {"text": PLAN_DOC, "why": "第一次盘点：定下全栈 / AI 应用方向", "base": st["digest"]})[1]
    c.server.req("PUT", "/api/plan", {"text": PLAN_DOC.replace("每天 2 道", "每天 1 道"),
                                       "why": "期末周，每天两道太多，先减到一道；这条原因写得很长，看版本列表里会不会好好换行", "base": st["digest"]})


def plan(pg, c):
    pg.keyboard.press("Control+4")
    pg.wait_for_selector("#planspace .plan-empty, #planspace .plan-doc", timeout=20000)


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
    State("resume, a card's conflict", conflict, ["#active .banner.warn"]),
    State("resume, long titles", lambda pg, c: open_entry(pg, "EXP-002"), ["#active"], prepare=long_entry),
    State("resume, line chips and issues", then(lambda pg, c: open_entry(pg, "EXP-002"), wait("#bullets .line-chip.long")),
          ["#issues .issue.warn"], prepare=lambda c: add_long_bullet(c.server)),
    State("resume, the data file's warnings", nothing, ["#library .warnings"], prepare=broken_subset),
    State("resume, an empty library", nothing, ["#library .empty", "#editor .doc-empty-lib"], prepare=empty_library),
    State("resume, sections & settings", js("() => select('sections')"), ["#editor .is-page"]),
    State("resume, subset settings", js("() => select('subset', 'RES-TECH-EN')"), ["#editor .is-page"]),
    State("resume, versions", js("() => select('versions', 'RES-TECH-EN')"), ["#editor .is-page"], prepare=keep_version),
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
    State("chat, tasks and a queue", then(say("TASKS SLOW"), wait("#chat-tasks:not(.hidden)"), say("排队的一条"),
                                          wait("#chat-queue:not(.hidden)")), ["#chat-tasks", "#chat-queue"]),
    # ---- Job search
    State("job search, not connected", key("Control+2"), ["#jobspace .connect"]),
    State("job search, git and Node missing", then(key("Control+2"), wait("#jobspace .connect"),
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
    State("applying, an application's page", then(applying("apps"), click(".dbv-title >> nth=0")), ["#app-status"],
          prepare=companies),
    State("applying, companies filled in, with today's changes", applying("companies"),
          [".htable .co-logo img", ".htable .role-chip.open-chip", ".scan-news .news-row", ".scan-news .note-bar"], prepare=filled_companies),
    State("applying, a company's open roles", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          [".post-list .post-row", ".co-sites .co-site", ".post-row .new-chip"], prepare=filled_companies),
    State("applying, a company whose scan failed", then(applying("companies"), click(".dbv-view tr[data-id='C1'] .dbv-title")),
          [".post-tools .fs-status.bad", ".note-bar.warn"], prepare=failed_scan),
    State("applying, progress as a board", then(applying("apps"), click(".dbv-tab[data-view=v3]")), [".dbv-board .dbv-card"], prepare=companies),
    State("applying, progress grouped by status", then(applying("apps"), js("""() => { const tb = DBV.tables.apps, v = tb.views[1];
        tb.active = v.id; v.group = 'status'; v.sorts = [{key: 'role', dir: 'desc'}]; paintHunt(); }""")), [".dbv-group .dbv-fold", ".dbv-tool.on"],
          prepare=companies),
    State("applying, a status picked in its cell", then(applying("apps"), click(".dbv-view tbody tr >> nth=0 >> td[data-key=status]")),
          [".float.dbv-options"], prepare=companies),
    State("applying, a cell typed over", then(applying("apps"), click(".dbv-view tbody tr >> nth=0 >> td[data-key=next]")),
          [".float.dbv-textpanel"], prepare=companies),
    State("applying, a filter being set", then(applying("apps"), click(".dbv-chip[data-key=status]")), [".float.dbv-filterpanel .dbv-check"],
          prepare=companies),
    State("applying, sorts being set", then(applying("apps"), js("() => { DBV.tables.apps.views[0].sorts = [{key: 'deadline', dir: 'desc'}]; paintHunt(); }"),
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
            pg.wait_for_selector(sel, state="visible", timeout=20000)
        for (w, h), lg in LOOKS:
            if lg == lang:
                pg.set_viewport_size({"width": w, "height": h})
                problems += look(pg, f"{st.name} [{lang} {w}]")
    return problems


PLAIN = [s for s in STATES if not s.needs]
CONNECTED = [s for s in STATES if "connected" in s.needs]


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
