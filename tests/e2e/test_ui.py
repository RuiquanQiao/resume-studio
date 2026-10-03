"""End-to-end: drive the real UI in Microsoft Edge like a user would.

Run:  .venv/Scripts/python -m pytest tests/e2e
Screenshots for visual review land in tests/artifacts/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

import pytest

from conftest import ROOT, calls

pytestmark = pytest.mark.e2e
ART = ROOT / "tests" / "artifacts"
ART.mkdir(parents=True, exist_ok=True)
needs_tex = pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")


@pytest.fixture(scope="session")
def browser_ctx():
    from playwright.sync_api import sync_playwright

    profile = ROOT / ".cache" / "pw-profile"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), channel="msedge", headless=True,
                                                   viewport={"width": 1440, "height": 900})
        yield ctx
        ctx.close()


@pytest.fixture
def page(page_all):
    """Most tests work on one job target (岗位)."""
    choose(page_all, "#version-select", "RES-TECH-EN")
    page_all.wait_for_selector(".lib-hint.job")
    wait_rendered(page_all)
    return page_all


@pytest.fixture
def page_all(browser_ctx, server):
    """A fresh window: it opens on ALL."""
    pg = browser_ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: errors.append(f"console: {m.text}")
          if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.request.method} {r.url}")
          if r.status in (404, 500) else None)
    if os.environ.get("E2E_TRACE"):
        t0 = time.time()
        pg.on("request", lambda r: print(f"  {time.time() - t0:6.2f} -> {r.method} {r.url}") if "/api/" in r.url else None)
        pg.on("console", lambda m: print(f"  {time.time() - t0:6.2f} console.{m.type}: {m.text}"))
    pg.goto(server.url)
    pg.evaluate("localStorage.clear()")
    pg.reload()
    pg.wait_for_selector(".entry")
    wait_rendered(pg)
    yield pg
    pg.close()
    assert not errors, errors


def choose(pg, trigger, value):
    """Pick an option in one of the app's own dropdowns (there is no native <select>)."""
    pg.click(trigger)
    pg.click(f".float [role=option][data-value='{value}']")
    pg.wait_for_selector(".float", state="detached")


def value_of(pg, trigger):
    return pg.get_attribute(trigger, "data-value")


def wait_rendered(pg, timeout=40000):
    pg.wait_for_function("""() => { const b = document.querySelector('#page-badge');
        return b && !b.classList.contains('busy') && ['ok', 'bad', 'info'].some((c) => b.classList.contains(c)); }""", timeout=timeout)


def wait_until(fn, timeout=8.0, msg="condition"):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {msg}")


def state(server):
    return server.req("GET", "/api/state")[1]


def version(server, vid="RES-TECH-EN"):
    return next(v for v in state(server)["doc"]["versions"] if v["id"] == vid)


def open_entry(pg, eid):
    pg.click(f".entry[data-id='{eid}'] .e-main")
    pg.wait_for_selector(f".entry.on[data-id='{eid}']")
    pg.wait_for_function(f"() => !!document.querySelector('#editor .ed-head')")


def page_src(pg):
    return pg.eval_on_selector("#pages img", "i => i.src")


# ---------------------------------------------------------------- tests
@needs_tex
def test_opens_on_all_the_full_set(page_all, server):
    pg = page_all
    assert value_of(pg, "#version-select") == "ALL"
    assert pg.inner_text("#version-select .sel-label") == "ALL · 全部经历"
    assert pg.locator(".entry .check").count() == 0                  # nothing to tick in the full set
    assert pg.locator("#page-badge").inner_text().strip().startswith("全部经历")
    assert pg.locator("#pages .page.overflow").count() == 0
    pg.hover(".entry[data-id='EXP-003']")
    pg.click(".entry[data-id='EXP-003'] .e-tools button[data-tip='上移']")   # arrows reorder the library
    wait_until(lambda: [e["id"] for e in state(server)["doc"]["entries"]].index("EXP-003")
               < [e["id"] for e in state(server)["doc"]["entries"]].index("EXP-002"), msg="library reordered")


@needs_tex
def test_first_load_and_preview_fits(page, server):
    assert page.locator(".entry").count() == 5
    assert page.locator("#page-badge").inner_text().strip().startswith("1 页")
    size = page.eval_on_selector("#pages img", "i => [i.naturalWidth, i.getBoundingClientRect().width]")
    canvas_w = page.eval_on_selector("#canvas", "c => c.clientWidth")
    assert size[0] > 800 and size[1] <= canvas_w                    # real image, fitted to the panel
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".tab.on").inner_text() == "经历库"


@needs_tex
def test_edit_autosaves_and_rerenders(page, server, data_file):
    open_entry(page, "EXP-002")
    before = page_src(page)
    page.fill("#editor input[data-key=subtitle]", "Founder & builder")
    wait_until(lambda: "Founder & builder" in data_file.read_text(encoding="utf-8"), msg="file saved")
    page.wait_for_selector("#save-status:text('已保存')")
    wait_until(lambda: page_src(page) != before, timeout=30, msg="preview refresh")
    assert "# Resume Studio sample data." in data_file.read_text(encoding="utf-8")


@needs_tex
def test_include_toggle_and_reorder(page, server):
    page.click(".entry[data-id='EXP-003'] .check")
    wait_until(lambda: "EXP-003" not in version(server)["entries"], msg="removed from version")
    assert "out" in page.get_attribute(".entry[data-id='EXP-003']", "class")
    page.click(".entry[data-id='EXP-003'] .check")
    wait_until(lambda: version(server)["entries"][-1] == "EXP-003", msg="re-added at the end")
    page.hover(".entry[data-id='EXP-003']")
    page.click(".entry[data-id='EXP-003'] .e-tools button[data-tip='上移']")
    wait_until(lambda: version(server)["entries"].index("EXP-003") < version(server)["entries"].index("EXP-002"), msg="moved up")
    wait_rendered(page)


@needs_tex
def test_layout_template_and_language(page, server):
    page.click("#layout-toggle")
    page.wait_for_selector("#layout-panel:not(.hidden)")
    page.eval_on_selector("#layout-panel input[data-key=font_size]",
                          "el => { el.value = '11'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    wait_until(lambda: version(server)["layout"].get("font_size") == 11, msg="font size saved")
    choose(page, "#tpl-select", "modern")
    wait_until(lambda: version(server)["template"] == "modern", msg="template saved")
    page.click("#lang-seg button:text('中文')")
    wait_until(lambda: version(server)["lang"] == "zh", msg="language saved")
    wait_rendered(page)
    assert page.locator("#page-badge").inner_text().strip().startswith("1 页")
    # reset one knob back to default
    page.click("#layout-panel .ctl:has(input[data-key=font_size]) button[data-tip='恢复默认']")
    wait_until(lambda: "font_size" not in version(server)["layout"], msg="font size reset")


def test_external_edit_syncs_open_form(page, server, data_file):
    open_entry(page, "EXP-002")
    text = data_file.read_text(encoding="utf-8").replace("Solo developer", "Edited by Claude")
    data_file.write_text(text, encoding="utf-8")
    page.wait_for_function("() => document.querySelector('#editor input[data-key=subtitle]').value === 'Edited by Claude'", timeout=5000)


def test_conflict_banner_when_both_edit(page, server, data_file):
    open_entry(page, "EXP-002")
    page.type("#editor input[data-key=location]", "Canberra")        # unsaved local change
    text = data_file.read_text(encoding="utf-8").replace("Solo developer", "Changed elsewhere")
    data_file.write_text(text, encoding="utf-8")                      # same entry changes on disk
    page.wait_for_selector(".banner.warn", timeout=5000)
    page.click(".banner.warn button:text('载入最新版本')")
    page.wait_for_selector(".banner.warn", state="detached")
    assert page.input_value("#editor input[data-key=subtitle]") == "Changed elsewhere"
    assert page.input_value("#editor input[data-key=location]") == ""


def test_add_and_delete_entry(page, server, data_file):
    page.click(".lib-section:has(.t:text('Projects')) .lib-head button")
    page.wait_for_selector(".entry.on[data-id='EXP-006']")
    page.wait_for_selector(".ed-head .id:text('Projects #3')")
    assert page.evaluate("document.activeElement.dataset.key") == "title"
    page.keyboard.type("New Project")
    wait_until(lambda: "New Project" in data_file.read_text(encoding="utf-8"), msg="title saved")
    assert "EXP-006" in version(server)["entries"]                    # new entries join the current version
    page.click("button:text('删除这条经历')")
    page.click(".modal button:text('删除')")
    wait_until(lambda: "EXP-006" not in data_file.read_text(encoding="utf-8"), msg="entry deleted")
    page.wait_for_selector(".ed-head h1:text('个人信息')")


def test_profile_contacts_and_sections(page, server, data_file):
    page.click(".nav-row:text('个人信息')")
    page.click("button:text('添加联系方式')")
    rows = page.locator(".card:has(h3:text('联系方式')) .lr")
    last = rows.nth(rows.count() - 1)
    last.locator("input").nth(0).fill("linkedin")
    last.locator("input").nth(1).fill("linkedin.com/in/alex")
    wait_until(lambda: "linkedin.com/in/alex" in data_file.read_text(encoding="utf-8"), msg="contact saved")
    page.click(".nav-row:text('板块与设置')")
    page.click("button:text('添加板块')")
    wait_until(lambda: any(s["id"].startswith("section-") for s in state(server)["doc"]["sections"]), msg="section added")


def test_jobs_create_switch_delete(page, server):
    page.click("#version-menu-btn")
    page.click(".float .menu-item:has(.mi-hint:text('空白，自己勾选'))")
    page.fill(".modal input", "管培生")
    page.click(".modal button:text('创建')")
    wait_until(lambda: value_of(page, "#version-select") == "JOB-001", msg="switched to new job")
    assert page.inner_text("#version-select .sel-label") == "管培生"
    assert page.locator(".entry.out").count() == 5                    # blank job: nothing ticked
    page.click("#version-menu-btn")
    page.click(".float .menu-item:has-text('删除当前岗位')")
    page.click(".modal button:text('删除')")
    wait_until(lambda: all(v["id"] != "JOB-001" for v in state(server)["doc"]["versions"]), msg="job deleted")
    wait_until(lambda: value_of(page, "#version-select") == "ALL", msg="back on ALL")


def test_job_from_all_starts_full_and_has_own_headline(page_all, server):
    pg = page_all
    pg.click("#version-menu-btn")
    pg.click(".float .menu-item:has-text('先勾上全部经历')")
    pg.fill(".modal input", "AI Agent 工程师")
    pg.click(".modal button:text('创建')")
    wait_until(lambda: value_of(pg, "#version-select") == "JOB-001", msg="new job")
    assert pg.locator(".entry.out").count() == 0
    pg.click("#version-menu-btn")
    pg.click(".float .menu-item:has-text('岗位设置')")
    pg.fill("#editor input[data-key=headline]", "AI Agent Engineer")
    wait_until(lambda: version(server, "JOB-001").get("headline") in ("AI Agent Engineer", {"en": "AI Agent Engineer"}), msg="headline saved")
    assert "headline" not in state(server)["doc"]["profile"]


def test_contacts_can_be_hidden_per_job(page, server):
    page.click(".nav-row:text('个人信息')")
    rows = page.locator(".card:has(h3:text('联系方式')) .lr")
    rows.nth(2).locator(".check").click()                             # phone
    wait_until(lambda: version(server).get("hide_contacts") == ["phone"], msg="hidden in this job")
    page.wait_for_selector(".card:has(h3:text('联系方式')) .lr >> nth=2 >> .check:not(.on)")


def test_numbers_are_per_section_and_ids_stay_hidden(page_all):
    pg = page_all
    nos = lambda sec: pg.locator(f".lib-section:has(.t:text('{sec}')) .e-no").all_inner_texts()
    assert nos("Education") == ["1"] and nos("Projects") == ["1", "2"] and nos("Skills") == ["1", "2"]
    open_entry(pg, "EXP-003")
    assert pg.inner_text(".ed-head .id") == "Projects #2"
    assert "EXP-" not in pg.inner_text("#library") + pg.inner_text("#editor")
    choose(pg, "#editor .select[data-key=section]", "experience")      # moving section keeps the entry
    pg.wait_for_selector(".lib-section:has(.t:text('Experience')) .entry[data-id='EXP-003']")
    assert nos("Experience") == ["1"] and nos("Projects") == ["1"]


def test_chat_streams_markdown_tools_and_resumes(page, server, fake_claude):
    page.click(".tab[data-tab=chat]")
    page.wait_for_selector(".chat-welcome")
    page.fill("#chat-input", "你好，帮我看看")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-user .msg-text:text('你好，帮我看看')")
    page.wait_for_selector(".msg-ai .md strong:text('已经处理')", timeout=10000)
    assert page.locator(".msg-ai .md li").count() == 2
    assert page.locator(".msg-ai .tool .tn").first.inner_text() == "读取"
    page.wait_for_selector("#chat-stop.hidden", state="attached")
    page.fill("#chat-input", "继续")
    page.keyboard.press("Enter")
    wait_until(lambda: len(calls(fake_claude)) == 2, msg="second call")
    page.wait_for_function("() => document.querySelectorAll('.msg-ai').length === 2", timeout=10000)
    second = calls(fake_claude)[1]["argv"]
    assert "--resume" in second
    assert "Claude 知道你正在看" in page.inner_text("#chat-context")


def test_polish_shows_diff_and_undo_restores(page, server, data_file, fake_claude, monkeypatch):
    original = data_file.read_text(encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", json.dumps({"file": str(data_file), "from": "Solo developer", "to": "Solo developer (polished)"}))
    open_entry(page, "EXP-002")
    page.click(".ai-btn")
    page.click(".float .menu-item:has-text('润色')")
    page.fill("#polish-box input", "更简洁")
    page.click("#polish-box button")
    page.wait_for_selector(".tab.on:text('对话')")
    page.wait_for_selector(".msg-user:has-text('Paper2Exam')")
    page.wait_for_selector(".card.diff h3:text('Claude 改了这一条')", timeout=15000)
    assert "Solo developer (polished)" in page.inner_text(".card.diff")
    page.click(".card.diff button:text('撤销，恢复到改动前')")
    wait_until(lambda: "Solo developer (polished)" not in data_file.read_text(encoding="utf-8"), msg="undo applied")
    assert "subtitle: {en: Solo developer, zh: 独立开发}" in data_file.read_text(encoding="utf-8")
    assert original.splitlines()[0] == data_file.read_text(encoding="utf-8").splitlines()[0]


@needs_tex
def test_export_and_overflow_warning(page, server, data_file):
    page.click("#export-btn")
    page.wait_for_selector(".toast.show:has-text('已导出')", timeout=30000)
    assert (data_file.parent / "exports" / "RES-TECH-EN.pdf").exists()
    s = state(server)
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    e["bullets"] = [{"en": "Designed and shipped a feature used by real people every single day " * 2}] * 45
    server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})
    page.wait_for_function("() => document.querySelector('#page-badge').innerText.includes('超出')", timeout=40000)
    assert page.locator("#pages .page.overflow").count() >= 1


def test_gutter_resize_persists(page, server):
    box = page.locator(".gutter[data-gutter=left]").bounding_box()
    before = page.locator(".pane.left").bounding_box()["width"]
    page.mouse.move(box["x"] + 2, box["y"] + 300)
    page.mouse.down()
    page.mouse.move(box["x"] + 90, box["y"] + 300, steps=6)
    page.mouse.up()
    after = page.locator(".pane.left").bounding_box()["width"]
    assert after > before + 60
    page.reload()
    page.wait_for_selector(".entry")
    assert abs(page.locator(".pane.left").bounding_box()["width"] - after) < 3
    page.dblclick(".gutter[data-gutter=left]")
    assert abs(page.locator(".pane.left").bounding_box()["width"] - before) < 3


@needs_tex
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [(1280, 800), (1920, 1080)])
def test_screenshots_for_review(page, server, fake_claude, scheme, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme)
    open_entry(page, "EXP-002")
    wait_rendered(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    tag = f"{scheme}-{size[0]}"
    page.screenshot(path=str(ART / f"editor-{tag}.png"))
    choose(page, "#version-select", "ALL")
    page.wait_for_selector(".lib-hint:not(.job)")
    wait_rendered(page)
    page.screenshot(path=str(ART / f"all-{tag}.png"))
    choose(page, "#version-select", "RES-TECH-EN")
    page.wait_for_selector(".lib-hint.job")
    wait_rendered(page)
    page.click("#layout-toggle")
    page.screenshot(path=str(ART / f"layout-{tag}.png"))
    page.click("#layout-toggle")
    page.click(".tab[data-tab=chat]")
    page.screenshot(path=str(ART / f"chat-empty-{tag}.png"))
    page.fill("#chat-input", "帮我看看这一条")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-ai .md strong", timeout=10000)
    page.wait_for_selector("#chat-stop.hidden", state="attached")
    page.screenshot(path=str(ART / f"chat-{tag}.png"))


# ---------------------------------------------------------------- no platform widgets anywhere
NATIVE = """() => ({
    selects: document.querySelectorAll('select').length,
    colors: document.querySelectorAll('input[type=color]').length,
    titles: [...document.querySelectorAll('[title]')].map((e) => e.outerHTML.slice(0, 80)),
})"""


def no_native(pg):
    found = pg.evaluate(NATIVE)
    assert found["selects"] == 0 and found["colors"] == 0, found
    assert not found["titles"], found["titles"]                       # every tooltip is the app's own


@needs_tex
def test_no_native_controls_anywhere(page, server, fake_claude):
    no_native(page)
    open_entry(page, "EXP-002")
    no_native(page)
    page.click("#layout-toggle")
    no_native(page)
    page.click(".nav-row:text('板块与设置')")
    no_native(page)
    page.click(".tab[data-tab=chat]")
    no_native(page)


def test_custom_select_mouse_and_keyboard(page, server):
    page.click("#version-select")
    lb = page.locator(".float.listbox")
    assert lb.locator(".lb-group").first.inner_text() == "岗位"
    assert lb.locator("[role=option][aria-selected=true]").get_attribute("data-value") == "RES-TECH-EN"
    assert "中文" in lb.locator("[data-value='RES-TECH-ZH'] .lb-hint").inner_text()   # language + template at a glance
    page.keyboard.press("Escape")
    page.wait_for_selector(".float", state="detached")
    assert page.evaluate("document.activeElement.id") == "version-select"            # focus returns to the trigger
    page.keyboard.press("ArrowDown")                                                  # opens
    page.wait_for_selector(".float.listbox")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    wait_until(lambda: value_of(page, "#version-select") == "RES-TECH-ZH", msg="picked with the keyboard")
    page.click("#version-select")
    page.mouse.click(5, 880)                                                          # click outside closes
    page.wait_for_selector(".float", state="detached")


def test_tooltips_are_the_apps_own(page):
    page.hover("#version-menu-btn")
    page.wait_for_selector(".tip.show:text('岗位操作')")
    page.mouse.move(700, 600)
    page.wait_for_selector(".tip.show", state="detached", timeout=2000) if page.locator(".tip.show").count() else None


def test_right_click_menu_in_text_fields(page):
    open_entry(page, "EXP-002")
    field = page.locator("#editor input[data-key=subtitle]")
    field.click(button="right")
    menu = page.locator(".float.menu")
    assert [t.strip() for t in menu.locator(".mi-label").all_inner_texts()] == ["剪切", "复制", "粘贴", "全选"]
    menu.locator(".menu-item:has-text('全选')").click()
    assert page.evaluate("(() => { const el = document.activeElement; return el.selectionEnd - el.selectionStart; })()") == len("Solo developer")


@needs_tex
def test_layout_colour_picker_and_paper(page, server):
    page.click("#layout-toggle")
    page.click("#ctl-accent")
    page.click(".color-pop .cp-sw[aria-label='酒红']")
    wait_until(lambda: version(server)["layout"].get("accent") == "7A1F2B", msg="accent saved")
    page.click("#ctl-accent")
    page.fill(".color-pop .cp-hex", "#12ab34")
    page.keyboard.press("Enter")
    wait_until(lambda: version(server)["layout"].get("accent") == "12AB34", msg="typed hex saved")
    page.click("#ctl-paper button:text('Letter')")
    wait_until(lambda: version(server)["layout"].get("paper") == "letter", msg="paper saved")
    page.click("#ctl-accent")
    page.click(".color-pop .cp-reset")
    wait_until(lambda: "accent" not in version(server)["layout"], msg="accent reset")


# ---------------------------------------------------------------- bilingual + one line per bullet
LONG = "Added an evaluation harness that scores every generated question against the source paper and flags hallucinated facts before export"


def add_long_bullet(server, page=None):
    s = state(server)
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    e["bullets"].append({"en": LONG})
    server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})
    if page is not None:  # an edit made elsewhere: let the window pick it up before touching that entry
        page.wait_for_function("() => (entryById('EXP-002').bullets || []).length === 3")


@needs_tex
def test_line_chips_and_issue_strip(page, server):
    add_long_bullet(server, page)
    open_entry(page, "EXP-002")
    page.wait_for_selector("#bullets .line-chip.long", timeout=40000)
    chips = page.locator("#bullets .line-chip").all_inner_texts()
    assert chips == ["1 行", "1 行", "2 行"]
    assert "1 条要点超过一行" in page.inner_text("#bullet-notes")
    page.click("#issues .issue.warn")                                         # the strip lists it, and jumps there
    page.click(".float .menu-item:has-text('第 3 条')")
    wait_until(lambda: page.evaluate("document.activeElement.dataset.bullet") == "2", msg="focused the long bullet")


@needs_tex
def test_writing_the_other_language(page, server, data_file):
    add_long_bullet(server, page)
    open_entry(page, "EXP-002")
    page.wait_for_selector(".entry[data-id='EXP-002'] .e-gap:text('缺中文')")
    page.click(".ed-head .seg button:text('中文')")
    third = page.locator("#bullets .bullet").nth(2)
    assert "missing" in third.get_attribute("class")
    assert third.locator("textarea").get_attribute("placeholder").startswith("EN：Added an evaluation")
    assert third.locator(".line-chip").inner_text() == "缺中文"
    page.wait_for_function("() => document.querySelectorAll('#bullets .line-chip.ok').length === 2", timeout=40000)  # zh measured too
    third.hover()
    third.locator("button[data-tip^='中文简历不要这一条']").click()
    entry = lambda: next(x for x in state(server)["doc"]["entries"] if x["id"] == "EXP-002")  # noqa: E731
    wait_until(lambda: entry()["bullets"][2] == {"en": LONG, "zh": ""}, msg="hidden in zh")
    assert page.locator("#bullets .bullet").nth(2).locator(".line-chip").inner_text() == "不显示"
    wait_until(lambda: page.locator(".entry[data-id='EXP-002'] .e-gap").count() == 0, msg="nothing missing any more")


def test_translate_button_sends_a_native_rewrite_request(page, server, fake_claude):
    add_long_bullet(server, page)
    open_entry(page, "EXP-002")
    page.click(".ed-head .seg button:text('中文')")
    page.click("#bullet-notes button:has-text('让 Claude 写中文版')")
    page.wait_for_selector(".msg-user.is-action .msg-action:text('写另一种语言')")
    wait_until(lambda: len(calls(fake_claude)) == 1, msg="claude called")
    assert "不是逐句翻译" in calls(fake_claude)[0]["stdin"] and "--lang zh" in calls(fake_claude)[0]["stdin"]
    page.wait_for_selector(".card.diff", timeout=15000)                       # every AI action gets diff + undo


# ---------------------------------------------------------------- project folders
def make_repo(path):
    import subprocess
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)


def test_project_folder_pick_sync_and_update(page, server, data_file, fake_claude, monkeypatch, tmp_path):
    import subprocess
    repo = tmp_path / "demo"
    make_repo(repo)
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps([str(repo)]))
    open_entry(page, "EXP-002")
    page.click("#evidence button:has-text('选择项目文件夹')")
    row = page.locator("#evidence .ev-row")
    row.wait_for()
    assert row.locator(".ev-name b").inner_text() == "demo"
    wait_until(lambda: str(repo).replace("\\", "/") in data_file.read_text(encoding="utf-8"), msg="path saved")
    page.wait_for_selector("#evidence .ev-chip:text('Claude 还没读过')")
    page.click("#evidence .sync-bar button:text('标记为已同步')")
    page.wait_for_selector("#evidence .ev-chip.ok:text('已同步')")

    (repo / "eval.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "feat: evaluation harness"], check=True)
    page.evaluate("window.dispatchEvent(new Event('focus'))")                 # coming back to the window re-checks
    page.wait_for_selector("#evidence .ev-chip.new:text('1 个新提交')", timeout=8000)
    page.wait_for_selector(".entry[data-id='EXP-002'] .e-dot")
    page.click("#evidence .ev-more")
    assert "feat: evaluation harness" in page.inner_text("#evidence .change-detail")
    page.click("#evidence .sync-bar button:has-text('让 Claude 按新进展更新')")
    page.wait_for_selector(".msg-action:text('按项目更新')")
    page.wait_for_selector(".card.diff", timeout=15000)
    page.wait_for_selector(".entry[data-id='EXP-002'] .e-dot", state="detached", timeout=8000)   # read, so in sync again
    assert "feat: evaluation harness" in calls(fake_claude)[0]["stdin"]


def test_project_roots_setting(page, server, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps(["E:/Forge"]))
    page.click(".nav-row:text('板块与设置')")
    page.click(".card:has(h3:text('我的项目文件夹')) button:has-text('选择文件夹')")
    wait_until(lambda: state(server)["doc"]["settings"].get("project_roots") == ["E:/Forge"], msg="root saved")
    page.wait_for_selector(".card:has(h3:text('我的项目文件夹')) .ev-path:text('E:/Forge')")


def open_chat_tab(pg):
    pg.click(".tab[data-tab=chat]")
    pg.wait_for_function("() => ui.claude && ui.claude.info")                 # Claude Code's own lists arrived
    pg.wait_for_selector("#chat-model-btn .lbl:text('Sonnet 5')")


def argv_value(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def test_model_effort_and_mode_chips(page, server, fake_claude):
    """The chip shows the model; clicking it is how you pick one (like Claude Code), effort and mode beside it."""
    open_chat_tab(page)
    page.click("#chat-model-btn")
    pk = page.locator(".float.picker")
    assert pk.get_attribute("data-side") == "top"                              # opens upward from the composer
    labels = pk.locator(".pk-item .pk-label").all_inner_texts()[:4]
    assert [l.split()[0] + " " + l.split()[1][:3] for l in labels] == ["Opus 5.5", "Sonnet 5.5", "Fable 5.1", "Haiku 4.5"]
    assert "需要额外额度" in labels[2]
    page.keyboard.press("1")                                                    # number keys pick, like Claude Code
    page.wait_for_selector("#chat-model-btn .lbl:text('Opus 5.5')")
    page.click("#chat-effort-btn")
    page.click(".float .pk-item:has-text('High')")
    page.wait_for_selector("#chat-effort-btn .lbl:text('High')")
    page.click("#chat-mode-btn")
    page.click(".float .pk-item:has-text('计划模式')")
    page.wait_for_selector("#chat-mode-btn[data-mode=plan]")
    page.fill("#chat-input", "你好")
    page.keyboard.press("Enter")
    wait_until(lambda: len(calls(fake_claude)) == 1, msg="sent")
    argv = calls(fake_claude)[0]["argv"]
    assert (argv_value(argv, "--model"), argv_value(argv, "--effort"), argv_value(argv, "--permission-mode")) == ("claude-opus-5-5", "high", "plan")
    page.wait_for_selector("#chat-stop.hidden", state="attached")

    page.click("#chat-input")
    page.keyboard.press("Shift+Tab")                                            # cycles the mode, like the terminal
    page.wait_for_selector("#chat-mode-btn[data-mode=default]")
    page.click("#chat-model-btn")
    page.click(".float .pk-item:has-text('Haiku 4.5')")
    page.wait_for_selector("#chat-effort-btn:disabled")                         # Haiku has no effort levels
    assert page.inner_text("#chat-effort-btn .lbl") == "—"
    page.click("#chat-model-btn")
    page.click(".float .pk-more")
    page.fill(".float .pk-custom input", "claude-opus-4-1")
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-model-btn .lbl:text('Opus 4.1')")
    meta = server.req("GET", "/api/chats")[1]["chats"][0]
    assert (meta["model_choice"], meta["mode"]) == ("claude-opus-4-1", "default")
    page.click("#chat-new")                                                     # a new chat starts with the last choice
    page.wait_for_selector("#chat-model-btn .lbl:text('Opus 4.1')")


def test_permission_card_in_the_chat(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "PERM 写个文件")
    page.keyboard.press("Enter")
    card = page.locator(".ask.ask-perm.pending")
    card.wait_for()
    assert "Claude 想要写入文件" in card.inner_text() and "notes.md" in card.inner_text()
    assert "+ hello" in card.locator(".ask-diff").inner_text()
    page.wait_for_selector("#chat-dot.wait")                                    # the tab says Claude is waiting for you
    card.locator("button:has-text('允许，并切到「自动接受编辑」')").click()
    page.wait_for_selector(".ask.ask-perm.done.allowed")
    page.wait_for_selector(".msg-ai .md:has-text('已写入。')", timeout=10000)
    page.wait_for_selector("#chat-mode-btn[data-mode=acceptEdits]")            # "always" switched the mode
    assert page.locator("#chat-dot.wait").count() == 0

    page.fill("#chat-input", "PERM 再写一次")
    page.keyboard.press("Enter")
    card = page.locator(".ask.ask-perm.pending")
    card.wait_for()
    card.locator("button:has-text('拒绝并说明')").click()
    card.locator("input.ask-input").fill("先别写")
    card.locator("input.ask-input").press("Enter")
    page.wait_for_selector(".ask.ask-perm.done.denied")
    page.wait_for_selector(".msg-ai .md:has-text('好的，不写了。')", timeout=10000)


def test_question_and_plan_cards_in_the_chat(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "QUESTION 颜色")
    page.keyboard.press("Enter")
    card = page.locator(".ask.ask-q.pending")
    card.wait_for()
    assert card.locator("button:has-text('提交回答')").is_disabled()          # nothing picked yet
    card.locator(".q-opt:has-text('Blue')").click()
    card.locator("button:has-text('提交回答')").click()
    page.wait_for_selector(".msg-ai .md:has-text('你选了 Blue。')", timeout=10000)
    assert "Blue" in page.inner_text(".ask.ask-q.done")

    page.fill("#chat-input", "PLAN 改一下")
    page.keyboard.press("Enter")
    card = page.locator(".ask.ask-plan.pending")
    card.wait_for()
    assert card.locator(".plan-md li").count() == 2                             # the plan renders as markdown
    card.locator("button:has-text('批准，自动接受编辑')").click()
    page.wait_for_selector(".ask.ask-plan.done.approved")
    page.wait_for_selector(".msg-ai .md:has-text('开始执行计划。')", timeout=10000)
    page.wait_for_selector("#chat-mode-btn[data-mode=acceptEdits]")


def test_queue_stop_tasks_and_fork(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "TASKS 开始")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-ai .md:has-text('已经处理')", timeout=10000)
    wait_until(lambda: server.req("GET", "/api/chats")[1]["chats"][0]["tasks"][0]["status"] == "completed", msg="task list")
    page.wait_for_selector("#chat-stop.hidden", state="attached", timeout=10000)

    page.fill("#chat-input", "PERM 第一条")
    page.keyboard.press("Enter")
    page.locator(".ask.ask-perm.pending").wait_for()
    page.fill("#chat-input", "排队的第二条")                                     # typing while Claude works: queued
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-queue .cq-text:text('排队的第二条')")
    assert page.input_value("#chat-input") == ""
    page.click(".ask.ask-perm.pending button:has-text('允许')")
    page.wait_for_selector("#chat-queue.hidden", state="attached", timeout=10000)
    page.wait_for_selector(".msg-user .msg-text:text('排队的第二条')")
    page.wait_for_selector("#chat-stop.hidden", state="attached", timeout=10000)
    assert [c["stdin"] for c in calls(fake_claude)][-2:] == ["PERM 第一条", "排队的第二条"]

    page.fill("#chat-input", "SLOW 慢慢来")
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-stop:not(.hidden)")
    page.wait_for_selector(".msg-ai .md:has-text('开始处理…')")
    page.click("#chat-stop")
    page.wait_for_selector(".msg-note:text('已停止')", timeout=10000)

    n = page.locator(".msg-user-wrap").count()
    target = page.locator(".msg-user-wrap").nth(n - 2)                         # "排队的第二条"
    target.hover()
    target.locator("button[aria-label^='从这里分叉']").click()
    page.wait_for_selector("#chat-select .sel-label:text-matches('（分叉）')")
    assert page.input_value("#chat-input") == "排队的第二条"                   # ready to rewrite and resend
    assert page.locator(".msg-user-wrap").count() == n - 2


def test_slash_commands_and_attachments(page, server, fake_claude, monkeypatch):
    open_chat_tab(page)
    page.click("#chat-input")
    page.keyboard.type("/co")
    menu = page.locator(".float.slash-menu")
    menu.wait_for()
    names = menu.locator(".sl-name").all_inner_texts()
    assert names[0].startswith("/compact") and any(n.startswith("/context") for n in names)
    page.keyboard.type("nt")
    page.wait_for_function("() => document.querySelectorAll('.slash-menu .sl-item').length === 1")
    page.keyboard.press("Enter")                                                # no arguments: runs at once
    page.wait_for_selector(".msg-ai .md h2:text('Context Usage')", timeout=10000)
    assert calls(fake_claude)[-1]["stdin"] == "/context"

    page.wait_for_selector("#chat-stop.hidden", state="attached")
    page.fill("#chat-input", "/effort max")                                     # handled by the window itself
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-effort-btn .lbl:text('Max')")
    assert len(calls(fake_claude)) == 1

    # a pasted screenshot and a picked file
    page.evaluate("""() => {
        const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
        addBrowserFiles([new File([bytes], 'shot.png', { type: 'image/png' })]);
    }""")
    page.wait_for_selector("#chat-files .cf-item.img img")
    tmp = ROOT / ".cache" / "tmp" / "附件.pdf"
    tmp.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps([str(tmp)]))
    page.click("#chat-attach")
    page.wait_for_selector("#chat-files .cf-name:text('附件.pdf')")
    page.fill("#chat-input", "看看这两个")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-user .mf-img img")
    page.wait_for_selector(".msg-user .mf-file:has-text('附件.pdf')")
    wait_until(lambda: calls(fake_claude)[-1]["content"] == ["text", "image"], msg="image sent inline")
    assert str(tmp).replace("\\", "/") in calls(fake_claude)[-1]["stdin"]
    page.wait_for_function("() => [...document.querySelectorAll('.msg-user .mf-img img')].every((i) => i.complete && i.naturalWidth > 0)")
    assert page.locator("#chat-files.hidden").count() == 1


def test_context_ring_and_limits(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "你好")
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-stop.hidden", state="attached", timeout=10000)
    page.wait_for_function("() => (document.querySelector('#chat-ctx-btn').dataset.tip || '').includes('20%')")
    page.click("#chat-ctx-btn")
    pop = page.locator(".float.cx-pop")
    pop.wait_for()
    text = pop.inner_text()
    assert "40.1k / 200k" in text and "5 小时内" in text and "12%" in text and "34%" in text and "Claude Pro" in text


def shot_float(pg, name):
    pg.wait_for_selector(".float.in")
    pg.wait_for_timeout(200)                                   # let the fade-in finish
    assert pg.locator(".float").count() == 1, f"{name}: the panel closed by itself"
    pg.screenshot(path=str(ART / f"{name}.png"))


@needs_tex
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_screenshots_of_new_parts(page, server, fake_claude, monkeypatch, tmp_path, scheme):
    page.emulate_media(color_scheme=scheme)
    repo = tmp_path / "Paper2Exam"
    make_repo(repo)
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps([str(repo)]))
    add_long_bullet(server, page)
    open_entry(page, "EXP-002")
    page.click("#evidence button:has-text('选择项目文件夹')")
    page.wait_for_selector("#evidence .ev-chip:text('Claude 还没读过')")
    page.wait_for_selector("#bullets .line-chip.long", timeout=40000)
    page.screenshot(path=str(ART / f"new-entry-{scheme}.png"))
    page.locator("#evidence").scroll_into_view_if_needed()
    page.screenshot(path=str(ART / f"new-evidence-{scheme}.png"))
    page.click(".ai-btn")
    shot_float(page, f"new-ai-menu-{scheme}")
    page.keyboard.press("Escape")
    page.click(".ed-head .seg button:text('中文')")
    page.wait_for_function("() => document.querySelectorAll('#bullets .line-chip.ok').length === 2", timeout=40000)
    page.locator("#bullets").scroll_into_view_if_needed()
    page.screenshot(path=str(ART / f"new-zh-{scheme}.png"))
    page.click("#version-select")
    shot_float(page, f"new-select-{scheme}")
    page.keyboard.press("Escape")
    page.click("#issues .issue.warn")
    shot_float(page, f"new-issues-{scheme}")
    page.keyboard.press("Escape")
    page.click("#layout-toggle")
    page.click("#ctl-accent")
    shot_float(page, f"new-color-{scheme}")
    page.keyboard.press("Escape")
    open_chat_tab(page)
    page.click("#chat-model-btn")
    shot_float(page, f"new-model-{scheme}")
    page.keyboard.press("Escape")
    page.click("#chat-mode-btn")
    shot_float(page, f"new-mode-{scheme}")
    page.keyboard.press("Escape")
    page.fill("#chat-input", "PERM 写个文件")
    page.keyboard.press("Enter")
    page.locator(".ask.ask-perm.pending").wait_for()
    page.screenshot(path=str(ART / f"new-permission-{scheme}.png"))
    page.click(".ask.ask-perm.pending button:has-text('允许')")
    page.wait_for_selector("#chat-stop.hidden", state="attached", timeout=10000)
    page.fill("#chat-input", "QUESTION 颜色")
    page.keyboard.press("Enter")
    page.locator(".ask.ask-q.pending").wait_for()
    page.screenshot(path=str(ART / f"new-question-{scheme}.png"))
    page.click(".ask.ask-q.pending .q-opt:has-text('Red')")
    page.click(".ask.ask-q.pending button:has-text('提交回答')")
    page.wait_for_selector("#chat-stop.hidden", state="attached", timeout=10000)
    page.fill("#chat-input", "PLAN 改一下")
    page.keyboard.press("Enter")
    page.locator(".ask.ask-plan.pending").wait_for()
    page.screenshot(path=str(ART / f"new-plan-{scheme}.png"))
    page.click(".ask.ask-plan.pending button:has-text('批准，编辑前问我')")
    page.wait_for_selector("#chat-stop.hidden", state="attached", timeout=10000)
    page.fill("#chat-input", "TASKS SLOW")
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-tasks:not(.hidden)")
    page.fill("#chat-input", "排队的一条")
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-queue:not(.hidden)")
    page.fill("#chat-input", "/")
    page.locator(".float.slash-menu").wait_for()
    page.wait_for_timeout(200)
    page.screenshot(path=str(ART / f"new-slash-{scheme}.png"))
    page.keyboard.press("Escape")
    page.click("#chat-ctx-btn")
    shot_float(page, f"new-context-{scheme}")
    page.keyboard.press("Escape")
    page.click("#chat-stop")
