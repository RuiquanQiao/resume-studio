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
    page_all.select_option("#version-select", "RES-TECH-EN")
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
    assert pg.input_value("#version-select") == "ALL"
    assert pg.locator("#version-select option").first.inner_text() == "ALL · 全部经历"
    assert pg.locator(".entry .check").count() == 0                  # nothing to tick in the full set
    assert pg.locator("#page-badge").inner_text().strip().startswith("全部经历")
    assert pg.locator("#pages .page.overflow").count() == 0
    pg.hover(".entry[data-id='EXP-003']")
    pg.click(".entry[data-id='EXP-003'] .e-tools button[title='上移']")   # arrows reorder the library
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
    page.click(".entry[data-id='EXP-003'] .e-tools button[title='上移']")
    wait_until(lambda: version(server)["entries"].index("EXP-003") < version(server)["entries"].index("EXP-002"), msg="moved up")
    wait_rendered(page)


@needs_tex
def test_layout_template_and_language(page, server):
    page.click("#layout-toggle")
    page.wait_for_selector("#layout-panel:not(.hidden)")
    page.eval_on_selector("#layout-panel input[data-key=font_size]",
                          "el => { el.value = '11'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    wait_until(lambda: version(server)["layout"].get("font_size") == 11, msg="font size saved")
    page.select_option("#tpl-select", "modern")
    wait_until(lambda: version(server)["template"] == "modern", msg="template saved")
    page.click("#lang-seg button:text('中文')")
    wait_until(lambda: version(server)["lang"] == "zh", msg="language saved")
    wait_rendered(page)
    assert page.locator("#page-badge").inner_text().strip().startswith("1 页")
    # reset one knob back to default
    page.click("#layout-panel .ctl:has(input[data-key=font_size]) button[title='恢复默认']")
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
    page.click("#version-menu button:has-text('新建岗位（空白')")
    page.fill(".modal input", "管培生")
    page.click(".modal button:text('创建')")
    wait_until(lambda: page.input_value("#version-select") == "JOB-001", msg="switched to new job")
    assert page.locator("#version-select option:checked").inner_text() == "管培生"
    assert page.locator(".entry.out").count() == 5                    # blank job: nothing ticked
    page.click("#version-menu-btn")
    page.click("#version-menu button:text('删除当前岗位')")
    page.click(".modal button:text('删除')")
    wait_until(lambda: all(v["id"] != "JOB-001" for v in state(server)["doc"]["versions"]), msg="job deleted")
    wait_until(lambda: page.input_value("#version-select") == "ALL", msg="back on ALL")


def test_job_from_all_starts_full_and_has_own_headline(page_all, server):
    pg = page_all
    pg.click("#version-menu-btn")
    pg.click("#version-menu button:has-text('先勾上全部经历')")
    pg.fill(".modal input", "AI Agent 工程师")
    pg.click(".modal button:text('创建')")
    wait_until(lambda: pg.input_value("#version-select") == "JOB-001", msg="new job")
    assert pg.locator(".entry.out").count() == 0
    pg.click("#version-menu-btn")
    pg.click("#version-menu button:has-text('岗位设置')")
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
    pg.select_option("#editor select", "experience")                  # moving section keeps the entry
    pg.wait_for_selector(".lib-section:has(.t:text('Experience')) .entry[data-id='EXP-003']")
    assert nos("Experience") == ["1"] and nos("Projects") == ["1"]


def test_chat_streams_markdown_tools_and_resumes(page, server, fake_claude):
    page.click(".tab[data-tab=chat]")
    page.wait_for_selector(".chat-welcome")
    page.fill("#chat-input", "你好，帮我看看")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-user:text('你好，帮我看看')")
    page.wait_for_selector(".msg-ai .md strong:text('已经处理')", timeout=10000)
    assert page.locator(".msg-ai .md li").count() == 2
    assert page.locator(".msg-ai .tool .tn").first.inner_text() == "读取"
    page.wait_for_selector("#chat-send:not(.hidden)")
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
    page.click("button:text('让 Claude 润色')")
    page.fill("#polish-box input", "更简洁")
    page.click("#polish-box button")
    page.wait_for_selector(".tab.on:text('对话')")
    page.wait_for_selector(".msg-user:has-text('Paper2Exam')")
    page.wait_for_selector(".card.diff h3:text('Claude 改了这一条')", timeout=15000)
    assert "Solo developer (polished)" in page.inner_text(".card.diff")
    page.click(".card.diff button:text('撤销，恢复到润色前')")
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
    page.select_option("#version-select", "ALL")
    page.wait_for_selector(".lib-hint:not(.job)")
    wait_rendered(page)
    page.screenshot(path=str(ART / f"all-{tag}.png"))
    page.select_option("#version-select", "RES-TECH-EN")
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
    page.wait_for_selector("#chat-send:not(.hidden)")
    page.screenshot(path=str(ART / f"chat-{tag}.png"))
