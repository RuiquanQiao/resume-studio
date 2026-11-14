"""End-to-end: drive the real UI in Microsoft Edge like a user would.

Run:  .venv/Scripts/python -m pytest tests/e2e
Screenshots for visual review land in tests/artifacts/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT, calls, edge_args

pytestmark = pytest.mark.e2e
ART = ROOT / "tests" / "artifacts"
ART.mkdir(parents=True, exist_ok=True)
needs_tex = pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")


@pytest.fixture(scope="session")
def browser_ctx():
    from playwright.sync_api import sync_playwright

    # one browser profile per group: scripts/check.py runs ui, ai_review and session_ui side by side
    group = os.environ.get("STUDIO_TEST_GROUP")
    profile = ROOT / ".cache" / ("pw-profile-" + group if group else "pw-profile")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), channel="msedge", headless=True,
                                                   args=edge_args(), viewport={"width": 1440, "height": 900})
        yield ctx
        ctx.close()


@pytest.fixture
def page(page_all):
    """Most tests work on one subset (子集)."""
    choose(page_all, "#subset-select", "RES-TECH-EN")
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
    # what the last test left (a company page it had open, say) is cleared before the app's first script reads it:
    # cleared after the first load, the app had already asked for that company, gone with the last test's data (a 404).
    # Once per tab (sessionStorage), so a test that reloads keeps what it stored.
    pg.add_init_script("if (!sessionStorage.getItem('e2e-fresh')) { localStorage.clear(); sessionStorage.setItem('e2e-fresh', '1'); }")
    pg.goto(server.url)
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


def subset(server, vid="RES-TECH-EN"):
    return next(v for v in state(server)["doc"]["subsets"] if v["id"] == vid)


def open_entry(pg, eid):
    """Pick an entry in the outline: the document scrolls to its card, which becomes the one being edited (#active)."""
    pg.click(f"#library .entry[data-id='{eid}'] .e-main")
    pg.wait_for_selector(f"#library .entry.on[data-id='{eid}']")
    pg.wait_for_selector(f"#active.doc-card[data-key='entry:{eid}'] .ed-head")


def open_facts(pg):
    """Unfold the fact layer and project folders of the card being edited."""
    if not pg.is_visible("#active .fact-fold textarea"):
        pg.click("#active .fact-fold > summary")
    pg.wait_for_selector("#evidence", state="visible")


def page_src(pg):
    return pg.eval_on_selector("#pages img", "i => i.src")


# ---------------------------------------------------------------- tests
@needs_tex
def test_opens_on_all_the_full_set(page_all, server):
    pg = page_all
    assert value_of(pg, "#subset-select") == "ALL"
    assert pg.inner_text("#subset-select .sel-label") == "ALL · 全部经历"
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
    assert page.is_visible("#chat") and page.is_visible("#library")   # the chat on the left, the outline beside the document


@needs_tex
def test_edit_autosaves_and_rerenders(page, server, data_file):
    open_entry(page, "EXP-002")
    before = page_src(page)
    page.fill("#active input[data-key=subtitle]", "Founder & builder")
    wait_until(lambda: "Founder & builder" in data_file.read_text(encoding="utf-8"), msg="file saved")
    page.wait_for_selector("#save-status:text('已保存')")
    wait_until(lambda: page_src(page) != before, timeout=30, msg="preview refresh")
    assert "# Resume Studio sample data." in data_file.read_text(encoding="utf-8")


def test_the_document_edits_every_card_in_place(page, server, data_file):
    """docs/LAYOUT.md: Profile and every entry are one document; the outline is a rail that can fold into a strip."""
    pg = page
    assert pg.locator("#editor .doc-card").count() == 1 + len(subset(server)["entries"])   # Profile, then the subset's entries
    # typing in one card, then straight into another: both are saved
    a = pg.locator("#editor .doc-card[data-key='entry:EXP-002'] input[data-key=location]")
    b = pg.locator("#editor .doc-card[data-key='entry:EXP-001'] input[data-key=location]")
    a.fill("Sydney NSW")
    b.fill("Perth WA")
    loc = lambda eid: json.dumps(next(e for e in state(server)["doc"]["entries"] if e["id"] == eid).get("location"), ensure_ascii=False)   # noqa: E731
    wait_until(lambda: "Sydney NSW" in loc("EXP-002") and "Perth WA" in loc("EXP-001"), msg="both saved")
    assert "海港城" in loc("EXP-001")                                         # the other language is untouched
    assert pg.get_attribute("#active", "data-key") == "entry:EXP-001"          # the card typed in last is the one being edited
    assert pg.locator("#library .entry.on[data-id='EXP-001']").count() == 1
    assert "海港城市大学" in pg.inner_text("#chat-context") or "Harbour City University" in pg.inner_text("#chat-context")
    # an entry not in the subset is one folded line; ticking it adds it, opening it shows its card
    pg.click("#library .entry[data-id='EXP-003'] .check")
    wait_until(lambda: "EXP-003" not in subset(server)["entries"], msg="removed from subset")
    pg.wait_for_selector("#editor .doc-fold[data-key='entry:EXP-003']")
    pg.click("#editor .doc-fold[data-key='entry:EXP-003'] .df-title")
    pg.wait_for_selector("#active.doc-card.out[data-key='entry:EXP-003']")
    pg.click("#library .entry[data-id='EXP-003'] .check")
    wait_until(lambda: "EXP-003" in subset(server)["entries"], msg="back in the subset")
    pg.wait_for_selector("#editor .doc-card:not(.out)[data-key='entry:EXP-003']")
    # the fact layer is folded under each card
    assert not pg.is_visible("#active .fact-fold textarea")
    pg.click("#active .fact-fold > summary")
    pg.wait_for_selector("#active .fact-fold textarea", state="visible")
    # the outline folds into a strip that opens on hover, and is pinned again
    pg.click("#library .rail-head button")
    pg.wait_for_selector("#library", state="hidden")
    pg.hover("#editor .outline-strip")
    pg.wait_for_selector(".outline-pop .entry[data-id='EXP-002']")
    pg.click(".outline-pop .entry[data-id='EXP-002'] .e-main")
    pg.wait_for_selector("#active[data-key='entry:EXP-002']")
    pg.click(".outline-pop .rail-head button")
    pg.wait_for_selector("#library", state="visible")
    # the preview can be hidden for room, and comes back
    pg.click("#editor .preview-toggle")
    pg.wait_for_selector(".pane.right", state="hidden")
    pg.click("#editor .preview-toggle")
    pg.wait_for_selector(".pane.right", state="visible")
    wait_rendered(pg)


@needs_tex
def test_include_toggle_and_reorder(page, server):
    page.click(".entry[data-id='EXP-003'] .check")
    wait_until(lambda: "EXP-003" not in subset(server)["entries"], msg="removed from subset")
    page.wait_for_selector(".entry[data-id='EXP-003'].out")   # the page repaints just after the save lands
    page.click(".entry[data-id='EXP-003'] .check")
    wait_until(lambda: subset(server)["entries"][-1] == "EXP-003", msg="re-added at the end")
    page.hover(".entry[data-id='EXP-003']")
    page.click(".entry[data-id='EXP-003'] .e-tools button[data-tip='上移']")
    wait_until(lambda: subset(server)["entries"].index("EXP-003") < subset(server)["entries"].index("EXP-002"), msg="moved up")
    wait_rendered(page)


@needs_tex
def test_layout_template_and_language(page, server):
    page.click("#layout-toggle")
    page.wait_for_selector("#layout-panel:not(.hidden)")
    page.eval_on_selector("#layout-panel input[data-key=font_size]",
                          "el => { el.value = '11'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    wait_until(lambda: subset(server)["layout"].get("font_size") == 11, msg="font size saved")
    choose(page, "#tpl-select", "modern")
    wait_until(lambda: subset(server)["template"] == "modern", msg="template saved")
    page.click("#lang-seg button:text('中文')")
    wait_until(lambda: subset(server)["lang"] == "zh", msg="language saved")
    # the render of the Chinese resume, not one still finishing from before the switch
    page.wait_for_function("() => ui.render.last && ui.render.last.lang === 'zh' && !ui.render.busy", timeout=40000)
    wait_rendered(page)
    assert page.locator("#page-badge").inner_text().strip().startswith("1 页")
    # reset one knob back to default
    page.click("#layout-panel .ctl:has(input[data-key=font_size]) button[data-tip='恢复默认']")
    wait_until(lambda: "font_size" not in subset(server)["layout"], msg="font size reset")


def test_external_edit_syncs_open_form(page, server, data_file):
    open_entry(page, "EXP-002")
    text = data_file.read_text(encoding="utf-8").replace("Solo developer", "Edited by Claude")
    data_file.write_text(text, encoding="utf-8")
    page.wait_for_function("() => document.querySelector('#active input[data-key=subtitle]').value === 'Edited by Claude'", timeout=5000)


def test_conflict_banner_when_both_edit(page, server, data_file):
    open_entry(page, "EXP-002")
    page.type("#active input[data-key=location]", "Harbour City")        # unsaved local change
    text = data_file.read_text(encoding="utf-8").replace("Solo developer", "Changed elsewhere")
    data_file.write_text(text, encoding="utf-8")                      # same entry changes on disk
    page.wait_for_selector(".banner.warn", timeout=5000)
    page.click(".banner.warn button:text('放弃我的修改并载入最新版本')")
    page.click(".modal .btn.danger-soft")                                      # dropping what was typed asks first
    page.wait_for_selector(".banner.warn", state="detached")
    assert page.input_value("#active input[data-key=subtitle]") == "Changed elsewhere"
    assert page.input_value("#active input[data-key=location]") == ""


def test_add_and_delete_entry(page, server, data_file):
    page.click(".lib-section:has(.t:text('Projects')) .lib-head button")
    page.wait_for_selector(".entry.on[data-id='EXP-006']")
    page.wait_for_selector(".ed-head .id:text('Projects #3')")
    page.wait_for_function("() => document.activeElement.dataset.key === 'title'")   # ready to type the name
    page.keyboard.type("New Project")
    wait_until(lambda: "New Project" in data_file.read_text(encoding="utf-8"), msg="title saved")
    assert "EXP-006" in subset(server)["entries"]                    # new entries join the current subset
    page.click("#active .ed-head .btn.icon")                          # its ⋯ menu
    page.click(".float .menu-item:has-text('删除这条经历')")
    page.click(".modal button:text('删除')")
    wait_until(lambda: "EXP-006" not in data_file.read_text(encoding="utf-8"), msg="entry deleted")
    page.wait_for_selector("#active .ed-head h2:text('个人信息')")


def test_profile_contacts_and_sections(page, server, data_file):
    page.click(".nav-row:text('个人信息')")
    page.click("button:text('添加联系方式')")
    rows = page.locator(".card:has(h3:text('联系方式')) .lr")
    last = rows.nth(rows.count() - 1)
    last.locator("input").nth(0).fill("linkedin")
    last.locator("input").nth(1).fill("linkedin.com/in/alex")
    wait_until(lambda: "linkedin.com/in/alex" in data_file.read_text(encoding="utf-8"), msg="contact saved")
    page.click(".nav-row:text('板块与简历设置')")
    card = page.locator(".card:has(h3:text-is('板块'))")
    first = card.locator(".lr input").first.input_value()
    grip = card.locator(".lr").first.locator(".drag-handle").bounding_box()
    third = card.locator(".lr").nth(2).bounding_box()
    page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)   # drag the first row past the third
    page.mouse.down()
    page.mouse.move(grip["x"] + grip["width"] / 2, third["y"] + third["height"] - 2, steps=8)
    page.mouse.up()
    vid = value_of(page, "#subset-select")
    wait_until(lambda: next(v for v in state(server)["doc"]["subsets"] if v["id"] == vid).get("sections", [None] * 3)[2] == first,
               msg="order saved on the subset")
    page.wait_for_function(f"() => document.querySelectorAll('.card .lr input')[0].value !== {first!r}")
    page.click("button:text('添加板块')")
    wait_until(lambda: any(s["id"].startswith("section-") for s in state(server)["doc"]["sections"]), msg="section added")


def test_contact_split_by_language_keeps_the_other(page, server, data_file):
    text = data_file.read_text(encoding="utf-8").replace(
        "{label: phone, value: '+61 400 000 000'}",
        "{label: phone, value: {en: '+61 400 000 000', zh: '+86 150 0000 0000'}, url: {en: 'tel:+61400000000', zh: 'tel:+8615000000000'}}")
    data_file.write_text(text, encoding="utf-8")
    page.click(".nav-row:text('个人信息')")
    row = page.locator(".card:has(h3:text('联系方式')) .lr >> nth=2")
    page.click(".doc-top .seg button:text('中文')")
    page.wait_for_function("() => [...document.querySelectorAll('.lr input')].some((i) => i.value === 'tel:+8615000000000')")
    assert "object" not in row.locator("input").nth(2).input_value()
    row.locator("input").nth(1).fill("+86 150 1111 2222")
    phone = lambda: next(c for c in state(server)["doc"]["profile"]["contacts"] if c["label"] == "phone")
    wait_until(lambda: phone()["value"] == {"en": "+61 400 000 000", "zh": "+86 150 1111 2222"}, msg="only the Chinese number changed")
    assert phone()["url"] == {"en": "tel:+61400000000", "zh": "tel:+8615000000000"}
    page.click(".card:has(h3:text('联系方式')) .lr >> nth=0 >> input >> nth=1")
    page.keyboard.press("End")
    page.keyboard.type(".au")                                         # a contact written once stays one value
    wait_until(lambda: next(c for c in state(server)["doc"]["profile"]["contacts"] if c["label"] == "email")["value"] == "alex@example.com.au",
               msg="neutral contact saved as one value")


def chosen_lang(server):
    js = server.req("GET", "/i18n.js")[1].decode("utf-8")
    return json.loads(js.split("=", 1)[1].rstrip().rstrip(";"))["chosen"]


def test_settings_dialog_holds_app_preferences(page, server):
    assert page.locator(".card:has-text('界面语言')").count() == 0      # not on a resume page
    page.click(".nav-row:text('板块与简历设置')")
    assert page.locator("#editor :text('界面语言')").count() == 0
    assert page.locator("header :text('resume.yaml')").count() == 0    # no file path or status dot in the top bar
    assert page.locator("#conn, #datapath").count() == 0
    assert page.locator("#offline").is_hidden()
    page.click("#settings-btn")
    dlg = page.locator(".modal.settings")
    dlg.locator(":text('界面语言')").wait_for()
    assert dlg.locator(".data-path").inner_text() == state(server)["data_path"]
    page.screenshot(path=str(ART / "new-settings-open.png"))
    dlg.locator(".select[aria-label='界面语言']").click()
    page.wait_for_selector(".float [role=option]:has-text('Deutsch')")
    page.keyboard.press("Escape")                                    # closes the dropdown first
    page.wait_for_selector(".float", state="detached")
    assert dlg.is_visible()
    page.keyboard.press("Escape")
    dlg.wait_for(state="detached")
    page.keyboard.press("Control+,")
    dlg.wait_for()
    page.click(".modal.settings button:text('完成')")
    dlg.wait_for(state="detached")
    page.screenshot(path=str(ART / "new-settings.png"))
    # a picked language waits for "Done": no reload on pick, Escape drops it
    page.evaluate("window.__noReload = true")
    page.click("#settings-btn")
    dlg.locator(".select[aria-label='界面语言']").click()
    page.click(".float [role=option]:has-text('Deutsch')")
    page.wait_for_timeout(400)
    assert dlg.is_visible() and page.evaluate("window.__noReload") is True
    assert chosen_lang(server) is None
    page.keyboard.press("Escape")
    dlg.wait_for(state="detached")
    assert chosen_lang(server) is None
    page.click("#settings-btn")
    dlg.locator(".select[aria-label='界面语言']").click()
    page.click(".float [role=option]:has-text('Deutsch')")
    page.click(".modal.settings button:text('完成')")
    page.wait_for_function("() => window.__noReload === undefined")   # the page reloaded
    page.locator("#settings-btn").wait_for()
    assert chosen_lang(server) == "de"
    server.req("PUT", "/api/prefs", {"ui_lang": None})


def test_open_another_data_file_in_the_same_window(page, server, data_file, tmp_path, monkeypatch):
    other = tmp_path / "other" / "resume.yaml"
    other.parent.mkdir()
    other.write_text(data_file.read_text(encoding="utf-8").replace("Alex Chen", "Sam Lee"), encoding="utf-8")
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps([str(other)]))
    page.click("#settings-btn")
    page.click(".modal.settings button:has-text('打开其他文件')")
    page.wait_for_selector("#toast.show:has-text('已打开')")
    page.wait_for_selector(".entry")
    wait_rendered(page)
    assert state(server)["data_path"] == str(other.resolve())
    page.click(".nav-row:text('个人信息')")
    page.click(".doc-top .seg button:text('EN')")
    page.wait_for_function("() => [...document.querySelectorAll('#editor input')].some((i) => i.value === 'Sam Lee')")
    # the connection follows the new file: an edit Claude makes to it refreshes the form
    other.write_text(other.read_text(encoding="utf-8").replace("Sam Lee", "Sam Q. Lee"), encoding="utf-8")
    page.wait_for_function("() => [...document.querySelectorAll('#editor input')].some((i) => i.value === 'Sam Q. Lee')", timeout=8000)
    assert "Sam" not in data_file.read_text(encoding="utf-8")


def test_add_resume_language_from_the_preview(page, server):
    page.click("#lang-seg .seg-add")
    page.wait_for_selector(".float .menu-item:has-text('Deutsch')")
    page.screenshot(path=str(ART / "new-lang-menu.png"))
    page.click(".float .menu-item:has-text('Deutsch')")
    wait_until(lambda: state(server)["doc"]["settings"]["languages"] == ["en", "zh", "de"], msg="German added")
    page.wait_for_selector("#lang-seg button:text('DE')")
    page.click("#lang-seg .seg-add")
    page.click(".float .menu-item:has-text('管理语言')")
    page.wait_for_selector(".ed-head h1:text('板块与简历设置')")


def test_jobs_create_switch_delete(page, server):
    page.click("#subset-menu-btn")
    page.click(".float .menu-item:has(.mi-hint:text('空白，手动勾选'))")
    page.fill(".modal input", "管培生")
    page.click(".modal button:text('创建')")
    wait_until(lambda: value_of(page, "#subset-select") == "SUB-001", msg="switched to new job")
    assert page.inner_text("#subset-select .sel-label") == "管培生"
    assert page.locator(".entry.out").count() == 5                    # blank job: nothing ticked
    page.click("#subset-menu-btn")
    page.click(".float .menu-item:has-text('删除当前子集')")
    page.click(".modal button:text('删除')")
    wait_until(lambda: all(v["id"] != "SUB-001" for v in state(server)["doc"]["subsets"]), msg="job deleted")
    wait_until(lambda: value_of(page, "#subset-select") == "ALL", msg="back on ALL")


def test_subset_versions_keep_compare_preview_and_go_back(page, server):
    chip = page.locator("#subset-version")
    assert chip.inner_text().strip() == "尚未定稿"
    chip.click()
    page.click(".float .menu-item:has-text('定稿为 v1')")
    page.fill(".modal input", "秋招第一批")
    page.click(".modal button:text-is('保留')")
    wait_until(lambda: chip.inner_text().strip() == "v1", msg="kept as v1")
    # a shared entry it uses changes elsewhere (Claude, by hand): the subset has changed since v1
    eid = subset(server)["entries"][0]
    e = next(x for x in state(server)["doc"]["entries"] if x["id"] == eid)
    e["bullets"] = [*e.get("bullets", []), {"en": "Shipped v2 to every user", "zh": "上线第二版"}]
    assert server.req("PUT", "/api/item", {"kind": "entry", "id": eid, "data": e, "base": None})[0] == 200
    wait_until(lambda: chip.inner_text().strip() == "v1 · 有改动", msg="changed since v1")
    page.click(".nav-row:has-text('版本')")
    page.wait_for_selector(".ed-head h1:text('版本')")
    page.click(".vshow")                                                   # what changed since v1, git-style
    page.wait_for_selector(".vdiff .dl-add")
    assert "Shipped v2 to every user" in page.inner_text(".vdiff .dl-add")
    page.fill(".vnote-input", "加了一条")
    page.click(".vkeep .btn.primary")
    # only the wording changed: a minor number (2026-11-15), shown in the list too; the file still counts 1, 2
    wait_until(lambda: chip.inner_text().strip() == "v1.1", msg="kept as v1.1")
    assert page.inner_text(".vrow[data-n='2'] .vtext") == "加了一条" and page.inner_text(".vrow[data-n='2'] .vnum") == "v1.1"
    # look at v1 as it was, then back to now
    page.click(".vrow[data-n='1'] .vline .btn.sm")
    page.wait_for_selector("#viewing:not(.hidden)")
    page.wait_for_selector("#pages img[src*='/versions/1/page/1']")
    wait_rendered(page)
    page.click("#viewing button")
    page.wait_for_selector("#viewing.hidden", state="attached")
    page.wait_for_selector("#pages img[src*='/subsets/RES-TECH-EN/page/1']")
    # go back to v1: now is v2 already, so nothing extra is kept
    page.click(".vrow[data-n='1'] .btn.icon")
    page.click(".float .menu-item:has-text('恢复到 v1')")
    page.wait_for_selector(".modal:has-text('当前内容即为 v1.1')")
    page.click(".modal button:text('恢复到此版本')")
    wait_until(lambda: chip.inner_text().strip() == "v1", msg="back to v1")
    e = next(x for x in state(server)["doc"]["entries"] if x["id"] == eid)
    assert "Shipped v2" not in json.dumps(e) and page.locator(".vrow").count() == 2
    # leaving an entry out changes what the resume is made of: the next one kept is a major number
    v = subset(server)
    v["entries"] = v["entries"][1:]
    assert server.req("PUT", "/api/item", {"kind": "subset", "id": "RES-TECH-EN", "data": v, "base": None})[0] == 200
    wait_until(lambda: "定稿为 v2" in page.inner_text(".vkeep .btn.primary"), msg="offered as v2")
    page.click(".vkeep .btn.primary")
    wait_until(lambda: chip.inner_text().strip() == "v2", msg="kept as v2")


def test_job_from_all_starts_full_and_has_own_headline(page_all, server):
    pg = page_all
    pg.click("#subset-menu-btn")
    pg.click(".float .menu-item:has-text('先勾上全部经历')")
    pg.fill(".modal input", "AI Agent 工程师")
    pg.click(".modal button:text('创建')")
    wait_until(lambda: value_of(pg, "#subset-select") == "SUB-001", msg="new job")
    assert pg.locator(".entry.out").count() == 0
    # the headline is typed right on the profile page, into the job picked at the top
    pg.click(".nav-row:text('个人信息')")
    pg.click(".doc-top .seg button:text('EN')")
    pg.fill("#active input[data-key=headline]", "AI Agent Engineer")
    wait_until(lambda: subset(server, "SUB-001").get("headline") in ("AI Agent Engineer", {"en": "AI Agent Engineer"}), msg="headline saved")
    assert "headline" not in state(server)["doc"]["profile"]
    assert subset(server, "ALL").get("headline") != "AI Agent Engineer"
    pg.fill("#active input[data-key=headline]", "AI Agent Engineer · LLM")
    choose(pg, "#subset-select", "ALL")                              # switching right away still saves into the job
    wait_until(lambda: "LLM" in json.dumps(subset(server, "SUB-001").get("headline")), msg="typed headline kept in the job")
    assert "LLM" not in json.dumps(subset(server, "ALL").get("headline"))
    # job settings: in the sidebar for a job, absent for ALL
    assert pg.locator(".nav-row:text('子集设置')").count() == 0
    choose(pg, "#subset-select", "SUB-001")
    pg.click(".nav-row:text('子集设置')")
    pg.wait_for_selector(".ed-head h1:text('子集设置')")
    choose(pg, "#subset-select", "ALL")
    pg.wait_for_selector("#active .ed-head h2:text('个人信息')")
    pg.click("#subset-menu-btn")
    assert pg.locator(".float .menu-item:has-text('设置')").count() == 0
    pg.keyboard.press("Escape")


def test_sections_can_be_hidden_per_resume(page, server):
    page.click(".nav-row:text('板块与简历设置')")
    first = page.locator(".card:has(h3:text-is('板块')) .lr .check").first
    first.click()
    wait_until(lambda: len(subset(server).get("hide_sections") or []) == 1, msg="section hidden in this job")
    assert not subset(server, "ALL").get("hide_sections")
    page.wait_for_selector(".card:has(h3:text-is('板块')) .lr.off")
    page.locator(".card:has(h3:text-is('板块')) .lr .check").first.click()
    wait_until(lambda: not subset(server).get("hide_sections"), msg="shown again")


def test_contacts_can_be_hidden_per_job(page, server):
    page.click(".nav-row:text('个人信息')")
    rows = page.locator(".card:has(h3:text('联系方式')) .lr")
    rows.nth(2).locator(".check").click()                             # phone
    wait_until(lambda: subset(server).get("hide_contacts") == ["phone"], msg="hidden in this job")
    page.wait_for_selector(".card:has(h3:text('联系方式')) .lr >> nth=2 >> .check:not(.on)")


def test_numbers_are_per_section_and_ids_stay_hidden(page_all):
    pg = page_all
    nos = lambda sec: pg.locator(f".lib-section:has(.t:text('{sec}')) .e-no").all_inner_texts()
    assert nos("Education") == ["1"] and nos("Projects") == ["1", "2"] and nos("Skills") == ["1", "2"]
    open_entry(pg, "EXP-003")
    assert pg.inner_text("#active .ed-head .id") == "Projects #2"
    assert "EXP-" not in pg.inner_text("#library") + pg.inner_text("#editor")
    choose(pg, "#active .select[data-key=section]", "experience")      # moving section keeps the entry
    pg.wait_for_selector(".lib-section:has(.t:text('Experience')) .entry[data-id='EXP-003']")
    assert nos("Experience") == ["1"] and nos("Projects") == ["1"]


def test_chat_streams_markdown_tools_and_resumes(page, server, fake_claude):
    page.wait_for_selector(".chat-welcome")
    page.fill("#chat-input", "你好，帮我看看")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-user .msg-text:text('你好，帮我看看')")
    page.wait_for_selector(".msg-ai .md strong:text('已经处理')", timeout=10000)
    assert page.locator(".msg-ai .md li").count() == 2
    assert page.locator(".msg-ai .tool .tn").first.inner_text() == "读取"
    # a chat started by typing is named by its agent, and the list shows the name without a reload
    page.wait_for_selector("#chat-select .sel-label:text-is('AI名 你好，帮我看看')", timeout=10000)
    page.wait_for_selector("#chat-stop.hidden", state="attached")
    page.fill("#chat-input", "继续")
    page.keyboard.press("Enter")
    wait_until(lambda: len(calls(fake_claude)) == 2, msg="second call")
    page.wait_for_function("() => document.querySelectorAll('.msg-ai').length === 2", timeout=10000)
    second = calls(fake_claude)[1]["argv"]
    assert "--resume" in second
    assert "Claude 知道你正在看" in page.inner_text("#chat-context")


def test_chat_rows_show_what_claude_code_shows_and_stay_open(page, server, fake_claude):
    """Like the Claude Code app: an edit opens to its diff, a command to its whole text and output, a subagent to its
    steps; a bare address is a link; a code block copies; a finished reply says how long it took. A row opened while
    the reply streams stays open through the next pieces and the reply's end, and reading higher up stays put."""
    page.wait_for_selector(".chat-welcome")
    page.fill("#chat-input", "SHOWCASE")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-ai .ai-tools .mt-time", timeout=15000)
    # eight steps in a row fold into one line saying what they did; opened, each step is its own row
    group = page.locator(".msg-ai .tool-group")
    assert group.count() == 1 and group.get_attribute("open") is None
    assert group.locator(":scope > summary .tg-t").inner_text() == "读取了 1 个文件，修改了 2 个文件，运行了 2 条命令，搜索了 1 次，查看了 1 个网页，运行了 1 个子任务"
    assert group.locator(":scope > summary .tg-fail").inner_text() == "1 步失败" and group.locator(":scope > summary .trow-stat").inner_text().split() == ["+33", "−2"]
    group.locator(":scope > summary").click()
    rows = group.locator(".tool")
    assert rows.count() == 8
    edit = rows.filter(has_text="修改").first
    assert edit.locator(".trow-stat").inner_text().split() == ["+2", "−2"]
    edit.locator("summary").click()
    assert "multi-stage AI agent" in edit.locator(".trow-diff .add").first.inner_text()
    assert edit.locator("pre:not(.trow-diff)").count() == 0                  # "file updated" says nothing the diff doesn't
    cmd = rows.filter(has_text="check.py").first
    cmd.locator("summary").click()
    assert cmd.locator(".trow-cmd").inner_text().endswith("report.xml") and "119 passed" in cmd.locator(".trow-out").inner_text()
    sub = rows.filter(has_text="子任务").first
    assert "3 步" in sub.locator("summary").inner_text()
    sub.locator("summary").click()
    assert sub.locator(".trow-step").count() == 3
    assert page.locator(".msg-ai .md a[href^='https://www.example.com/careers/engineering/software-engineer-graduate-program-2027-sydney-melbourne-canberra?utm_source=linkedin&utm_medium=social']").count() == 1
    page.evaluate("() => { window.__copied = []; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (s) => { window.__copied.push(s); } } }); }")
    page.click(".msg-ai .code-block .cb-copy")
    copied = page.evaluate("() => window.__copied")
    assert copied and copied[0].startswith("def fit_one_page(doc, max_lines=60):"), copied
    page.wait_for_selector(".code-block .cb-copy:text('已复制')")
    assert page.locator(".msg-ai .ai-tools .mt-time").inner_text().startswith("用时")

    # a reply still streaming: open a row, more arrives, the reply ends, and the user reading higher up stays there
    page.evaluate("""() => {
        ui.chat.running = true;
        ui.chat.live = { started_at: Date.now() / 1000, blocks: [
          { type: "tool", id: "live-1", name: "Bash", hint: "ls", status: "done", result: "a\\nb", input: { command: "ls" } },
          { type: "text", text: "第一段" }] };
        renderChat(); }""")
    page.click(".msg-ai .tool[data-key='tool:live-1'] summary")
    page.evaluate("() => { ui.chat.live.blocks[1].text += '，第二段'; renderLive(true); }")
    assert page.locator(".tool[data-key='tool:live-1']").get_attribute("open") is not None
    page.evaluate("() => { const l = document.querySelector('#chat-log'); l.scrollTop = 40; }")
    page.evaluate("""() => { ui.chat.messages.push({ role: "assistant", blocks: ui.chat.live.blocks, took: 3 });
        ui.chat.running = false; ui.chat.live = null; renderChat(); }""")
    assert page.locator(".tool[data-key='tool:live-1']").get_attribute("open") is not None
    assert page.locator(".msg-ai .tool-group").first.get_attribute("open") is not None   # the opened group too
    assert page.evaluate("document.querySelector('#chat-log').scrollTop") == 40


def test_selected_text_survives_the_stream_and_can_be_quoted(page, server, fake_claude):
    """Text selected in a reply stays selected while it streams on (in the paragraph that grows, too), through a new
    step and the reply's end; a Quote button by the selection puts it in the box as "> …" after what is typed."""
    page.wait_for_selector(".chat-welcome")
    page.evaluate("""() => {
        ui.chat.running = true;
        ui.chat.live = { started_at: Date.now() / 1000, blocks: [{ type: "text", text: "开头一句。正在输出的这一段" }] };
        renderChat();
        const t = ui.chat.liveEl.querySelector('.md p').firstChild, r = document.createRange();
        r.setStart(t, 5); r.setEnd(t, 9);
        getSelection().removeAllRanges(); getSelection().addRange(r); }""")
    for piece in ["继续", "，还有 **加粗**", "。"]:
        page.evaluate("(p) => { ui.chat.live.blocks[0].text += p; renderLive(true); }", piece)
        assert page.evaluate("getSelection().toString()") == "正在输出"
    page.evaluate("""() => { ui.chat.live.blocks.push({ type: "tool", id: "q1", name: "Bash", hint: "ls", status: "running", result: "", input: { command: "ls" } });
        renderLive(true); }""")
    assert page.evaluate("getSelection().toString()") == "正在输出"
    page.evaluate("""() => { ui.chat.messages.push({ role: "assistant", blocks: ui.chat.live.blocks, took: 2 });
        ui.chat.running = false; ui.chat.live = null; renderChat(); }""")
    assert page.evaluate("getSelection().toString()") == "正在输出"
    page.fill("#chat-input", "我的看法")
    page.evaluate("""() => { const t = document.querySelector('.msg-ai .md p').firstChild, r = document.createRange();
        r.setStart(t, 5); r.setEnd(t, 9); getSelection().removeAllRanges(); getSelection().addRange(r); }""")
    page.wait_for_selector(".chat-quote:not(.hidden):text('引用')")
    page.click(".chat-quote")
    assert page.input_value("#chat-input") == "我的看法\n\n> 正在输出\n\n"
    page.wait_for_selector(".chat-quote", state="hidden")
    assert page.evaluate("document.activeElement.id") == "chat-input"


def test_polish_shows_diff_and_undo_restores(page, server, data_file, fake_claude, monkeypatch):
    original = data_file.read_text(encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", json.dumps({"file": str(data_file), "from": "Solo developer", "to": "Solo developer (polished)"}))
    open_entry(page, "EXP-002")
    page.click("#active .ai-btn")
    page.click(".float .menu-item:has-text('润色')")
    page.fill("#polish-box input", "更简洁")
    page.click("#polish-box button")
    page.wait_for_selector(".msg-user:has-text('QuizLoom')")
    page.wait_for_selector(".card.diff h3:text('AI 已修改此条')", timeout=15000)
    assert "Solo developer (polished)" in page.inner_text(".card.diff")
    page.click(".card.diff button:text('撤销，恢复到改动前')")
    wait_until(lambda: "Solo developer (polished)" not in data_file.read_text(encoding="utf-8"), msg="undo applied")
    assert "subtitle: {en: Solo developer, zh: 独立开发}" in data_file.read_text(encoding="utf-8")
    assert original.splitlines()[0] == data_file.read_text(encoding="utf-8").splitlines()[0]


def test_an_ai_button_starts_a_new_chat_unless_settings_say_otherwise(page, server, fake_claude):
    """2026-11-04: an AI button went on in the open chat, sending its whole (maybe long, cache expired) history again.
    It now starts a chat of its own (an empty open chat is used as it is); Settings can turn that off."""
    chats = lambda: server.req("GET", "/api/chats")[1]["chats"]
    idle = "() => !ui.chat.running && !AI_WORK.size"
    open_chat_tab(page)
    page.fill("#chat-input", "你好")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-user:has-text('你好')")
    page.wait_for_function(idle)
    page.wait_for_function("() => !!ui.chat.id")
    talk = page.evaluate("ui.chat.id")
    assert [c["id"] for c in chats()] == [talk], (talk, chats())

    def polish():
        open_entry(page, "EXP-002")
        page.click("#active .ai-btn")
        page.click(".float .menu-item:has-text('润色')")
        page.fill("#polish-box input", "更简洁")
        page.click("#polish-box button")
        page.wait_for_selector(".msg-user:has-text('QuizLoom')")
        page.wait_for_function(idle, timeout=15000)

    polish()
    assert len(chats()) == 2 and page.evaluate("ui.chat.id") != talk            # a chat of its own
    first = server.req("GET", f"/api/chats/{page.evaluate('ui.chat.id')}")[1]["messages"][0]
    assert first["role"] == "user" and "QuizLoom" in first["text"]
    assert all("QuizLoom" not in m.get("text", "") for m in server.req("GET", f"/api/chats/{talk}")[1]["messages"])
    # an empty open chat is used as it is: no second empty one
    page.click("#chat-new")
    page.wait_for_function("() => ui.chat.messages.length === 0")
    empty = page.evaluate("ui.chat.id")
    polish()
    assert len(chats()) == 3 and page.evaluate("ui.chat.id") == empty
    # turned off in Settings (saved on Done): the button goes on in the chat that is open
    page.click("#settings-btn")
    box = page.locator(".modal.settings #ai-new-chat .check-row")
    assert box.get_attribute("aria-checked") == "true"                           # on unless turned off
    page.locator(".modal.settings").screenshot(path=str(ART / "settings-ai-new-chat.png"))
    box.click()
    assert box.get_attribute("aria-checked") == "false"
    assert state(server)["claude"]["ai_new_chat"] is True                        # nothing saved before Done
    page.click(".modal.settings button:text('完成')")
    page.wait_for_selector(".modal.settings", state="detached")
    assert state(server)["claude"]["ai_new_chat"] is False
    polish()
    assert len(chats()) == 3 and page.evaluate("ui.chat.id") == empty
    # it stays off after a restart of the page
    page.reload()
    page.wait_for_selector(".entry")
    page.click("#settings-btn")
    assert page.locator(".modal.settings #ai-new-chat .check-row").get_attribute("aria-checked") == "false"
    page.keyboard.press("Escape")
    server.req("PUT", "/api/prefs", {"ai_new_chat": True})


def test_mock_interview_predicts_hides_answers_and_follows_up(page, server, data_file, fake_claude, monkeypatch):
    mock = ("    evidence: []\n    interview:\n      - q: 为什么拆成多阶段？\n        a: 一个 prompt 会跑偏。\n"
            "        follow_ups:\n          - {q: 怎么发现跑偏的？, a: 【待补充：一个具体例子】}\n  - id: EXP-003")
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", json.dumps({"file": str(data_file), "from": "    evidence: []\n  - id: EXP-003", "to": mock}))
    open_entry(page, "EXP-002")
    page.click("#active .mock-fold > summary")
    page.wait_for_selector("#active .mock-empty", state="visible")
    page.fill("#active .mock-instr", "字节后端")
    page.click("#active .mock-run")
    page.wait_for_selector(".msg-user:has-text('字节后端')")
    page.wait_for_selector("#active .card.diff", timeout=15000)
    review = page.inner_text("#active .card.diff")                              # the review lists each question and answer
    assert "模拟面试 · 1 · 问题" in review and "为什么拆成多阶段？" in review
    assert "模拟面试 · 1 · 追问 · 1 · 问题" in review and "怎么发现跑偏的？" in review
    page.wait_for_selector("#active .mq[data-path='[0]'] .mq-q")
    assert page.input_value("#active .mq[data-path='[0]'] > .mq-q") == "为什么拆成多阶段？"
    assert page.input_value("#active .mq[data-path='[0,0]'] .mq-ans") == "【待补充：一个具体例子】"
    assert page.inner_text("#active .mock-fold .ff-sub") == "1 题 · 1 个追问"
    page.click("#active .card.diff button:text('保留')")

    # hide the answers to practise: each opens on its own
    page.click("#active .mock-hide")
    assert page.locator("#active .mq-ans").count() == 0 and page.locator("#active .mq-show").count() == 2
    page.click("#active .mq[data-path='[0]'] > .mq-a .mq-show")
    assert page.input_value("#active .mq[data-path='[0]'] .mq-ans") == "一个 prompt 会跑偏。"
    assert page.locator("#active .mq-ans").count() == 1
    page.click("#active .mock-hide")
    assert page.locator("#active .mq-ans").count() == 2

    # a question of my own: Claude can answer it, and once answered, follow it up
    page.click("#active .mock-add")
    q2 = "#active .mq[data-path='[1]']"
    page.wait_for_selector(f"{q2} .mq-q:focus")
    assert not page.is_visible(f"{q2} .mq-answer")
    page.keyboard.type("最难的问题是什么？")
    assert page.is_visible(f"{q2} .mq-answer") and not page.is_visible(f"{q2} .mq-probe")
    page.click(f"{q2} .mq-answer")
    page.wait_for_selector(".msg-user .msg-action:has-text('回答')")
    page.wait_for_selector("#active .card.diff h3:text('AI 未修改此条')", timeout=15000)   # the fake one changes nothing
    page.click("#active .card.diff button:text('知道了')")
    wait_until(lambda: "最难的问题是什么？" in data_file.read_text(encoding="utf-8"), msg="my question saved")
    page.fill(f"{q2} .mq-ans", "把课件切块。")
    assert page.is_visible(f"{q2} .mq-probe") and not page.is_visible(f"{q2} .mq-answer")
    page.click(f"{q2} > .mq-top button[aria-label='手动添加追问']")
    page.wait_for_selector(f"{q2} .mq[data-path='[1,0]'] .mq-q:focus")
    page.keyboard.type("怎么切的？")
    wait_until(lambda: "怎么切的？" in data_file.read_text(encoding="utf-8"), msg="my follow-up saved")
    e = next(x for x in state(server)["doc"]["entries"] if x["id"] == "EXP-002")
    assert e["interview"][1] == {"q": "最难的问题是什么？", "a": "把课件切块。", "follow_ups": [{"q": "怎么切的？", "a": ""}]}
    assert len(e["interview"][0]["follow_ups"]) == 1

    # delete a question with follow-ups: asked first
    page.click(f"{q2} > .mq-top button[aria-label='删除此问题']")
    page.click("#modal-root button:text('删除')")
    wait_until(lambda: "最难的问题是什么？" not in data_file.read_text(encoding="utf-8"), msg="deleted")


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
    choose(page, "#subset-select", "ALL")
    page.wait_for_selector(".lib-hint:not(.job)")
    wait_rendered(page)
    page.screenshot(path=str(ART / f"all-{tag}.png"))
    choose(page, "#subset-select", "RES-TECH-EN")
    page.wait_for_selector(".lib-hint.job")
    wait_rendered(page)
    page.click("#layout-toggle")
    page.screenshot(path=str(ART / f"layout-{tag}.png"))
    page.click("#layout-toggle")
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
    page.click(".nav-row:text('板块与简历设置')")
    no_native(page)
    no_native(page)


def test_custom_select_mouse_and_keyboard(page, server):
    page.click("#subset-select")
    lb = page.locator(".float.listbox")
    assert lb.locator(".lb-group").first.inner_text() == "子集"
    assert lb.locator("[role=option][aria-selected=true]").get_attribute("data-value") == "RES-TECH-EN"
    assert "中文" in lb.locator("[data-value='RES-TECH-ZH'] .lb-hint").inner_text()   # language + template at a glance
    page.keyboard.press("Escape")
    page.wait_for_selector(".float", state="detached")
    assert page.evaluate("document.activeElement.id") == "subset-select"            # focus returns to the trigger
    page.keyboard.press("ArrowDown")                                                  # opens
    page.wait_for_selector(".float.listbox")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    wait_until(lambda: value_of(page, "#subset-select") == "RES-TECH-ZH", msg="picked with the keyboard")
    page.click("#subset-select")
    page.mouse.click(5, 880)                                                          # click outside closes
    page.wait_for_selector(".float", state="detached")


def test_a_second_press_on_what_opened_a_panel_closes_it(page_all, server):
    """2026-11-13: a table cell's options (nature, cities) stayed open when the cell was clicked again. A dropdown, a
    cell editor or a menu button closes on a second press, like everywhere else, and is not opened again by it."""
    pg = page_all
    c1 = server.req("POST", "/api/hunt/companies", {"name": "星河科技"})[1]["company"]["id"]
    server.req("POST", f"/api/hunt/companies/{c1}/roles", {"role": "服务端研发工程师"})
    shut = lambda: pg.wait_for_timeout(300) or pg.locator(".float").count() == 0

    def twice(sel):
        pg.click(sel)
        pg.wait_for_selector(".float")
        pg.click(sel)
        assert shut(), f"{sel}: still open after a second press"
        pg.click(sel)                                   # and a third opens it again
        pg.wait_for_selector(".float")
        pg.keyboard.press("Escape")
        pg.wait_for_selector(".float", state="detached")

    twice("#subset-select")                             # a dropdown
    twice("#subset-menu-btn")                           # a menu button
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".dbv-view[data-table=apps] tbody tr")
    twice(".dbv-view[data-table=apps] tbody tr td[data-key=status]")   # a table cell's options


def test_tooltips_are_the_apps_own(page):
    page.hover("#subset-menu-btn")
    page.wait_for_selector(".tip.show:text('子集操作')")
    page.mouse.move(700, 600)
    page.wait_for_selector(".tip.show", state="detached", timeout=2000) if page.locator(".tip.show").count() else None


def test_start_and_end_are_months_picked_from_a_panel(page, server):
    """2026-10-31: no free-text "Custom date"; start / end are picked, the end can be now or expected."""
    entry = lambda: next(e for e in server.req("GET", "/api/state")[1]["doc"]["entries"] if e["id"] == "EXP-002")
    open_entry(page, "EXP-002")
    assert not page.locator("#active [data-key=date]").count()
    assert not page.locator("#active input[data-key=start], #active input[data-key=end]").count()
    end, present, expected = "#active .month-pick[data-key=end]", "#active #end-present", "#active #end-expected"
    tick = lambda sel: page.get_attribute(sel, "aria-checked") == "true"
    assert page.inner_text(end) == "至今" and tick(present) and not tick(expected)   # the sample's end is "present"
    # the end: a month two years on, by clicking; picking a month clears "present"
    page.click(end)
    page.wait_for_selector(".float.month-pop .mp-m")
    assert not page.locator(".month-pop .mp-foot").count()                      # nothing to clear yet
    page.click(".month-pop .mp-next")
    page.click(".month-pop .mp-next")
    page.click(".month-pop .mp-m >> nth=5")
    page.wait_for_selector(".float.month-pop", state="detached")
    year = page.evaluate("() => new Date().getFullYear()") + 2
    wait_until(lambda: entry().get("end") == f"{year}-06", msg="end picked")
    assert page.inner_text(end) == f"{year}.06" and not tick(present)
    assert page.evaluate("() => document.activeElement.dataset.key") == "end"   # the keyboard goes on from the field
    # not reached yet: ticked under the field, no panel needed
    page.click(expected)
    wait_until(lambda: entry().get("expected") is True, msg="expected saved")
    assert tick(expected)
    assert f"{year}.06（预计）" in page.inner_text("#library .entry[data-id='EXP-002'] .e-meta")
    # present again: expected goes, and the other way round
    page.click(present)
    wait_until(lambda: entry().get("end") == "present" and "expected" not in entry(), msg="back to present")
    assert tick(present) and not tick(expected) and page.inner_text(end) == "至今"
    page.click(expected)
    wait_until(lambda: entry().get("expected") is True and not entry().get("end"), msg="expected clears present")
    assert not tick(present)
    # the start by keyboard: the panel opens on its month; left, down (four to a row), Enter
    page.click("#active .month-pick[data-key=start]")
    assert page.evaluate("() => document.activeElement.textContent") == "8月"   # the sample starts 2025-08
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    wait_until(lambda: entry().get("start") == "2025-11", msg="start by keyboard")
    # cleared
    page.click("#active .month-pick[data-key=start]")
    page.click(".month-pop .mp-foot .btn:text('清除')")
    wait_until(lambda: not entry().get("start"), msg="start cleared")
    assert page.inner_text("#active .month-pick[data-key=start]") == "选择月份"


def test_text_cut_to_an_ellipsis_shows_in_full_on_hover(page):
    """2026-10-31: a label cut short (the outline's titles, a table cell, a chip) can always be read in full."""
    long = "A very long project title that the narrow outline can never show on one line, end"
    page.evaluate("(t) => { const el = document.querySelector('#library .entry .e-title'); el.textContent = t; el.id = 'cut-title'; }", long)
    assert page.evaluate("() => { const el = document.querySelector('#cut-title'); return el.scrollWidth > el.clientWidth; }")
    page.hover("#cut-title")
    page.wait_for_selector(f".tip.show:text('{long}')")
    page.mouse.move(700, 600)
    short = page.locator("#library .entry .e-title").nth(1)                     # one that fits gets no tip
    assert page.evaluate("(el) => el.scrollWidth <= el.clientWidth", short.element_handle())
    short.hover()
    page.wait_for_timeout(700)
    assert not page.locator(".tip.show").count()


def test_right_click_menu_in_text_fields(page):
    open_entry(page, "EXP-002")
    field = page.locator("#active input[data-key=subtitle]")
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
    wait_until(lambda: subset(server)["layout"].get("accent") == "7A1F2B", msg="accent saved")
    page.click("#ctl-accent")
    page.fill(".color-pop .cp-hex", "#12ab34")
    page.keyboard.press("Enter")
    wait_until(lambda: subset(server)["layout"].get("accent") == "12AB34", msg="typed hex saved")
    page.click("#ctl-paper button:text('Letter')")
    wait_until(lambda: subset(server)["layout"].get("paper") == "letter", msg="paper saved")
    page.click("#ctl-accent")
    page.click(".color-pop .cp-reset")
    wait_until(lambda: "accent" not in subset(server)["layout"], msg="accent reset")


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
    page.click(".doc-top .seg button:text('中文')")
    third = page.locator("#bullets .bullet").nth(2)
    assert "missing" in third.get_attribute("class")
    assert third.locator("textarea").get_attribute("placeholder").startswith("EN: Added an evaluation")
    assert third.locator(".line-chip").inner_text() == "缺中文"
    page.wait_for_function("() => document.querySelectorAll('#bullets .line-chip.ok').length === 2", timeout=40000)  # zh measured too
    third.hover()
    third.locator("button[data-tip^='简体中文简历不显示此条']").click()
    entry = lambda: next(x for x in state(server)["doc"]["entries"] if x["id"] == "EXP-002")  # noqa: E731
    wait_until(lambda: entry()["bullets"][2] == {"en": LONG, "zh": ""}, msg="hidden in zh")
    assert page.locator("#bullets .bullet").nth(2).locator(".line-chip").inner_text() == "不显示"
    wait_until(lambda: page.locator(".entry[data-id='EXP-002'] .e-gap").count() == 0, msg="nothing missing any more")


def test_translate_button_sends_a_native_rewrite_request(page, server, fake_claude):
    add_long_bullet(server, page)
    open_entry(page, "EXP-002")
    page.click(".doc-top .seg button:text('中文')")
    page.click("#bullet-notes button:has-text('让 AI 写简体中文版')")
    page.wait_for_selector(".msg-user.is-action .msg-action:text('添加其他语言版本')")
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
    open_facts(page)
    page.click("#evidence button:has-text('选择项目文件夹')")
    row = page.locator("#evidence .ev-row")
    row.wait_for()
    assert row.locator(".ev-name b").inner_text() == "demo"
    wait_until(lambda: str(repo).replace("\\", "/") in data_file.read_text(encoding="utf-8"), msg="path saved")
    page.wait_for_selector("#evidence .ev-chip:text('AI 尚未读取')")
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
    page.click("#evidence .sync-bar button:has-text('让 AI 根据最新进展更新')")
    page.wait_for_selector(".msg-action:text('按项目更新')")
    page.wait_for_selector(".card.diff", timeout=15000)
    page.wait_for_selector(".entry[data-id='EXP-002'] .e-dot", state="detached", timeout=8000)   # read, so in sync again
    assert "feat: evaluation harness" in calls(fake_claude)[0]["stdin"]


def test_project_roots_setting(page, server, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps(["E:/Forge"]))
    page.click(".nav-row:text('板块与简历设置')")
    page.click(".card:has(h3:text('我的项目文件夹')) button:has-text('选择文件夹')")
    wait_until(lambda: state(server)["doc"]["settings"].get("project_roots") == ["E:/Forge"], msg="root saved")
    page.wait_for_selector(".card:has(h3:text('我的项目文件夹')) .ev-path:text('E:/Forge')")


def open_chat_tab(pg):
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
    page.evaluate("localStorage.clear()")                                       # a restart on another port has no page storage
    page.reload()
    page.wait_for_function("() => ui.claude && ui.claude.info")
    page.click("#chat-new")
    page.wait_for_selector("#chat-model-btn .lbl:text('Opus 4.1')")             # the app's prefs still have it
    page.evaluate("pickAgent(agentOf('codex'))")                               # another agent for a while...
    page.wait_for_function("() => chatAgentId() === 'codex'")
    page.evaluate("pickAgent(agentOf('claude'))")                              # ...and back: Claude Code's picks are still there
    page.wait_for_selector("#chat-model-btn .lbl:text('Opus 4.1')")
    meta = server.req("GET", "/api/chats")[1]["chats"][0]
    assert (meta["agent"], meta["model_choice"], meta["mode"]) == ("claude", "claude-opus-4-1", "default")


def test_permission_card_in_the_chat(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "PERM 写个文件")
    page.keyboard.press("Enter")
    card = page.locator(".ask.ask-perm.pending")
    card.wait_for()
    assert "Claude 想要写入文件" in card.inner_text() and "notes.md" in card.inner_text()
    assert "+ hello" in card.locator(".ask-diff").inner_text()
    page.wait_for_selector("#chat-toggle .tab-dot.wait")                        # the Claude button says Claude is waiting for you
    page.wait_for_selector("#chat-select .sel-mark .chat-mark.waiting")         # and so does the chat's own sign
    card.locator("button:has-text('允许，并切换到「自动接受编辑」')").click()
    page.wait_for_selector(".ask.ask-perm.done.allowed")
    page.wait_for_selector("#chat-select .chat-mark.waiting", state="detached")
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


def test_each_chat_shows_what_it_is_doing_in_the_list(page, server, fake_claude):
    """As in Claude Code's session list: a sign left of each title, replying / waiting for you / a reply not seen."""
    open_chat_tab(page)
    page.fill("#chat-input", "SLOW 慢慢来")
    page.keyboard.press("Enter")
    page.wait_for_selector("#chat-select .sel-mark .chat-mark.running")
    first = server.req("GET", "/api/chats")[1]["chats"][0]["id"]
    page.click("#chat-new")                                                     # the user goes to another chat
    page.fill("#chat-input", "你好")
    page.keyboard.press("Enter")
    wait_until(lambda: len(server.req("GET", "/api/chats")[1]["chats"]) == 2, msg="second chat")
    page.click("#chat-select")
    page.wait_for_selector(f".float .lb-opt[data-value='{first}'] .chat-mark.running")
    assert page.locator(f".float .lb-opt[data-value='{first}'] .lb-hint").inner_text().strip()   # its time stays on the right
    page.keyboard.press("Escape")
    server.req("POST", f"/api/chats/{first}/cancel")                           # its reply ends while the user is elsewhere
    page.click("#chat-select")
    page.wait_for_selector(f".float .lb-opt[data-value='{first}'] .chat-mark.unread")
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.screenshot(path=str(ART / f"chat-states-{scheme}.png"))
    page.emulate_media(color_scheme="light")
    page.click(f".float .lb-opt[data-value='{first}']")                       # opened: seen
    page.wait_for_function(f"() => document.querySelector('#chat-select').dataset.value === '{first}'")
    page.click("#chat-select")
    page.wait_for_selector(f".float .lb-opt[data-value='{first}']")
    assert page.locator(f".float .lb-opt[data-value='{first}'] .chat-mark").count() == 0
    page.keyboard.press("Escape")


def test_signed_out_claude_explains_how_to_sign_in(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "FAIL 普通错误")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg-error:has-text('模拟的错误')", timeout=10000)
    assert page.locator(".msg-help").count() == 0                             # other errors get no sign-in hint

    page.fill("#chat-input", "LOGIN 你好")
    page.keyboard.press("Enter")
    help_ = page.locator(".msg-help")
    help_.wait_for(timeout=10000)
    text = help_.inner_text()
    assert "Claude Code（命令行）未登录" in text and "/login" in text
    assert help_.locator("code:has-text('claude')").count() >= 1


def test_a_dialog_that_destroys_opens_on_cancel_and_a_stray_enter_keeps_things(page):
    """Every danger dialog used to focus its Delete button, and Enter anywhere confirmed it."""
    page.evaluate("() => { window.got = 'open'; modal({ title: 'Delete it?', ok: 'Delete', danger: true }).then((v) => { window.got = v; }); }")
    page.wait_for_selector(".modal")
    assert page.evaluate("() => document.activeElement.textContent") == "取消"
    page.keyboard.press("Enter")
    page.wait_for_selector(".modal", state="detached")
    assert page.evaluate("window.got") is False
    page.evaluate("() => { window.got = 'open'; modal({ title: 'Delete it?', ok: 'Delete', danger: true }).then((v) => { window.got = v; }); }")
    page.wait_for_selector(".modal")
    page.focus(".modal .btn.danger-soft")                                     # pressed on purpose: it deletes
    page.keyboard.press("Enter")
    page.wait_for_selector(".modal", state="detached")
    assert page.evaluate("window.got") is True
    page.evaluate("() => { window.got = 'open'; modal({ title: 'Save?', ok: 'Save' }).then((v) => { window.got = v; }); }")
    page.wait_for_selector(".modal")
    page.keyboard.press("Enter")                                               # a safe one: Enter is its answer
    page.wait_for_selector(".modal", state="detached")
    assert page.evaluate("window.got") is True


def test_question_and_plan_cards_in_the_chat(page, server, fake_claude):
    open_chat_tab(page)
    page.fill("#chat-input", "QUESTION 颜色")
    page.keyboard.press("Enter")
    card = page.locator(".ask.ask-q.pending")
    card.wait_for()
    # nothing picked yet: Submit is never a dead button, a click takes you to the question left
    card.locator("button:has-text('提交回答')").click()
    page.wait_for_selector(".ask.ask-q.pending .q.q-missing")
    assert page.locator(".ask.ask-q.pending").count() == 1                   # nothing was sent
    card.locator(".ask-input").fill("绿色")
    page.evaluate("() => { renderLive(true); renderChat(); }")              # redraws keep what is typed in the card
    assert card.locator(".ask-input").input_value() == "绿色"
    card.locator(".ask-input").fill("")
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
    tools = target.locator(".msg-tools")                                      # under the message, like Claude Code
    assert tools.locator(".mt-time").inner_text() == "刚刚"
    # the log is drawn again once the stopped turn is saved: measure the element that stays
    wait_until(lambda: tools.bounding_box()["y"] > target.locator(".msg-user").bounding_box()["y"], msg="tools under the message")
    target.hover()
    tools.locator("button[aria-label^='分叉']").click()
    page.wait_for_selector("#chat-select .sel-label:text-matches('（分叉）')")
    assert page.input_value("#chat-input") == "排队的第二条"                   # ready to rewrite and resend
    assert page.locator(".msg-user-wrap").count() == n - 2

    # rewind inside this chat: back to before "PERM 第一条"
    first = page.locator(".msg-user-wrap").nth(1)
    first.hover()
    first.locator("button[aria-label^='回退到此消息之前']").click()
    page.click(".modal button:has-text('回退')")
    page.wait_for_function("() => document.querySelectorAll('.msg-user-wrap').length === 1")
    assert page.input_value("#chat-input") == "PERM 第一条"
    page.fill("#chat-input", "重来一次")
    page.keyboard.press("Enter")
    wait_until(lambda: calls(fake_claude)[-1]["stdin"] == "重来一次", msg="resent")
    assert "--resume-session-at" in calls(fake_claude)[-1]["argv"]
    page.wait_for_function("() => document.querySelectorAll('.msg-user-wrap').length === 2")


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
    tmp = Path(os.environ["TMP"]) / "附件.pdf"
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

    pop.locator("button", has_text="压缩上下文").click()                  # the path from the screenshot
    page.wait_for_function("() => (document.querySelector('#chat-ctx-btn').dataset.tip || '').includes('已压缩')")
    assert "NaN" not in page.inner_html("#chat-ctx-btn")
    page.click("#chat-ctx-btn")
    pop.wait_for()
    text = pop.inner_text()
    assert "已压缩" in text and "undefined" not in text and "NaN" not in text
    assert pop.locator(".cx-bar i").first.evaluate("e => e.style.width") == "0%"


def shot_float(pg, name):
    pg.wait_for_selector(".float.in")
    pg.wait_for_timeout(200)                                   # let the fade-in finish
    assert pg.locator(".float").count() == 1, f"{name}: the panel closed by itself"
    pg.screenshot(path=str(ART / f"{name}.png"))


@needs_tex
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_screenshots_of_new_parts(page, server, fake_claude, monkeypatch, tmp_path, scheme):
    page.emulate_media(color_scheme=scheme)
    repo = tmp_path / "QuizLoom"
    make_repo(repo)
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps([str(repo)]))
    add_long_bullet(server, page)
    open_entry(page, "EXP-002")
    open_facts(page)
    page.click("#evidence button:has-text('选择项目文件夹')")
    page.wait_for_selector("#evidence .ev-chip:text('AI 尚未读取')")
    page.wait_for_selector("#bullets .line-chip.long", timeout=40000)
    page.screenshot(path=str(ART / f"new-entry-{scheme}.png"))
    page.locator("#evidence").scroll_into_view_if_needed()
    page.screenshot(path=str(ART / f"new-evidence-{scheme}.png"))
    page.click("#active .ai-btn")
    shot_float(page, f"new-ai-menu-{scheme}")
    page.keyboard.press("Escape")
    page.click(".doc-top .seg button:text('中文')")
    page.wait_for_function("() => document.querySelectorAll('#bullets .line-chip.ok').length === 2", timeout=40000)
    page.locator("#bullets").scroll_into_view_if_needed()
    page.screenshot(path=str(ART / f"new-zh-{scheme}.png"))
    page.click("#subset-select")
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
    page.click(".ask.ask-plan.pending button:has-text('批准，编辑前询问')")
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


# ---------------------------------------------------------------- Applications (career-ops behind the window)
from core.i18n import Prefs  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "career-ops"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")


@pytest.fixture
def connected(data_file, tmp_path):
    target = tmp_path / "career-ops"
    shutil.copytree(FIXTURE, target)
    (target / "node_modules" / "js-yaml").mkdir(parents=True)
    Prefs(data_file.parent / "local").save(career_ops={"path": str(target)})
    return target


def open_posting_page(pg, frame, link="#to-1"):
    """A posting's page in Resume, reached from the tool's own page for it in Job search (docs/LAYOUT.md)."""
    frame.locator(link).click()
    pg.wait_for_selector("#ws-tools .ws-here")
    pg.click("#ws-tools .ws-here .btn")
    pg.click(".resume-picker #posting-page-btn")
    pg.wait_for_selector("body[data-ws=resume] #editor .verdict")


def test_not_connected_offers_install_and_an_existing_copy(page_all):
    pg = page_all
    assert pg.locator(".tab").count() == 0                                     # no Applications tab in Resume
    pg.keyboard.press("Control+2")
    pg.wait_for_selector("#jobspace .connect")
    assert pg.locator("#jobspace .connect .btn.primary").inner_text().startswith("安装 career-ops")
    assert pg.locator("#jobspace .connect").inner_text().count("MIT") == 1
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"jobs-connect-{scheme}.png"))


@needs_node
def test_a_postings_page_report_status_and_tailor(connected, page_all, server, fake_claude):
    pg = page_all
    frame = open_job_search(pg)
    open_posting_page(pg, frame)
    assert pg.locator("#editor .v-decision").inner_text() == "值得投递"
    assert not pg.locator("#editor .v-text").inner_text().startswith("Apply.")   # the bold call isn't repeated
    assert pg.locator("#editor .req").count() == 5
    assert pg.locator("#editor .rep-sec").count() >= 5
    assert "Northwind" in pg.inner_text("#chat-context")                        # the chat knows which posting is open
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"jobs-posting-{scheme}.png"))
    pg.emulate_media(color_scheme="light")

    # status goes through career-ops' own writer
    choose(pg, "#posting-status", "interview")
    wait_until(lambda: "| Interview |" in (connected / "data" / "applications.md").read_text(encoding="utf-8"), msg="tracker updated")

    # the resume to send: one of the subsets, ranked for this posting, linked with one click
    pg.wait_for_selector("#editor .resume-card .fit-row")
    assert pg.locator("#editor .resume-card .fit-row").count() == 2
    pg.click("#editor .resume-card .fit-row[data-subset='RES-TECH-ZH'] .fit-acts .btn:not(.icon)")
    linked = lambda vid: any(p.get("id") == "1" for v in state(server)["doc"]["subsets"] if v["id"] == vid for p in v.get("postings") or [])   # noqa: E731
    wait_until(lambda: linked("RES-TECH-ZH"), msg="linked")
    pg.wait_for_selector("#editor .resume-card .fit-row.on[data-subset='RES-TECH-ZH']")
    pg.screenshot(path=str(ART / "jobs-resume-to-send-light.png"))
    # back to the document from the posting's page
    pg.click("#editor .page-back .btn")
    pg.wait_for_selector("#editor .doc-wrap")
    pg.keyboard.press("Control+2")
    open_posting_page(pg, frame)

    # none fits: a new subset for this kind of role (named by the user, never after the company)
    pg.click("#editor .resume-card .fit-foot .btn.ghost")
    pg.fill(".modal input", "AI Engineer")
    pg.click(".modal .btn.primary")
    pg.wait_for_selector("#chat .msg-user")
    wait_until(lambda: any(v.get("label") == "AI Engineer" for v in state(server)["doc"]["subsets"]), msg="job created")
    vid = next(v["id"] for v in state(server)["doc"]["subsets"] if v.get("label") == "AI Engineer")
    wait_until(lambda: linked(vid) and not linked("RES-TECH-ZH"), msg="the posting moved to it")
    wait_until(lambda: any("001-northwind-ai" in c["stdin"] for c in calls(fake_claude)), msg="tailor message")
    assert pg.get_attribute("#subset-select", "data-value") == vid      # the preview shows the new job


def test_evaluate_starts_a_career_ops_chat(connected, page_all, server, fake_claude):
    pg = page_all
    open_job_search(pg)
    pg.click("#evaluate-btn")
    assert pg.locator(".eval-float .btn.primary").is_disabled()
    pg.fill("#posting-input", "https://jobs.example.com/77\nAI Engineer, Harbour City")
    pg.click(".eval-float .btn.primary")
    pg.wait_for_selector("#chat .msg-user")
    wait_until(lambda: any("jobs.example.com/77" in c["stdin"] for c in calls(fake_claude)), msg="evaluation sent")
    c = next(c for c in calls(fake_claude) if "jobs.example.com/77" in c["stdin"])
    assert Path(c["cwd"]).resolve() == connected.resolve()
    assert "career-ops" in pg.inner_text("#chat-context")
    pg.click("#evaluate-btn")
    assert pg.input_value("#posting-input") == ""                       # sent, so the box is empty again


# ---------------------------------------------------------------- Applying (the job-hunt tracker)
def test_a_scan_started_in_the_window_says_when_it_is_done(page, server, monkeypatch):
    """Scan all: the button spins while it runs; at the end a message says what it found, and the scan held the
    browser while it read and let go after (core/browser.py closes a browser nothing holds)."""
    pg = page
    hunt = server.inst.hunt
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    jobs = "https://careers.xinghe.example/campus/jobs"
    hunt.scanner.postings.set_scan_state("C1", route={"pages": [{"url": jobs, "script": "async () => ({postings: []})"}]})

    class Browser:
        holds = released = 0

        def hold(self):
            b = self

            class H:
                def __enter__(self):
                    b.holds += 1

                def __exit__(self, *exc):
                    b.holds -= 1
                    Browser.released += 1
            return H()

        def ensure(self):
            return True

        def read_pages(self, url, scripts, timeout=60.0, open_timeout=30.0):
            time.sleep(0.5)   # long enough to see the button spin
            return [{"postings": [{"title": "服务端研发工程师", "url": jobs + "/1"}, {"title": "算法工程师", "url": jobs + "/2"}]}] * len(scripts)

        def status(self):
            return {"state": "off"}
    monkeypatch.setattr(hunt.scanner, "browser", Browser())
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.click("#hunt-scan-all")
    pg.wait_for_selector("#hunt-scan-all[disabled][aria-busy=true]")    # a spinner one can see (drawn by style.css)
    assert pg.evaluate("getComputedStyle(document.querySelector('#hunt-scan-all'), '::before').animationName") == "spin"
    pg.screenshot(path=str(ART / "hunt-scan-running.png"), clip=pg.locator("#hunt-scan-all").bounding_box())
    pg.wait_for_selector("#toast.show:has-text('扫描完成')")
    assert "2 个新" in pg.inner_text("#toast") and "0 个已关闭" in pg.inner_text("#toast")
    assert Browser.released >= 1 and hunt.scanner.browser.holds == 0 and not pg.is_disabled("#hunt-scan-all")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-scan-done-{scheme}.png"))
    pg.emulate_media(color_scheme="light")


def test_a_link_the_scan_found_is_added_without_the_ai(page, server):
    """User, 2026-11-01: a posting's link is unique. Add from a link with the link of a posting a scan found:
    it becomes a role the user wants to apply to, linked to the posting, and the chat gets nothing."""
    pg = page
    hunt = server.inst.hunt
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    jobs = "https://careers.xinghe.example/campus/jobs"
    hunt.scanner.postings.merge("C1", [{"title": "服务端研发工程师", "url": jobs + "/1"}], how="moka")
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".hunt-tab.on[data-tab=apps]")
    sent = pg.locator(".msg-user").count()
    pg.click("#hunt-from-link")
    pg.fill("#fm-url", jobs + "/1?utm_source=boss")
    pg.click(".modal .btn.primary")
    pg.wait_for_selector("#toast.show:has-text('待投递')")
    pg.wait_for_selector(".hunt-wrap.is-page .ed-head h1:has-text('服务端研发工程师')")
    a = hunt.applications()
    assert [(x["role"], x["status"]) for x in a] == [("服务端研发工程师", "saved")]
    assert hunt.scanner.postings.get("P1")["app"] == a[0]["id"]
    assert pg.locator(".msg-user").count() == sent                       # no AI was asked
    pg.screenshot(path=str(ART / "hunt-link-found.png"))
    # the same link again: the role on the list, no second one
    pg.click(".hunt-tab[data-tab=apps]")
    pg.click("#hunt-from-link")
    pg.fill("#fm-url", jobs + "/1")
    pg.click(".modal .btn.primary")
    pg.wait_for_selector("#toast.show:has-text('已在列表中')")
    assert len(hunt.applications()) == 1
    # off the role's page: the next test has no A1. A click that lands while the role's page is still being drawn
    # (the toast's repaint) is lost now and then: clicked again until the page is gone
    for _ in range(5):
        pg.click(".hunt-tab[data-tab=apps]")
        try:
            pg.wait_for_selector(".hunt-wrap:not(.is-page)", timeout=4000)
            break
        except Exception:  # noqa: BLE001
            continue
    pg.wait_for_selector(".hunt-wrap:not(.is-page)")


def test_today_folds_and_its_roles_open_the_company_at_that_role(page, server):
    """User, 2026-11-02: "Today" folds away (and stays folded), and a role listed there opens the company's page
    scrolled to that role, even when the page would hide it (folded card, a search typed there, past "show more")."""
    pg = page
    hunt = server.inst.hunt
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    posts = hunt.scanner.postings
    posts.settings(keywords="研发")
    posts.merge("C1", [{"title": f"研发工程师 {i:03d}", "url": f"https://careers.xinghe.example/campus/job/{i}"} for i in range(150)], how="route")
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_selector("#hunt-today .news-item")
    first = pg.locator("#hunt-today .news-item").first
    pid, title = first.get_attribute("data-id"), first.inner_text()

    pg.screenshot(path=str(ART / "hunt-today-open.png"), clip=pg.locator("#hunt-today").bounding_box())   # by place: a quiet repaint may swap the box
    # folded: only the head stays, and it stays folded after a reload
    pg.click("#hunt-today .sn-fold")
    assert not pg.is_visible("#hunt-today .news-row") and pg.is_visible("#hunt-tracks")
    pg.mouse.move(0, 0)
    pg.wait_for_timeout(250)                                                    # past the chevron's turn
    pg.screenshot(path=str(ART / "hunt-today-folded.png"), clip=pg.locator("#hunt-today").bounding_box())   # by place: a quiet repaint may swap the box
    pg.reload()
    pg.keyboard.press("Control+3")
    pg.wait_for_selector("#hunt-today.folded")
    assert not pg.is_visible("#hunt-today .news-row")
    pg.click("#hunt-today .sn-fold")
    pg.wait_for_selector("#hunt-today .news-item")

    # what would hide the role on its page: the open-roles card folded, and a search that doesn't match it
    pg.evaluate("() => { localStorage.setItem('fold:company-postings', '1'); H.pq = 'nothing like it'; }")
    pg.click(f"#hunt-today .news-item[data-id='{pid}']")
    row = f".postings-card .post-row[data-id='{pid}']"
    pg.wait_for_selector(row)
    assert pg.locator(row + " .pr-title").inner_text() == title
    assert pg.evaluate(f"""() => {{ const r = document.querySelector("{row}").getBoundingClientRect();
        const s = document.querySelector('#fillspace .hunt-scroll').getBoundingClientRect();
        return r.top >= s.top && r.bottom <= s.bottom; }}""")                       # scrolled into view
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-today-to-role-{scheme}.png"))
    pg.emulate_media(color_scheme="light")

    # a role past the first 120 shown is still reached; "and N more" opens the list of them
    far = pg.evaluate("() => H.view.postings.filter((p) => !p.closed && !p.stale)[140].id")
    pg.evaluate(f"() => openCompanyAt('C1', '{far}')")
    pg.wait_for_selector(f".postings-card .post-row[data-id='{far}']")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.click("#hunt-today .news-more")
    pg.wait_for_selector(".hunt-wrap.is-page .postings-card")
    pg.click(".hunt-tab[data-tab=companies]")                                   # off the company's page for the next test
    pg.wait_for_selector(".hunt-wrap:not(.is-page)")


def test_an_open_cards_head_stays_at_the_top_and_folds_from_there(page, server):
    """User, 2026-11-12: scrolled down a long open-roles list, its head (fold, count, Scan) stays stuck at the top of
    the page, a sticky section header; folded from there, the folded card sits where its head was."""
    pg = page
    hunt = server.inst.hunt
    c = hunt.add_company({"name": "长列表公司", "site": "https://careers.long.example/campus"})
    hunt.scanner.postings.merge(c["id"], [{"title": f"研发工程师 {i:03d}", "url": f"https://careers.long.example/job/{i}"}
                                          for i in range(80)], how="route")
    hunt.scanner.postings.settings(keywords="研发")
    pg.evaluate("() => { localStorage.removeItem('fold:company-postings'); H.pq = ''; }")
    pg.keyboard.press("Control+3")
    pg.evaluate(f"() => openHunt('company', '{c['id']}')")
    card = ".hunt-wrap.is-page .postings-card"
    pg.wait_for_selector(card + " .post-row")
    pos = """() => { const sc = document.querySelector('#fillspace .hunt-scroll').getBoundingClientRect();
        const card = document.querySelector('.hunt-wrap.is-page .postings-card');
        return { head: card.querySelector(':scope > .card-h').getBoundingClientRect().top - sc.top,
                 card: card.getBoundingClientRect().top - sc.top, folded: card.classList.contains('folded') }; }"""
    pg.evaluate("""() => { const sc = document.querySelector('#fillspace .hunt-scroll');
        const card = document.querySelector('.hunt-wrap.is-page .postings-card');
        sc.scrollTop += card.getBoundingClientRect().top - sc.getBoundingClientRect().top + 900; }""")
    at = pg.evaluate(pos)
    assert at["card"] < -800 and abs(at["head"]) < 1, at                       # the card's top is far above; its head is at the top
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-sticky-head-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    pg.click(card + " > .card-h .card-fold")
    at = pg.evaluate(pos)
    assert at["folded"] and abs(at["card"]) < 1, at                           # folded where its head was, not off above
    pg.click(card + " > .card-h .card-fold")                                   # open again for the tests after it
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_selector(".hunt-wrap:not(.is-page)")


def test_a_system_the_ai_reads_one_company_at_a_time_is_offered_to_report(page, server):
    """User, 2026-11-12: many companies on a system the app can't read built in each cost the AI; the developer
    should hear of it. The companies table offers the report; the user sees the whole text first and sends it
    on GitHub (or copies it), or ignores it; either way the same systems aren't offered again."""
    pg = page
    hunt = server.inst.hunt
    for name in ("星河科技", "远山能源"):
        c = hunt.add_company({"name": name, "site": f"https://{len(name)}{name[0]}.joinus.example/job"})
        hunt.scanner.postings.set_scan_state(c["id"], route={"pages": [{"url": c["site"],
            "script": "async (h) => { await fetch('https://api.joinus.example/api/v2/job/search'); return {postings: []}; }"}]})
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.evaluate("() => { REPORT.at = 0; paintHunt(); }")
    pg.wait_for_selector("#hunt-report")
    assert "1 套招聘系统" in pg.inner_text("#hunt-report")
    pg.click("#hunt-report-open")
    pg.wait_for_selector(".modal .report-text")
    text = pg.inner_text(".modal .report-text")
    assert "/api/v2/job/search" in text and "api.joinus.example" in text and "星河" not in text and "远山" not in text
    assert text.startswith("以下招聘系统")                                     # in the user's language: every word read
    pg.wait_for_timeout(300)                                                    # past the dialog's fade-in
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.wait_for_timeout(300)                                                # past the buttons' colour transition
        pg.screenshot(path=str(ART / f"hunt-report-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    pg.click(".modal .acts .btn.primary")                                      # Submit on GitHub: opened, kept as sent
    pg.wait_for_selector("#hunt-report", state="detached")
    done = json.loads((hunt.dir / "reported.json").read_text(encoding="utf-8"))
    assert [v["how"] for v in done.values()] == ["issue"]
    # a new system later is offered again; ignored, it goes for good
    for name in ("长风集团", "青山制造"):
        c = hunt.add_company({"name": name, "site": f"https://{len(name)}{name[0]}.hire.example/"})
        hunt.scanner.postings.set_scan_state(c["id"], route={"pages": [{"url": c["site"],
            "script": "async (h) => { await fetch('/rest/hire/jobs/page'); return {postings: []}; }"}]})
    pg.evaluate("() => { REPORT.at = 0; paintHunt(); }")
    pg.wait_for_selector("#hunt-report")
    pg.click("#hunt-report-ignore")
    pg.wait_for_selector("#hunt-report", state="detached")
    assert sorted(v["how"] for v in json.loads((hunt.dir / "reported.json").read_text(encoding="utf-8")).values()) == ["ignored", "issue"]


def test_a_browser_about_to_close_itself_can_be_kept_open(page, server):
    """A browser work started says so a few seconds before it closes itself; Keep it open makes it the user's."""
    b = server.inst.browser
    page.wait_for_function("() => typeof F !== 'undefined' && F.browser")
    # a windowless one closing is nothing to see: no toast (2026-11-09)
    b.closing = True
    b._changed()
    page.wait_for_function("() => F.browser.closing")
    page.wait_for_timeout(300)
    assert not page.is_visible("#toast.show:has-text('几秒后关闭')")
    b.closing = False
    b._changed()
    b.shown = True
    try:
        b.closing = True
        b._changed()
        page.wait_for_selector("#toast.show:has-text('几秒后关闭')")
        page.wait_for_timeout(300)                                          # the toast's slide-in
        page.screenshot(path=str(ART / "browser-closing-toast.png"))
        page.click("#toast button:has-text('保持打开')")
        wait_until(lambda: not b.closing, msg="kept")
        page.wait_for_selector("#toast.show", state="hidden")
    finally:
        b.shown = False


def test_the_browser_chip_says_whether_it_runs_in_the_background_or_a_window(page, server):
    """The 网申 bar's chip: off; 后台 (no window), pulsing while work uses it; 窗口 once shown, where the button
    switches to it instead of showing it (2026-11-09). Each state comes the way it does live: pushed by the server."""
    pg = page
    b = server.inst.browser
    real = b.status
    base = {**real(), "found": True, "browser": "Microsoft Edge", "error": "", "closing": False, "title": ""}
    now = {"state": {"state": "off"}}
    b.status = lambda: {**base, **now["state"]}   # from the start: a load still under way answers with these states too

    def show(state: dict, mode: str) -> None:
        now["state"] = state
        b._changed()
        pg.wait_for_function(f"() => document.querySelector('#browser-here').dataset.mode === '{mode}'")

    try:
        pg.keyboard.press("Control+3")
        pg.wait_for_selector("#browser-here")
        show({"state": "off"}, "off")
        # the state is the button (user, 2026-11-09): no "Show the browser" or ⋯ of its own at the far end of the bar
        assert "浏览器未启动" in pg.inner_text("#browser-here") and pg.get_attribute("#browser-show", "aria-label") == "打开浏览器"
        assert pg.locator("#ws-tools button").count() == 2 and "显示浏览器" not in pg.inner_text("#ws-tools")
        assert pg.is_disabled("#browser-here .bchip-menu")   # there, greyed: nothing in it while the browser is off
        # read in one go: the chip is drawn again as the state comes in, and a locator can land between two drawings
        menu_right = "() => document.querySelector('#browser-here .bchip-menu').getBoundingClientRect().right"
        menu_off = pg.evaluate(menu_right)
        page_url = "https://campus.tj-sports.com/apply"
        show({"state": "running", "shown": False, "working": True, "url": page_url}, "working")
        assert pg.inner_text("#browser-here .bchip-mode") == "后台" and pg.get_attribute("#browser-show", "aria-label") == "显示浏览器"
        assert "点击可显示浏览器" in pg.get_attribute("#browser-show", "data-tip")
        assert pg.evaluate("getComputedStyle(document.querySelector('#browser-here .fb-dot')).animationName") == "pulse"
        # the ▾ stays where it was: the bar doesn't move when the browser opens
        assert abs(pg.evaluate(menu_right) - menu_off) < 1 and not pg.is_disabled("#browser-here .bchip-menu")
        pg.click("#browser-here .bchip-menu")
        assert "关闭浏览器" in pg.inner_text(".menu")
        pg.keyboard.press("Escape")
        # pointed at, it looks pressable
        pg.hover("#browser-show")
        assert pg.evaluate("getComputedStyle(document.querySelector('#browser-show')).cursor") == "pointer"
        pg.screenshot(path=str(ART / "browser-chip-background.png"), clip=pg.locator("#browser-here").bounding_box())
        show({"state": "running", "shown": False, "working": False, "url": page_url}, "working")   # a moment after the last command
        pg.wait_for_function("() => document.querySelector('#browser-here').dataset.mode === 'background'", timeout=6000)
        assert pg.evaluate("getComputedStyle(document.querySelector('#browser-here .fb-dot')).animationName") == "none"
        show({"state": "running", "shown": True, "working": True, "url": page_url}, "window")
        assert pg.inner_text("#browser-here .bchip-mode") == "已显示" and pg.get_attribute("#browser-show", "aria-label") == "切换到浏览器"
        # a long address stays inside the bar
        show({"state": "running", "shown": False, "working": False, "url": page_url + "?source=university&" + "x" * 300}, "background")
        for size, scheme in (((2000, 1000), "light"), ((900, 800), "dark"), ((1440, 900), "light")):
            pg.set_viewport_size({"width": size[0], "height": size[1]})
            pg.emulate_media(color_scheme=scheme)
            pg.wait_for_timeout(150)
            over = pg.evaluate("""() => { const c = document.querySelector('#browser-here'), bar = c.closest('.ws-tools'),
                r = c.getBoundingClientRect(), page = c.querySelector('.bchip-page').getBoundingClientRect(),
                menu = c.querySelector('.bchip-menu').getBoundingClientRect();
                return r.right > bar.getBoundingClientRect().right + 1 || bar.scrollWidth > bar.clientWidth + 1
                  || document.documentElement.scrollWidth > innerWidth || page.right > menu.left + 1 || menu.right > r.right + 1; }""")
            assert not over, (size, scheme)
            # the page's name is shown whole enough to read, or not at all: never a stub like "ca…"
            name_w = pg.evaluate("() => { const p = document.querySelector('#browser-here .bchip-page'); "
                                 "return p.offsetParent ? p.getBoundingClientRect().width : 0; }")
            assert name_w == 0 or name_w > 80, (size, name_w)
            bar = pg.locator("#browser-here").evaluate("(c) => { const r = c.closest('.topbar, header, .ws-tools').getBoundingClientRect(); "
                                                       "return {x: r.x, y: r.y, width: r.width, height: r.height}; }")
            pg.screenshot(path=str(ART / f"browser-chip-{size[0]}-{scheme}.png"), clip=bar)
        # every state, light and dark, in the bar and in a platform's browser line (for looking at)
        pg.set_viewport_size({"width": 2000, "height": 1000})
        titled = {"url": page_url + "?source=university", "title": "campus.tj-sports.com/apply?source=university"}
        states = [("off", {"state": "off"}), ("working", {"state": "running", "shown": False, "working": True, **titled}),
                  ("background", {"state": "running", "shown": False, "working": False, **titled}),
                  ("window", {"state": "running", "shown": True, "working": False, **titled})]
        for scheme in ("light", "dark"):
            pg.emulate_media(color_scheme=scheme)
            for mode, st in states:
                if mode == "background":
                    browser_mode_reset = "() => { browserWorkUntil = 0; }"
                    pg.evaluate(browser_mode_reset)
                show(st, mode)
                pg.screenshot(path=str(ART / f"browser-chip-{mode}-{scheme}.png"),
                              clip={**pg.locator("#ws-tools").bounding_box(), "height": 40, "y": 8})
        open_platform(pg)
        for scheme in ("light", "dark"):
            pg.emulate_media(color_scheme=scheme)
            for mode, st in states[1:]:
                if mode == "background":
                    pg.evaluate("() => { browserWorkUntil = 0; }")
                show(st, mode)
                pg.wait_for_selector("#fill-browser .fill-browser")
                pg.screenshot(path=str(ART / f"browser-line-{mode}-{scheme}.png"), clip=pg.locator("#fill-browser .fill-browser").bounding_box())
        pg.emulate_media(color_scheme="light")
        pg.set_viewport_size({"width": 1440, "height": 900})
    finally:
        b.status = real
        b._changed()
    # the server says when work is using the browser
    assert b.status()["working"] is False
    with b.hold():
        assert b.status()["working"] is True
    assert b.status()["working"] is False


def test_versions_show_where_they_went_while_the_roles_still_load(page, server):
    """2026-10-31: opened while another part of the window was loading the roles, the versions page never
    said which roles a version went to (sentWith only repainted after a load it had started itself)."""
    co = server.req("POST", "/api/hunt/companies", {"name": "Atlassian", "site": "careers.example.com/campus", "subset": "RES-TECH-EN"})[1]
    cid = (co.get("company") or co)["id"]
    server.req("POST", f"/api/hunt/companies/{cid}/roles", {"role": "Graduate Software Engineer"})
    server.req("POST", f"/api/hunt/companies/{cid}/sent")
    page.evaluate("async () => { H.data = null; loadHunt({ quiet: true }); await select('versions', 'RES-TECH-EN'); }")
    page.wait_for_selector(".vrow[data-n='1'] .vsent .role-chip:has-text('Graduate Software Engineer')", timeout=5000)


def test_a_search_box_takes_chinese_typed_through_an_ime(page_all, server):
    """A user report, 2026-10-23: pinyin in the Companies search broke off after each key, because every keystroke
    rebuilt the box. While the IME composes, the box stays the same element, also through a repaint from Claude's edits."""
    pg = page_all
    made = [server.req("POST", "/api/hunt/companies", {"name": n})[1]["company"]["id"] for n in ("星河 IME", "腾讯 IME")]
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_selector(".dbv-view tbody:has-text('腾讯 IME')")
    pg.wait_for_timeout(600)   # the repaints the two new companies cause land first (rebuilding an idle box is fine)
    pg.click("#table-search")
    pg.evaluate("() => { document.querySelector('#table-search').dataset.mark = '1'; }")
    cdp = pg.context.new_cdp_session(pg)
    cdp.send("Input.imeSetComposition", {"text": "xing", "selectionStart": 4, "selectionEnd": 4})
    cdp.send("Input.imeSetComposition", {"text": "xinghe", "selectionStart": 6, "selectionEnd": 6})
    server.req("PUT", f"/api/hunt/companies/{made[1]}", {"note": "repaint while composing"})   # the "hunt" event repaints
    time.sleep(0.6)
    assert pg.evaluate("() => document.querySelector('#table-search').dataset.mark") == "1"  # not rebuilt mid-word
    cdp.send("Input.insertText", {"text": "星河"})
    pg.wait_for_function("() => document.querySelector('#table-search').value === '星河'")
    pg.wait_for_selector(".dbv-view tbody:not(:has-text('腾讯 IME'))")             # the finished word filters
    assert pg.evaluate("() => [document.activeElement.id, document.activeElement.selectionStart]") == ["table-search", 2]   # the cursor stays in the box
    pg.fill("#table-search", "")
    for cid in made:
        server.req("DELETE", f"/api/hunt/companies/{cid}")


def test_a_companys_wangshen_profile_is_copied_from_its_page_and_comes_back_after_a_restart(page_all, server):
    """docs/JOB_HUNT.md step 4: the company's page keeps a copy of the user's 网申 profile there, like a platform's.
    The copy's page goes back to the company, isn't one of the Platforms, and is open again after a reload (a
    platform's page open at a restart came back as the table, 2026-11-12)."""
    pg = page_all
    cid = server.req("POST", "/api/hunt/companies", {"name": "星河网申", "site": "https://careers.example.com/campus",
                                                     "subset": "RES-TECH-EN"})[1]["company"]["id"]
    bare = server.req("POST", "/api/hunt/companies", {"name": "无网址公司"})[1]["company"]["id"]
    pg.keyboard.press("Control+3")
    pg.evaluate("(id) => openHunt('company', id)", bare)
    pg.wait_for_selector("#company-profile-add[disabled]")   # no sign-up address: nothing to copy yet, said beside it
    pg.evaluate("(id) => openHunt('company', id)", cid)
    pg.click("#company-profile-add")
    pg.wait_for_selector(".fill-card.fill-start")
    assert "星河网申" in pg.inner_text(".fill-head h1") and pg.inner_text(".hunt-back").strip() == "星河网申"
    assert pg.evaluate("() => H.tab") == "companies" and not pg.query_selector("#fill-sites")
    assert pg.inner_text(".hunt-tab[data-tab=platforms] .n") == "2"   # LinkedIn and BOSS直聘 only
    pg.reload()
    pg.wait_for_selector(".fill-card.fill-start")
    assert "星河网申" in pg.inner_text(".fill-head h1")
    pg.click(".hunt-back")
    pg.wait_for_selector("#company-profile-open")
    assert "尚未读取" in pg.inner_text("#company-profile")
    # a platform's page comes back after a reload too
    pg.evaluate("() => openHunt('platform', 'linkedin')")
    pg.wait_for_selector("#fill-sites")
    pg.reload()
    pg.wait_for_selector("#fill-sites .fs-site.on[data-site=linkedin]")
    # the Platforms table lists platforms only
    pg.click(".hunt-tab[data-tab=platforms]")
    pg.wait_for_selector(".dbv-view[data-table=platforms] tbody tr")
    assert "星河网申" not in pg.inner_text(".dbv-view[data-table=platforms]")
    # and the copy can go: off the company's page, with what was read kept for when it comes back
    pg.evaluate("(id) => openHunt('company', id)", cid)
    pg.wait_for_selector("#company-profile-open")
    pg.click(".ed-acts .btn.icon.ghost")
    pg.click(".menu button:has-text('停止保存该网站简历的副本')")
    pg.click(".modal .btn.danger-soft")
    pg.wait_for_selector("#company-profile-add")
    assert not pg.query_selector("#company-profile-open")


def test_the_company_table_shows_while_changes_keep_coming(page_all, server):
    """A user report, 2026-11-12: the Companies tab said "Loading…" for minutes. A scan says "changed" every few
    seconds, each change asked for the table again while the last answer was still on its way, and the table was
    painted only once the changes stopped: never, while the scan ran. Each answer is painted as it comes now."""
    pg = page_all
    cid = server.req("POST", "/api/hunt/companies", {"name": "星河 Loading"})[1]["company"]["id"]
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_selector(".dbv-view tbody:has-text('星河 Loading')")
    pg.evaluate("""() => {
      // the overview's own fetch (huntFetch, with its ETag), slow, and each answer a new one as a scan's are
      window.__fetch = huntFetch;
      huntFetch = async () => { await new Promise((r) => setTimeout(r, 300)); const d = await window.__fetch(); return { ...d }; };
      H.data = null; paintFill();
      window.__tick = setInterval(() => loadHunt({ quiet: true }), 100);   // what a scan's "hunt" events do
    }""")
    try:
        pg.wait_for_selector(".dbv-view tbody:has-text('星河 Loading')", timeout=3000)
        assert pg.evaluate("() => !!H.loading")   # still loading again and again: the table didn't wait for that
    finally:
        pg.evaluate("() => { clearInterval(window.__tick); huntFetch = window.__fetch; }")
        server.req("DELETE", f"/api/hunt/companies/{cid}")


def test_phd_records_link_to_each_other_in_the_window(page_all, server):
    """2026-11-12: PhD applications are linked tables. A programme's university is picked in its cell (or made there),
    the university's page lists its programmes, and a university deleted leaves its programmes unlinked."""
    pg = page_all
    base = "/api/plugins/phd-applications/records"
    server.req("PUT", "/api/plugins/phd-applications", {"on": True})
    prog = server.req("POST", f"{base}/programs", {"title": "PhD in Computer Science"})[1]["id"]
    pg.wait_for_function("() => wsPlugin('p.phd-applications')")
    pg.evaluate("() => { PW.col['phd-applications'] = 'programs'; setWorkspace('p.phd-applications'); }")
    cell = "#pluginspace .dbv-view[data-table='p.phd-applications.programs'] tbody tr td[data-key=university]"
    pg.click(cell)
    pg.fill(".plugin-relpick input", "Australian National University")
    pg.click(".plugin-relpick .dbv-newopt")                                   # made from the picker, and linked
    pg.wait_for_selector(f"{cell} .plugin-rel:has-text('Australian National University')")
    uni = server.req("GET", "/api/plugins")[1]["records"]["phd-applications"]["universities"][0]
    assert server.req("GET", "/api/plugins")[1]["records"]["phd-applications"]["programs"][0]["university"] == uni["id"]
    pg.click(f"{cell} .plugin-rel")                                           # the chip opens the university
    pg.wait_for_selector("#pluginspace .plugin-backlinks .plugin-linkrow:has-text('PhD in Computer Science')")
    pg.click("#pluginspace .plugin-backlinks .plugin-linkrow")                # and its programme opens from there
    pg.wait_for_selector("#pluginspace .hunt-head h1:has-text('PhD in Computer Science')")
    server.req("DELETE", f"{base}/universities/{uni['id']}")
    pg.wait_for_function("() => { const b = document.querySelector('#pluginspace .plugin-relbtn[data-key=university]');"
                         " return b && !b.parentElement.querySelector('.plugin-rel'); }")   # unlinked, shown by itself
    assert "university" not in server.req("GET", "/api/plugins")[1]["records"]["phd-applications"]["programs"][0]
    server.req("DELETE", f"{base}/programs/{prog}")
    server.req("PUT", "/api/plugins/phd-applications", {"on": False})


def test_a_change_shows_while_the_user_types(page_all, server):
    """A user report, 2026-10-23: a change from Claude waited for a switch of views while the cursor was in a box.
    Now the table follows at once, and the box keeps what is typed and the cursor."""
    pg = page_all
    cid = server.req("POST", "/api/hunt/companies", {"name": "打字测试 Co"})[1]["company"]["id"]
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.click("#table-search")
    pg.keyboard.type("打字测试")
    pg.keyboard.press("ArrowLeft")
    server.req("PUT", f"/api/hunt/companies/{cid}", {"industry": "测试行业 XYZ"})
    pg.wait_for_selector(".dbv-view tbody:has-text('测试行业 XYZ')")
    assert pg.evaluate("() => [document.activeElement.id, document.activeElement.value, document.activeElement.selectionStart]") \
        == ["table-search", "打字测试", 3]
    pg.keyboard.type("!")
    assert pg.input_value("#table-search") == "打字测!试"
    pg.fill("#table-search", "")
    server.req("DELETE", f"/api/hunt/companies/{cid}")


def picture(side: int, rgba: tuple[int, int, int, int], mark: tuple[int, int, int, int] | None = None) -> bytes:
    """A real png, side × side, all one colour (alpha 0 = blank), with a `mark` colour over its left half."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    half = side // 2 if mark else 0
    rows = b"".join(b"\x00" + bytes(mark or rgba) * half + bytes(rgba) * (side - half) for _ in range(side))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_a_logo_is_looked_at_before_it_is_kept(page_all, server):
    """A user report, 2026-10-23: logos came in blank. The window draws each picture before it is kept: a blank
    or tiny one is refused with the reason, a good one is kept and drawn as a png for Claude to look at."""
    import http.server
    import threading
    pics = {"/blank.png": picture(64, (0, 0, 0, 0)), "/white.png": picture(64, (255, 255, 255, 255)),
            "/tiny.png": picture(16, (30, 60, 200, 255), (240, 140, 0, 255)), "/block.png": picture(96, (220, 0, 60, 255)),
            "/good.png": picture(96, (30, 60, 200, 255), (240, 140, 0, 255))}

    class Pics(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(pics[self.path])

        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pics)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    pg = page_all
    try:
        cid = server.req("POST", "/api/hunt/companies", {"name": "Logo 测试"})[1]["company"]["id"]
        fill = lambda name: server.req("POST", "/api/hunt/do", {"cmd": "fill", "args": {"ref": cid, "fields": {"logo_url": f"{base}/{name}"}}})[1]
        for name in ("blank.png", "white.png", "tiny.png", "block.png"):   # block: a plain red square, no mark on it
            r = fill(name)
            assert r["logo_error"] and not r["company"]["company"]["logo"] and not r["logo_look"], (name, r)
        assert "16" in fill("tiny.png")["logo_error"]
        r = fill("good.png")
        assert not r["logo_error"] and r["company"]["company"]["logo"] == f"{cid}.png"
        look = Path(r["logo_look"])
        assert look.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and look.parent.name == "logo-look"
        look.unlink()
        # a wrong picture sent after a good one leaves the good one
        assert fill("blank.png")["logo_error"]
        assert server.req("GET", f"/api/hunt/companies/{cid}")[1]["company"]["logo"] == f"{cid}.png"
        # `hunt.py logo`: the same look at the logo already there
        r = server.req("POST", "/api/hunt/do", {"cmd": "logo", "args": {"ref": cid}})[1]
        assert r["checked"] and not r["problem"] and Path(r["look"]).is_file()
        Path(r["look"]).unlink()
        server.req("DELETE", f"/api/hunt/companies/{cid}")
    finally:
        httpd.shutdown()


def test_a_roles_description_gives_its_skills_and_says_when_it_reads_like_another_track(page_all, server):
    """User, 2026-11-14: roles are sorted into tracks by title alone, with no AI; once one is picked its description
    is read, with no AI, for the skills it asks for (a column of their own) and for a track that fits it better."""
    pg = page_all
    server.req("PUT", "/api/hunt/scan-settings", {"tracks": [{"name": "AI 全栈", "keywords": ["AI+应用开发"]},
                                                            {"name": "AI Agent", "keywords": ["Agent", "智能体"]}]})
    cid = server.req("POST", "/api/hunt/companies", {"name": "星河科技"})[1]["company"]["id"]
    aid = server.req("POST", f"/api/hunt/companies/{cid}/roles", {"role": "AI应用开发工程师"})[1]["application"]["id"]
    server.req("PUT", f"/api/hunt/apps/{aid}", {"jd": "岗位职责\n1. 负责 AI Agent 的设计与开发；\n2. 负责 Agent 的工具调用与记忆；\n"
                                                "3. 优化 Agent 评测，提升智能体任务成功率。\n任职要求\n1. 熟练掌握 Python；\n"
                                                "2. 熟悉 LangChain、LangGraph 等框架；\n3. 有 RAG 项目经验者优先。"})
    app = lambda: server.req("GET", f"/api/hunt/apps/{aid}")[1]["application"]  # noqa: E731
    pg.keyboard.press("Control+3")
    row = f".dbv-view tr[data-id='{aid}']"
    pg.wait_for_selector(f"{row} td[data-key=track] .c-track-hint")             # by its title AI 全栈, its duties say Agent
    assert pg.inner_text(f"{row} td[data-key=track]").strip() == "AI 全栈"
    assert {"Python", "LangChain", "LangGraph", "RAG"} <= set(pg.locator(f"{row} td[data-key=skills] .trk-tag").all_inner_texts())
    pg.click(f"{row} .dbv-title")
    pg.wait_for_selector("#app-track-hint")
    assert "AI Agent" in pg.inner_text("#app-track-hint") and "Agent" in pg.inner_text("#app-track-hint")
    pg.click("#app-track-move")                                                 # the user moves it: nothing moves by itself
    wait_until(lambda: app()["track"] == "T2", msg="moved")
    pg.wait_for_selector("#app-track-hint", state="detached")
    assert "AI Agent" in pg.inner_text("#app-track")
    # a wrong skill taken off, a missing one typed in: the box stays where the user is typing
    pg.click("#app-skills .sk-chip:has-text('RAG') .sk-x")
    wait_until(lambda: "RAG" not in app()["skills"], msg="taken off")
    pg.fill("#app-skill-add", "Docker")
    pg.press("#app-skill-add", "Enter")
    wait_until(lambda: "Docker" in app()["skills"], msg="typed in")
    pg.wait_for_selector("#app-skills .sk-chip:has-text('Docker')")
    assert pg.evaluate("document.activeElement && document.activeElement.id") == "app-skill-add"
    assert "RAG" not in pg.locator("#app-skills .sk-name").all_inner_texts()


def test_the_tables_work_like_a_notion_database(page_all, server):
    """docs/JOB_HUNT.md, 2026-10-19: edit cells in place, move / resize / hide columns, filter, sort, group,
    several views as tabs and a board by status; the views are saved with the data."""
    pg = page_all
    c1 = server.req("POST", "/api/hunt/companies", {"name": "星河科技"})[1]["company"]["id"]
    c2 = server.req("POST", "/api/hunt/companies", {"name": "腾讯"})[1]["company"]["id"]
    a1 = server.req("POST", f"/api/hunt/companies/{c1}/roles", {"role": "服务端研发工程师"})[1]["application"]["id"]
    a2 = server.req("POST", f"/api/hunt/companies/{c1}/roles", {"role": "算法工程师"})[1]["application"]["id"]
    a3 = server.req("POST", f"/api/hunt/companies/{c2}/roles", {"role": "后台开发"})[1]["application"]["id"]
    server.req("POST", f"/api/hunt/apps/{a3}/status", {"status": "rejected"})
    app = lambda aid: server.req("GET", f"/api/hunt/apps/{aid}")[1]["application"]
    views = lambda: server.req("GET", "/api/hunt/views")[1]
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".dbv-view[data-table=apps] .dbv-tab.on[data-view=v1]")
    row = lambda aid: f".dbv-view tr[data-id='{aid}']"
    pg.wait_for_function("() => document.querySelectorAll('.dbv-view tbody tr').length === 2")                    # "Active": the rejected role is filtered out
    assert pg.locator(".dbv-chip[data-key=status]").count() == 1

    # a status, picked in its cell: the same event as on the role's page
    pg.click(f"{row(a1)} td[data-key=status]")
    pg.click(".float [role=option][data-value=interview]")
    wait_until(lambda: app(a1)["status"] == "interview", msg="status saved")
    assert server.req("GET", f"/api/hunt/apps/{a1}")[1]["events"][-1]["to"] == "interview"
    pg.wait_for_selector(f"{row(a1)} td[data-key=status] .s-interview")
    # text, typed over the cell: Enter saves, Esc cancels
    pg.click(f"{row(a1)} td[data-key=referral]")
    pg.fill(".float .dbv-textbox", "NTA8X2")
    pg.press(".float .dbv-textbox", "Enter")
    wait_until(lambda: app(a1)["referral"] == "NTA8X2", msg="referral code saved")
    pg.wait_for_selector(f"{row(a1)} td[data-key=referral]:has-text('NTA8X2')")
    pg.click(f"{row(a1)} td[data-key=referral]")
    pg.fill(".float .dbv-textbox", "不要")
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".float", state="detached")
    time.sleep(0.4)
    assert app(a1)["referral"] == "NTA8X2"
    # the next step: what and when in one cell, no deadline column (docs/JOB_HUNT.md, 2026-10-21); a role not applied
    # to yet shows its 投递 event, only when the posting gave a date or said 招满即止: nothing known is nothing shown (2026-10-26)
    due = lambda a: next(e for e in a["schedule"] if e.get("kind") == "apply")  # noqa: E731
    assert pg.locator(".dbv-view th[data-key=deadline]").count() == 0
    assert pg.inner_text(f"{row(a2)} td[data-key=events]").strip() == ""      # no date known: none made up
    assert pg.inner_text(f"{row(a1)} td[data-key=events]").strip() == ""      # interviewing, nothing booked: applying is done
    server.req("PUT", f"/api/hunt/apps/{a2}", {"apply_by": "2030-11-01"})
    pg.wait_for_selector(f"{row(a2)} td[data-key=events]:has-text('投递截止') .c-left:has-text('还有')")
    server.req("PUT", f"/api/hunt/apps/{a2}", {"apply_by": "2020-11-01"})
    pg.wait_for_selector(f"{row(a2)} td[data-key=events] .c-booked.late .c-left:has-text('已过')")
    server.req("PUT", f"/api/hunt/apps/{a2}", {"apply_by": "rolling"})
    pg.wait_for_selector(f"{row(a2)} td[data-key=events]:has-text('招满即止')")
    pg.click(f"{row(a2)} td[data-key=events]")                                # opens the role, at its events
    pg.wait_for_selector(".bk-item.is-due")
    pg.click(".hunt-back")
    server.req("PUT", f"/api/hunt/apps/{a2}", {"apply_by": ""})
    pg.wait_for_function(f"() => document.querySelector(`{row(a2)} td[data-key=events]`).innerText.trim() === ''")
    # no batch (docs/JOB_HUNT.md, 2026-10-30); the resume that went out is picked over its cell, not on the role's
    # page: the subset, then its version (as it is now, or one kept before), saved only by Mark as sent
    assert pg.locator(".dbv-view th[data-key=batch]").count() == 0
    corner = {"x": 6, "y": 6}                                                # the cell, not its Resume button
    pg.click(f"{row(a1)} td[data-key=sent]", position=corner)
    pg.wait_for_selector(".float.sentpop #sentpop-subsets .lb-opt")
    assert pg.locator(".hunt-back").count() == 0                             # still the table
    assert pg.locator("#sentpop-send").is_disabled()                          # no subset yet: nothing to send
    pg.locator("#sentpop-subsets .lb-opt").first.click()
    pg.wait_for_selector("#sentpop-versions .lb-opt.on:has-text('当前内容')")
    time.sleep(0.3)
    assert not app(a1)["sent"]                                               # a pick saves nothing
    fits = "() => { const p = document.querySelector('.float.sentpop'); return p.scrollWidth <= p.clientWidth && [...p.querySelectorAll('*')].every((x) => x.getBoundingClientRect().right <= p.getBoundingClientRect().right + 1); }"
    assert pg.evaluate(fits)
    pg.screenshot(path=str(ART / "hunt-sent-pop-light.png"))
    pg.click("#sentpop-send")
    wait_until(lambda: len(app(a1)["sent"]) == 1, msg="sent as it is now")
    sid, n = app(a1)["sent"][0]["subset"], app(a1)["sent"][0]["n"]
    assert app(a1)["subset"] == sid and app(a1)["status"] == "interview"     # it goes out with that subset from now on
    kept = server.req("GET", "/api/state")[1]["subset_versions"][sid]["count"]
    pg.wait_for_selector(".float", state="detached")
    pg.click(f"{row(a1)} td[data-key=sent]", position=corner)
    pg.wait_for_selector(f"#sentpop-subsets .lb-opt.on")                     # the role's own subset, picked already
    pg.click(f"#sentpop-versions .lb-opt:has-text('v{n}') >> nth=-1")      # the version kept before
    pg.wait_for_selector(".float .sentpop-row")                              # and what went out so far
    pg.click("#sentpop-send")
    wait_until(lambda: [s["n"] for s in app(a1)["sent"]] == [n, n], msg="sent as the older version")
    assert server.req("GET", "/api/state")[1]["subset_versions"][sid]["count"] == kept   # no new version made
    # places (docs/JOB_HUNT.md, 2026-10-27): picked from a list as you type, in the window's language; several
    # written at once are each found in it; remote must say its region; saved when the panel closes
    server.req("PUT", f"/api/hunt/apps/{a1}", {"places": ["杭州"]})   # another role's, offered as a suggestion below
    pg.click(f"{row(a2)} td[data-key=places]")
    pg.fill(".float .pl-input", "南京、上海")
    pg.press(".float .pl-input", "Enter")
    pg.wait_for_selector(".float .pl-list .pl-chip:has-text('上海')")
    pg.fill(".float .pl-input", "syd")
    pg.wait_for_selector(".float .pl-opt.on:has-text('悉尼'):has-text('澳大利亚')")    # the bigger Sydney first, told apart
    assert "加拿大" in pg.locator(".float .pl-opt").nth(1).inner_text()
    pg.screenshot(path=str(ART / "hunt-places-suggest-light.png"))
    pg.press(".float .pl-input", "ArrowDown")
    pg.press(".float .pl-input", "ArrowUp")
    pg.press(".float .pl-input", "Enter")
    pg.wait_for_selector(".float .pl-list .pl-chip:has-text('悉尼')")
    pg.click(".float .pl-add .seg button[data-value=remote]")
    pg.press(".float .pl-input", "Enter")                       # no region: not added
    pg.wait_for_selector(".toast:has-text('不限')")
    assert pg.locator(".float .pl-list .pl-chip").count() == 3
    pg.fill(".float .pl-input", "APAC")
    pg.wait_for_selector(".float .pl-opt.on:has-text('亚太地区（APAC）')")
    pg.press(".float .pl-input", "Enter")
    pg.wait_for_selector(".float .pl-chip.k-remote:has-text('亚太地区（APAC）')")
    pg.screenshot(path=str(ART / "hunt-places-cell-light.png"))
    pg.keyboard.press("Escape")
    wait_until(lambda: app(a2)["places"] == [{"kind": "onsite", "area": "Nanjing", "id": "g1799962"},
                                             {"kind": "onsite", "area": "Shanghai", "id": "g1796236"},
                                             {"kind": "onsite", "area": "Sydney", "id": "g2147714"},
                                             {"kind": "remote", "area": "Asia Pacific (APAC)", "id": "r:apac"}], msg="places saved")
    pg.wait_for_selector(f"{row(a2)} td[data-key=places] .pl-chip.k-remote:has-text('远程 · 亚太地区（APAC）')")
    # the title opens the row's page; other cells don't
    pg.click(f"{row(a2)} .dbv-title")
    pg.wait_for_selector("#app-status")
    # the same places on the role's page: one removed, one suggested from another role
    pg.click("#app-places .pl-chip:has-text('上海') .pl-x")
    wait_until(lambda: [p["area"] for p in app(a2)["places"]] == ["Nanjing", "Sydney", "Asia Pacific (APAC)"], msg="a place removed on the page")
    pg.click("#app-places .pl-chip:has-text('悉尼') .pl-x")
    wait_until(lambda: [p["area"] for p in app(a2)["places"]] == ["Nanjing", "Asia Pacific (APAC)"], msg="another removed")
    pg.click("#app-places .bk-chip:has-text('杭州')")
    wait_until(lambda: [p["area"] for p in app(a2)["places"]] == ["Nanjing", "Asia Pacific (APAC)", "Hangzhou"], msg="a place suggested and added")
    # what the list doesn't have is added as written, when the user says so (and looked up on OpenStreetMap,
    # which is off in the tests)
    pg.wait_for_timeout(300)
    pg.wait_for_function("() => !H.loading")
    pg.fill("#app-places .pl-input", "滨江园区")
    pg.click("#app-places .pl-asis:has-text('滨江园区')")
    wait_until(lambda: app(a2)["places"][-1] == {"kind": "onsite", "area": "滨江园区"}, msg="a place kept as written")
    pg.wait_for_selector("#app-places .pl-chip.pl-free:has-text('滨江园区')")
    pg.wait_for_timeout(300)
    pg.wait_for_function("() => !H.loading")                                  # the saved page is back (it repaints, cursor kept)
    pg.locator("#app-places").scroll_into_view_if_needed()
    pg.screenshot(path=str(ART / "hunt-places-page-light.png"))
    pg.click(".hunt-back")
    pg.wait_for_selector(".dbv-view[data-table=apps]")

    # a heading's menu sorts; the sort is the view's, saved with the data
    pg.click(".dbv-th[data-key=role] .dbv-thbtn")
    pg.click(".float .menu-item:has-text('降序排列')")
    pg.wait_for_selector(".dbv-th[data-key=role] .dbv-thbtn svg >> nth=1")
    assert pg.locator(".dbv-view tbody tr").first.get_attribute("data-id") == a2   # 算法 before 服务端, by the title, descending
    wait_until(lambda: views()["apps"]["views"][0]["sorts"] == [{"key": "role", "dir": "desc"}], msg="sort saved")
    # hide a column in Properties; move one by dragging its heading; resize one by its edge
    pg.click("#dbv-apps-props")
    pg.click(".float .dbv-proprow[data-key=last] .btn:not(.drag-handle)")
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".dbv-th[data-key=last]", state="detached")
    pg.drag_and_drop(".dbv-th[data-key=referral]", ".dbv-th[data-key=company]", target_position={"x": 4, "y": 10})
    wait_until(lambda: pg.locator(".dbv-th").evaluate_all("(xs) => xs.map((x) => x.dataset.key)")[1:3] == ["referral", "company"], msg="column moved")
    before = pg.locator(".dbv-th[data-key=status]").bounding_box()["width"]   # the table fits the pane until a column is sized
    box = pg.locator(".dbv-th[data-key=status] .dbv-resize").bounding_box()
    pg.mouse.move(box["x"] + 3, box["y"] + 10)
    pg.mouse.down()
    pg.mouse.move(box["x"] + 83, box["y"] + 10, steps=5)
    pg.mouse.up()
    def saved_col(key):
        return next(c for c in views()["apps"]["views"][0]["columns"] if c["key"] == key)
    wait_until(lambda: abs(saved_col("status")["width"] - (before + 80)) <= 2 and saved_col("last")["hidden"], msg="width and hidden saved")
    assert saved_col("role")["width"]                                           # the others keep the widths they had on screen
    assert pg.locator(".dbv-body[data-scrolls] table.sized").count() == 1

    # another view: everything; a filter typed in; grouped by status
    pg.click(".dbv-tab[data-view=v2]")
    pg.wait_for_selector(".dbv-tab.on[data-view=v2]")
    assert pg.locator(".dbv-view tbody tr").count() == 3
    pg.click("#dbv-apps-filter")
    pg.click(".float .menu-item:has-text('公司')")
    pg.fill(".float .dbv-fin", "腾")
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".dbv-chip[data-key=company]")
    assert pg.locator(".dbv-view tbody tr").count() == 1
    pg.click(".dbv-chip[data-key=company]")
    pg.click(".float .btn[aria-label='删除此筛选']")
    pg.wait_for_selector(".dbv-chip[data-key=company]", state="detached")
    pg.click("#dbv-apps-group")
    pg.click(".float .menu-item:has-text('状态')")
    pg.wait_for_selector(".dbv-group[data-group=rejected]")
    assert pg.locator(".dbv-group").count() == 3                             # 待投递, 面试中, 未通过: empty groups don't show
    pg.click(".dbv-group[data-group=rejected] .dbv-fold")
    pg.wait_for_selector(f".dbv-group[data-group=rejected] {row(a3)[len('.dbv-view '):]}", state="detached")

    # the board: a column per status; dragging a card changes its status
    pg.click(".dbv-tab[data-view=v3]")
    pg.wait_for_selector(".dbv-board .dbv-col[data-group=applied]")
    pg.drag_and_drop(f".dbv-card[data-id='{a2}']", ".dbv-col[data-group=applied]")
    wait_until(lambda: app(a2)["status"] == "applied", msg="dragged to Applied")
    pg.wait_for_selector(f".dbv-col[data-group=applied] .dbv-card[data-id='{a2}']")
    # a new view, renamed; then deleted
    pg.click(".dbv-addview")
    pg.click(".float .menu-item:has-text('表格')")
    pg.wait_for_selector(".dbv-tab.on[data-view=v7]")
    pg.click(".dbv-tab.on")
    pg.click(".float .menu-item:has-text('重命名')")
    pg.fill("#fm-name", "本周截止")
    pg.click(".modal .btn.primary")
    pg.wait_for_selector(".dbv-tab.on:has-text('本周截止')")
    wait_until(lambda: [v["name"] for v in views()["apps"]["views"]][-1] == "本周截止", msg="view saved")
    pg.click(".dbv-tab[data-view=v3]")
    pg.wait_for_selector(".dbv-board")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-board-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    pg.click(".dbv-tab[data-view=v7]")
    pg.click(".dbv-tab.on")
    pg.click(".float .menu-item:has-text('删除此视图')")
    pg.click(".modal .btn.danger-soft")                                        # its filters and columns go: asked first
    pg.wait_for_selector(".dbv-tab[data-view=v7]", state="detached")
    no_native(pg)

    # the calendar: what is booked, on its day; a click opens the role (docs/JOB_HUNT.md, 2026-10-21)
    day = pg.evaluate("() => dbvYmd(new Date())")
    server.req("POST", f"/api/hunt/apps/{a1}/schedule", {"title": "一面", "at": day})   # all day: still to come whatever the time now
    server.req("PUT", f"/api/hunt/apps/{a2}", {"apply_by": day})
    pg.click(".dbv-tab[data-view=v4]")
    pg.wait_for_selector(f".dbv-calday.today[data-day='{day}'] .dbv-item.k-event:has-text('一面')")
    # two requests, two repaints: the deadline may land a moment after the interview (raced, 2026-11-12)
    pg.wait_for_selector(f".dbv-calday[data-day='{day}'] .dbv-item.k-deadline")
    assert pg.locator(f".dbv-calday[data-day='{day}'] .dbv-item.k-deadline").count() == 1
    assert pg.locator("#dbv-apps-group").count() == 0                       # no columns or groups on a calendar
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-calendar-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    pg.click(".dbv-calbar .btn[aria-label='下个月']")
    # today may still show, greyed, among the next month's leading days (the last week of a month): not as a day of it
    pg.wait_for_selector(f".dbv-calday[data-day='{day}']:not(.out)", state="detached")
    pg.click(".dbv-calbar .btn:has-text('今天')")
    pg.click(f".dbv-calday[data-day='{day}'] .dbv-item.k-event")
    pg.wait_for_selector(".bk-item:has-text('一面')")
    pg.wait_for_timeout(600)   # the role page's own repaints land before the form is filled
    # on the role's page: a common one picked, a date and time, Add
    pg.click(".bk-new .bk-chip:has-text('二面')")
    pg.fill(".bk-new .bk-day", "2030-01-15")
    pg.fill(".bk-new .bk-time", "09:30")
    pg.click("#bk-add")
    booked = lambda: [e for e in app(a1)["schedule"] if e.get("kind") != "apply"]  # noqa: E731
    # the usual rounds are built in (no title of their own: they read in the interface's language)
    wait_until(lambda: [(e.get("kind"), e["title"]) for e in booked()] == [("round1", ""), ("round2", "")], msg="booked from the page")
    assert booked()[1]["at"] == "2030-01-15 09:30"
    pg.wait_for_selector(".bk-item:has-text('二面')")
    assert pg.input_value(".bk-new .bk-day") == ""                            # no date filled in for the user
    # its 投递, added by hand: it opens for its date; then 招满即止 from its own edit (the date goes)
    assert pg.locator(".bk-item.is-due").count() == 0
    pg.click("#bk-add-due")
    pg.fill(".bk-edit .bk-day", "2030-01-10")
    pg.click(".bk-edit .btn.primary")
    wait_until(lambda: due(app(a1))["at"] == "2030-01-10", msg="apply-by dated from the page")
    pg.hover(".bk-item.is-due")
    pg.click(".bk-item.is-due .btn[aria-label='修改']")
    pg.click(".bk-edit .dl-rolling")
    assert pg.input_value(".bk-edit .bk-day") == ""
    pg.click(".bk-edit .btn.primary")
    wait_until(lambda: (due(app(a1))["at"], due(app(a1))["rolling"]) == ("", True), msg="招满即止, said by the user")
    pg.wait_for_selector(".bk-item.is-due .bk-when:has-text('招满即止')")
    server.req("PUT", f"/api/hunt/apps/{a1}", {"apply_by": "2030-01-10"})
    # in interviews, the next round waits in the Add row; booking one moves the status forward, with Undo
    pg.wait_for_function("() => document.querySelector('.bk-new .bk-title').value === '三面'")
    server.req("POST", f"/api/hunt/apps/{a1}/status", {"status": "applied"})
    pg.wait_for_function("() => document.querySelector('.bk-new .bk-title').value === ''")
    pg.fill(".bk-new .bk-title", "三面")
    pg.fill(".bk-new .bk-day", "2030-01-20")
    pg.click("#bk-add")
    wait_until(lambda: app(a1)["status"] == "interview", msg="booking moved the status")
    pg.click("#toast.show:has-text('状态已改为') .btn:has-text('撤销')")
    wait_until(lambda: app(a1)["status"] == "applied", msg="undone")
    server.req("POST", f"/api/hunt/apps/{a1}/status", {"status": "interview"})
    pg.wait_for_function("() => document.querySelector('.bk-new .bk-title').value === '终面'")
    assert pg.evaluate("() => { const w = document.querySelector('.bk-item').closest('.hunt-wrap'); return w.offsetWidth >= w.parentNode.clientWidth - 2; }")   # the page uses the whole pane
    pg.screenshot(path=str(ART / "hunt-role-events-light.png"))
    pg.click(".hunt-back")
    # the Gantt chart: a bar per role, what is booked on it
    pg.click(".dbv-tab[data-view=v5]")
    pg.wait_for_selector(f".dbv-grow[data-id='{a1}'] .dbv-gbar")
    assert pg.locator(f".dbv-grow[data-id='{a1}'] .dbv-gitem").count() == 4          # 一面, 二面, 三面 and its deadline
    assert pg.locator(f".dbv-grow[data-id='{a1}'] .dbv-gitem.k-deadline").count() == 1
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-gantt-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    no_native(pg)

    # the world map (docs/JOB_HUNT.md, 2026-10-21): a pin per city; remote and unknown places beside it
    server.req("PUT", f"/api/hunt/apps/{a1}", {"places": ["杭州", "滨江园区"]})
    pg.click(".dbv-tab[data-view=v6]")
    pg.wait_for_selector(".dbv-map .dbv-land[d]")
    pg.wait_for_selector(".dbv-pin")
    assert pg.locator("#dbv-apps-group").count() == 0 and pg.locator("#dbv-apps-sort").count() == 0
    pg.wait_for_selector(".dbv-offrow:has-text('远程 · 亚太地区（APAC）')")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"hunt-map-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    # a place the city list doesn't know: put on the map by a click, where the user says
    pg.click(".dbv-offrow[data-area='滨江园区'] .dbv-place")
    pg.wait_for_selector(".dbv-pinbar:has-text('滨江园区')")
    pg.mouse.click(*pg.evaluate("() => { const r = document.querySelector('.dbv-mapstage').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }"))
    wait_until(lambda: (server.req("GET", "/api/hunt")[1]["geo"].get("滨江园区") or {}).get("pinned"), msg="pinned where clicked")
    pg.wait_for_selector(".dbv-offrow[data-area='滨江园区']", state="detached")
    # a pin lists its roles; close ones join until zoomed in
    for _ in range(4):
        pin = pg.locator(".dbv-pin[data-area*='南京']").first
        joined = "many" in (pin.get_attribute("class") or "")
        pin.click()
        if not joined:
            break
    pg.wait_for_selector(".float.dbv-spot .dbv-maprow:has-text('算法工程师')")
    pg.click(".float.dbv-spot .dbv-maprow:has-text('算法工程师')")
    pg.wait_for_selector("#app-status")
    pg.click(".hunt-back")
    pg.wait_for_selector(".dbv-map")
    # the companies' map: where they hire, and the headquarters
    server.req("PUT", f"/api/hunt/companies/{c1}", {"places": ["上海", "北京", "remote: 中国大陆"], "hq": "深圳"})
    pg.click(".hunt-tab[data-tab=companies]")
    pg.click(".dbv-tab[data-view=v2]")
    pg.wait_for_selector(".dbv-pin.hq[data-area='深圳']")
    pg.screenshot(path=str(ART / "hunt-companies-map-light.png"))
    pg.click(".dbv-tab[data-view=v1]")
    wait_until(lambda: views()["companies"]["active"] == "v1", msg="companies back to their table")
    pg.click(".hunt-tab[data-tab=apps]")
    no_native(pg)

    # the views come back after a reload: they live with the data
    pg.click(".dbv-tab[data-view=v1]")
    wait_until(lambda: views()["apps"]["active"] == "v1", msg="active view saved")
    pg.reload()
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".dbv-tab.on[data-view=v1]")
    assert pg.locator(".dbv-th[data-key=last]").count() == 0 and pg.locator(".dbv-tab").count() == 6
    assert [v["layout"] for v in views()["apps"]["views"]][3:] == ["calendar", "gantt", "map"]   # kept as they are, not turned into tables

    # the companies table: a field typed in its cell is the user's (Claude's fills keep it)
    pg.click(".hunt-tab[data-tab=companies]")
    pg.click(f".dbv-view tr[data-id='{c1}'] td[data-key=industry]")
    pg.fill(".float .dbv-textbox", "电商")
    pg.press(".float .dbv-textbox", "Enter")
    wait_until(lambda: server.req("GET", f"/api/hunt/companies/{c1}")[1]["company"]["industry"] == "电商", msg="industry saved")
    assert "industry" in server.req("GET", f"/api/hunt/companies/{c1}")[1]["company"]["kept"]
    pg.screenshot(path=str(ART / "hunt-companies-light.png"))


def test_a_long_table_scrolls_with_its_page_under_a_heading_row_that_stays(page_all, server):
    """The heading row stays at the top of the window while the page scrolls; the table is never a slot of its
    own with a second scrollbar. Frozen columns stay at the left while it runs sideways, and its sideways
    scrollbar stays at the foot of the window. Every tab, short windows too."""
    pg = page_all
    cid = server.req("POST", "/api/hunt/companies", {"name": "星河科技"})[1]["company"]["id"]
    for i in range(30):
        server.req("POST", f"/api/hunt/companies/{cid}/roles", {"role": f"岗位 {i:02d} — 一个很长的岗位名字，看它换行"})
    for name in ("腾讯", "Atlassian"):
        server.req("POST", "/api/hunt/companies", {"name": name})
    pg.set_viewport_size({"width": 1000, "height": 650})
    pg.keyboard.press("Control+3")
    pg.wait_for_function("() => document.querySelectorAll('.dbv-view[data-table=apps] tbody tr').length === 30")
    pg.evaluate("() => { DBV.tables.apps.views[0].freeze = 2; paintHunt(); }")
    geom = """() => { const r = (s) => document.querySelector(s)?.getBoundingClientRect();
        const b = document.querySelector('.dbv-body'), sc = document.querySelector('.hunt-scroll');
        return {sc: r('.hunt-scroll'), head: r('.dbv-head'), wrap: r('.dbv-wrap'), hbar: r('.dbv-hbar'), body: r('.dbv-body'),
                frz: r('.dbv-body tbody tr:nth-child(20) td.frz-last'), last: r('.dbv-body tbody tr:last-child'),
                st: sc.scrollTop, sh: sc.scrollHeight, ch: sc.clientHeight, bodyTall: b.scrollHeight - b.clientHeight,
                x: [...document.querySelectorAll('.dbv-head, .dbv-body, .dbv-hbar')].map((e) => e.scrollLeft)}; }"""
    g = pg.evaluate(geom)
    assert g["sh"] > g["ch"] + 600 and g["bodyTall"] == 0          # the page scrolls; the table has no scrollbar of its own
    assert g["sc"]["top"] < g["hbar"]["top"] < g["hbar"]["bottom"] <= g["sc"]["bottom"] + 1   # sideways bar in reach at the foot

    # down the page: the heading row stays at the top of it
    pg.mouse.move(600, 500)
    for _ in range(5):   # a wheel turn can land on a repaint from the roles just added; turn again
        pg.mouse.wheel(0, 900)
        if pg.evaluate("() => new Promise((ok) => setTimeout(() => ok(document.querySelector('.hunt-scroll').scrollTop >= 600), 800))"):
            break
    pg.wait_for_function("() => document.querySelector('.hunt-scroll').scrollTop >= 600")
    g = pg.evaluate(geom)
    assert abs(g["head"]["top"] - g["sc"]["top"]) <= 1
    # sideways over the rows: the heading row and the bar follow, the frozen columns stay
    left0 = g["frz"]["left"]
    pg.mouse.wheel(300, 0)
    pg.wait_for_function("() => { const x = [...document.querySelectorAll('.dbv-head, .dbv-body, .dbv-hbar')].map((e) => e.scrollLeft); return x[1] > 0 && x.every((v) => v === x[1]); }")
    g = pg.evaluate(geom)
    assert abs(g["frz"]["left"] - left0) <= 1 and abs(g["head"]["top"] - g["sc"]["top"]) <= 1
    # a repaint (an edit, a reload of the data) leaves it where it was, both ways
    pg.evaluate("() => paintHunt()")
    g2 = pg.evaluate(geom)
    assert g2["st"] == g["st"] and g2["x"] == g["x"]

    # the companies tab: something stands above the table; the page still scrolls to the last row and the frame's end
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_selector(".dbv-view[data-table=companies] tbody tr")
    pg.mouse.move(600, 200)
    pg.mouse.wheel(0, 5000)
    pg.wait_for_function("() => { const s = document.querySelector('.hunt-scroll'); return s.scrollTop > 0 && s.scrollTop + s.clientHeight >= s.scrollHeight - 1; }")
    g = pg.evaluate(geom)
    assert g["bodyTall"] == 0 and g["wrap"]["bottom"] <= g["sc"]["bottom"] - 10   # the frame's end is on screen, with room below
    pg.set_viewport_size({"width": 1440, "height": 900})


def test_back_from_a_company_page_the_table_is_where_it_was_left(page_all, server):
    """Reading down the companies table, opening one and coming back lands at the same place, not the top
    (user, 2026-11-04). The company's own page opens at its top."""
    pg = page_all
    for i in range(40):
        server.req("POST", "/api/hunt/companies", {"name": f"公司 {i:02d}"})
    pg.set_viewport_size({"width": 1000, "height": 650})
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_function("() => document.querySelectorAll('.dbv-view[data-table=companies] tbody tr').length >= 40")
    pg.evaluate("() => { document.querySelector('.hunt-scroll').scrollTop = 700; }")
    pg.wait_for_function("() => document.querySelector('.hunt-scroll').scrollTop >= 650")
    top = pg.evaluate("() => document.querySelector('.hunt-scroll').scrollTop")
    # a row on screen, opened from its name
    cid = pg.evaluate("""() => { const sc = document.querySelector('.hunt-scroll').getBoundingClientRect();
        const r = [...document.querySelectorAll('.dbv-view[data-table=companies] tbody tr')]
          .find((tr) => tr.getBoundingClientRect().top > sc.top + 60); return r.dataset.id; }""")
    pg.evaluate("(id) => openHunt('company', id)", cid)
    pg.wait_for_selector(".hunt-back")
    assert pg.evaluate("() => document.querySelector('.hunt-scroll').scrollTop") == 0   # the page opens at its top
    pg.click(".hunt-back")
    pg.wait_for_selector(".dbv-view[data-table=companies] tbody tr")
    assert abs(pg.evaluate("() => document.querySelector('.hunt-scroll').scrollTop") - top) <= 1
    # another tab and back: still there
    pg.click(".hunt-tab[data-tab=apps]")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.wait_for_selector(".dbv-view[data-table=companies] tbody tr")
    assert abs(pg.evaluate("() => document.querySelector('.hunt-scroll').scrollTop") - top) <= 1
    pg.set_viewport_size({"width": 1440, "height": 900})


def test_a_cv_edited_in_career_ops_is_never_overwritten(connected, page_all, server):
    pg = page_all
    open_job_search(pg)
    cv = connected / "cv.md"
    wait_until(cv.exists, msg="cv.md written")
    cv.write_text(cv.read_text(encoding="utf-8") + "\n- added in career-ops\n", encoding="utf-8")
    pg.keyboard.press("Control+1")
    pg.keyboard.press("Control+2")                                       # Job search looks at the tool again
    pg.wait_for_selector("#jobspace .js-banners .jobs-banner.warn")
    assert "cv.md" in pg.inner_text("#jobspace .jobs-banner.warn")
    pg.screenshot(path=str(ART / "jobs-cv-conflict-light.png"))
    pg.click("#jobspace .jobs-banner.warn .btn.ghost")                   # use my library
    pg.click(".modal .btn.danger-soft")
    wait_until(lambda: "added in career-ops" not in cv.read_text(encoding="utf-8"), msg="cv replaced")
    pg.wait_for_selector("#jobspace .jobs-banner.warn", state="detached")


def test_settings_show_the_checks_and_apply_on_done(connected, page_all, server, data_file):
    pg = page_all
    open_job_search(pg)
    pg.click("#ws-tools .btn.icon")                                      # ⋯ → career-ops settings
    pg.click(".float .menu-item:has-text('career-ops 设置')")
    pg.wait_for_selector("#jobs-settings .chk")
    layout = pg.locator(".modal.settings").evaluate("""modal => {
      const body = modal.querySelector('.settings-body').getBoundingClientRect();
      const footer = modal.querySelector('.acts').getBoundingClientRect();
      const box = modal.getBoundingClientRect();
      return { bodyRight: body.right, modalRight: box.right, footerBottom: footer.bottom, modalBottom: box.bottom };
    }""")
    assert abs(layout["bodyRight"] - layout["modalRight"]) < 1  # scrollbar belongs at the panel's right edge
    assert layout["footerBottom"] <= layout["modalBottom"]
    assert pg.locator("#jobs-settings .chk.ok").count() >= 6
    pg.click("#jobs-settings .seg button[data-value='zh']")
    pg.click("#jobs-settings .check-row >> nth=1")                       # notes off
    prefs = lambda: Prefs(data_file.parent / "local").load()["career_ops"]   # noqa: E731
    assert "cv_lang" not in prefs()                                       # nothing saved before Done
    pg.screenshot(path=str(ART / "jobs-settings-light.png"))
    pg.click(".modal .acts .btn.primary")
    wait_until(lambda: prefs().get("cv_lang") == "zh" and prefs().get("notes") is False, msg="settings saved")
    wait_until(lambda: "海港城市大学" in (connected / "cv.md").read_text(encoding="utf-8"), msg="cv in Chinese")


def test_career_ops_is_an_optional_plugin(connected, page_all, server, data_file):
    """Its tab is called career-ops (it is career-ops' own interface), and Settings → Plugins turns it off and
    on like any plugin (2026-11-14): off, its tab, Ctrl+2, its settings and the tracker choice go; on, they're back."""
    pg = page_all
    tab = ".ws-btn[data-ws=jobs]"
    on = lambda: Prefs(data_file.parent / "local").load().get("career_ops", {}).get("on", True)   # noqa: E731
    try:
        assert pg.inner_text(f"{tab} .lbl").strip() == "career-ops"
        open_job_search(pg)
        pg.click("#settings-btn")
        row = "#plugins-settings .plugin-row[data-plugin=career-ops] .check-row"
        assert pg.get_attribute(row, "aria-checked") == "true"
        assert pg.locator("#plugins-settings .plugin-row").first.get_attribute("data-plugin") == "career-ops"
        assert not pg.is_visible("#jobs-settings")                              # its details are on its own page
        assert pg.locator("#tracker-settings .seg").count() == 1
        # its gear opens its page in place of the list; Back brings the list back
        pg.click("#plugins-settings .plugin-gear[data-open=career-ops]")
        pg.wait_for_selector(".settings-sub[data-plugin=career-ops] #jobs-settings .chk")
        assert not pg.is_visible(".settings-main")
        sub = ".settings-sub[data-plugin=career-ops]"
        assert pg.inner_text(f"{sub} h3").strip() == "career-ops"
        pg.click(f"{sub} > .field .check-row")                                   # off, on its page
        pg.wait_for_selector(f"{sub} #jobs-off-note")
        assert not pg.is_visible("#jobs-settings")
        pg.click(f"{sub} .settings-back")
        assert pg.is_visible(".settings-main") and pg.locator(".settings-sub").count() == 0
        assert pg.get_attribute(row, "aria-checked") == "false"                  # the list shows what the page did
        assert pg.locator("#tracker-settings .seg").count() == 0
        assert "false" not in pg.inner_text("#tracker-settings") and "null" not in pg.inner_text("#tracker-settings")
        assert on() is True                                                    # nothing saved before Done
        pg.click(".modal .acts .btn.primary")
        wait_until(lambda: on() is False, msg="turned off")
        pg.wait_for_selector(tab, state="hidden")
        pg.wait_for_selector("body[data-ws=resume]")                            # it was on screen: back to Resume
        pg.keyboard.press("Control+2")
        assert pg.get_attribute("body", "data-ws") == "resume"
        pg.reload()
        pg.wait_for_selector("body[data-ws=resume]")
        assert not pg.is_visible(tab)                                           # a new window opens without it
        pg.click("#settings-btn")
        pg.click(row)
        pg.click("#plugins-settings .plugin-gear[data-open=career-ops]")
        pg.wait_for_selector(".settings-sub #jobs-off-note")                     # its details come after Done
        pg.click(".modal .acts .btn.primary")
        wait_until(lambda: on() is True, msg="turned on")
        pg.wait_for_selector(tab, state="visible")
        open_job_search(pg)
    finally:
        server.req("PUT", "/api/jobs/settings", {"on": True})


def test_a_plugins_gear_opens_its_own_settings(page_all, server):
    """Every plugin has a gear in Settings → Plugins that opens its own page (user, 2026-11-14): its switch, its
    tables and their records, where it comes from."""
    pg = page_all
    pg.click("#settings-btn")
    pid = pg.locator("#plugins-settings .plugin-row[data-plugin]:not([data-plugin=career-ops])").first.get_attribute("data-plugin")
    assert pg.locator("#plugins-settings .plugin-gear").count() == pg.locator("#plugins-settings .plugin-row:not(.bad)").count()
    pg.click(f"#plugins-settings .plugin-gear[data-open={pid}]")
    sub = f".settings-sub[data-plugin={pid}]"
    pg.wait_for_selector(f"{sub} .plugin-table")
    on = server.req("GET", "/api/plugins")[1]
    on = next(p for p in on["plugins"] if p["id"] == pid)["on"]
    assert pg.locator(f"{sub} .plugin-table").count() >= 1
    assert pg.locator(f"{sub} .plugin-table-n").count() == (pg.locator(f"{sub} .plugin-table").count() if on else 0)   # off: no false 0
    text = pg.inner_text(sub)
    assert "false" not in text and "null" not in text and "undefined" not in text
    before = pg.get_attribute(f"{sub} > .field .check-row", "aria-checked")
    pg.click(f"{sub} > .field .check-row")
    pg.click(f"{sub} .settings-back")
    assert pg.get_attribute(f"#plugins-settings .plugin-row[data-plugin={pid}] .check-row", "aria-checked") != before
    pg.keyboard.press("Escape")                                                 # nothing saved: Done wasn't pressed
    pg.wait_for_selector(".modal.settings", state="detached")


def test_workspaces_are_dragged_into_any_order(page_all, server):
    """The top bar's workspaces are dragged into another order like a browser's tabs (user, 2026-11-14); the end
    of a drag doesn't switch the workspace, and the order outlives a reload."""
    pg = page_all
    order = lambda: pg.eval_on_selector_all("#ws-switch .ws-btn:not(.ws-more)", "bs => bs.map(b => b.dataset.ws)")   # noqa: E731
    try:
        assert order()[:4] == ["resume", "jobs", "fill", "plan"]
        was = pg.get_attribute("body", "data-ws")
        src, dst = pg.locator(".ws-btn[data-ws=plan]").bounding_box(), pg.locator(".ws-btn[data-ws=resume]").bounding_box()
        pg.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
        pg.mouse.down()
        pg.mouse.move(dst["x"] + 4, dst["y"] + dst["height"] / 2, steps=12)
        pg.mouse.up()
        assert order()[:4] == ["plan", "resume", "jobs", "fill"]
        assert pg.get_attribute("body", "data-ws") == was                         # a drag is not a click
        assert server.req("GET", "/api/plugins")[1]["ws_order"][:4] == ["plan", "resume", "jobs", "fill"]
        pg.reload()
        pg.wait_for_selector("#ws-switch .ws-btn")
        assert order()[:4] == ["plan", "resume", "jobs", "fill"]
        pg.click(".ws-btn[data-ws=plan]")                                        # a plain click still switches
        pg.wait_for_selector("body[data-ws=plan]")
    finally:
        server.req("PUT", "/api/workspace-order", {"order": []})
        pg.evaluate("localStorage.removeItem('rs:ws-order')")
        pg.reload()


# ---------------------------------------------------------------- the Job search workspace
def open_job_search(pg):
    pg.click(".ws-btn[data-ws=jobs]")
    pg.wait_for_selector("body[data-ws=jobs] #jobspace iframe.web-frame:not(.hidden)", timeout=30000)
    frame = pg.frame_locator("#jobspace iframe.web-frame")
    frame.locator("#page").wait_for()
    return frame


def test_job_search_hosts_the_tools_own_interface(connected, page_all, server, fake_claude):
    pg = page_all
    assert pg.is_visible("#editor") and not pg.is_visible("#jobspace")
    frame = open_job_search(pg)
    assert not pg.is_visible("#editor") and not pg.is_visible(".pane.right")        # the whole window is the tool's
    assert not pg.is_visible(".subset-switch") and pg.is_visible(".pane.left")      # the same chat on the left
    assert frame.locator("#page").inner_text() == "/"
    assert "career-ops 全部功能" in pg.inner_text("#ws-tools")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"jobsearch-{scheme}.png"))
    pg.emulate_media(color_scheme="light")

    # the frame tells the window which application is open: a real navigation, then an in-app one
    frame.locator("#to-1").click()
    pg.wait_for_selector("#ws-tools .ws-here")
    assert "Northwind AI — Graduate AI Engineer" in pg.inner_text("#ws-tools .ws-here")
    frame.locator("#push").click()
    wait_until(lambda: "Kestrel Labs" in pg.inner_text("#ws-tools"), msg="pushState reported")
    frame.locator("#home").click()
    pg.wait_for_selector("#ws-tools .ws-here", state="detached")
    frame.locator("#push").click()
    pg.wait_for_selector("#ws-tools .ws-here")

    # the resume to send for it, picked without leaving Job search
    pg.click("#ws-tools .ws-here .btn")
    pg.wait_for_selector(".resume-picker .fit-row")
    pg.click(".resume-picker .fit-row[data-subset='RES-TECH-EN'] .fit-acts .btn:not(.icon)")
    pg.wait_for_selector(".resume-picker .fit-row.on[data-subset='RES-TECH-EN']")
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(ART / "jobsearch-picker-light.png"))
    assert "false" not in pg.inner_text(".resume-picker")
    wait_until(lambda: any(p.get("id") == "2" for v in state(server)["doc"]["subsets"] if v["id"] == "RES-TECH-EN"
                           for p in v.get("postings") or []), msg="linked")
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".resume-picker", state="detached")
    wait_until(lambda: "ML 工程（英文）" in pg.inner_text("#ws-tools .ws-here"), msg="the top bar names it")

    # its preview, in Resume
    pg.click("#ws-tools .ws-here .btn")
    pg.click(".resume-picker .rp-acts .btn:has-text('在简历里预览')")
    pg.wait_for_selector("body[data-ws=resume]")
    wait_until(lambda: pg.get_attribute("#subset-select", "data-value") == "RES-TECH-EN", msg="previewed")

    # back to Job search (Ctrl+2): the same page
    pg.keyboard.press("Control+2")
    pg.wait_for_selector("body[data-ws=jobs]")
    assert frame.locator("#page").inner_text() == "/pipeline/2"                   # not reloaded
    pg.keyboard.press("Control+1")
    pg.wait_for_selector("body[data-ws=resume]")

def test_every_mode_starts_from_the_launcher(connected, page_all, server, fake_claude):
    pg = page_all
    open_job_search(pg)
    pg.click("#ws-tools .btn:has-text('career-ops 全部功能')")
    pg.wait_for_selector(".launcher .lg-item")
    assert pg.locator(".launcher .lg-item[data-mode='update']").count() == 0     # updates live in Settings
    assert pg.locator(".launcher .lg-item[data-mode='triage']").count() == 1
    pg.wait_for_timeout(300)                                                   # fully faded in
    pg.screenshot(path=str(ART / "jobsearch-launcher-light.png"))
    pg.keyboard.type("plan")
    assert pg.locator(".launcher .lg-item:not(.hidden)").count() == 1
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#chat .msg-user")
    wait_until(lambda: any("interview/plan" in c["stdin"] for c in calls(fake_claude)), msg="mode sent")
    c = next(c for c in calls(fake_claude) if "interview/plan" in c["stdin"])
    assert Path(c["cwd"]).resolve() == connected.resolve()


def test_one_chat_across_the_workspaces(connected, page_all, server, fake_claude):
    """docs/LAYOUT.md: the open chat stays open when the workspace changes; a chat in career-ops' folder is listed apart."""
    pg = page_all
    pg.click("#chat .suggest button >> nth=0")                                  # a resume chat
    pg.wait_for_selector("#chat .msg-user")
    resume_chat = pg.get_attribute("#chat-select", "data-value")
    open_job_search(pg)
    assert pg.is_visible("#chat") and pg.get_attribute("#chat-select", "data-value") == resume_chat   # the same chat
    assert "career-ops" in pg.inner_text("#chat-context")                       # it is told what is on screen
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".hunt-tab")
    assert pg.get_attribute("#chat-select", "data-value") == resume_chat
    pg.keyboard.press("Control+2")
    pg.click("#chat-new")                                                       # in Job search: a chat in career-ops' folder
    pg.wait_for_selector("#chat .chat-welcome")
    assert "career-ops" in pg.inner_text("#chat .chat-welcome .t")
    pg.click("#chat .suggest button >> nth=0")
    wait_until(lambda: any(Path(c["cwd"]).resolve() == connected.resolve() for c in calls(fake_claude)), msg="chat in its folder")
    jobs_chat = pg.get_attribute("#chat-select", "data-value")
    assert jobs_chat != resume_chat
    pg.keyboard.press("Control+1")
    pg.wait_for_selector("body[data-ws=resume]")
    assert pg.get_attribute("#chat-select", "data-value") == jobs_chat          # it stays open in Resume too
    pg.click("#chat-select")
    assert pg.locator(".float .lb-group").count() == 2                          # Resume Studio's chats, then career-ops' folder's
    pg.click(f".float [role=option][data-value='{resume_chat}']")
    wait_until(lambda: pg.get_attribute("#chat-select", "data-value") == resume_chat, msg="picked")
    # the chat pane folds away in any workspace, and comes back
    pg.click("#chat-toggle")
    pg.wait_for_selector("#chat", state="hidden")
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".hunt-tab")
    assert not pg.is_visible("#chat")
    pg.click("#chat-toggle")
    pg.wait_for_selector("#chat", state="visible")


# ---------------------------------------------------------------- Autofill (online profiles)
FILL_NOW = {
    "url": "https://www.linkedin.com/in/alex/",
    "basics": {"headline": "Student at HCU", "about": ""},
    "items": [
        {"section": "education", "key": None, "fields": {"org": "The Harbour City University", "degree": "Master of Computer Science",
                                                       "start": "2025-02", "end": "2026-12"}},
        {"section": "projects", "key": None, "fields": {"name": "Old hackathon thing", "start": "2022-03"}},
    ],
    "skills": ["Python"],
}


def fill_view(server):
    return server.req("GET", "/api/autofill/linkedin")[1]


def open_platform(pg, sid="linkedin"):
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=platforms]")
    pg.click(f".dbv-view tr[data-id='{sid}'] .dbv-title")
    pg.wait_for_selector(f".fs-site.on[data-site='{sid}']")


def test_autofill_copy_edits_conflicts_and_push(page_all, server, fake_claude, monkeypatch):
    pg = page_all
    server.inst.browser.headless, server.inst.browser.pace = True, 0
    server.inst.browser.prefer = "msedge"
    open_platform(pg)
    pg.wait_for_selector("#fillspace .fill-start")
    assert not pg.is_visible("#editor") and not pg.is_visible(".pane.right") and pg.is_visible("#chat")
    assert pg.get_attribute(".fs-site.on", "data-site") == "linkedin"
    assert "读取 LinkedIn" in pg.inner_text("#fill-check") and "记录读取路线" in pg.inner_text("#fillspace")
    assert "false" not in pg.inner_text("#ws-tools") and "false" not in pg.inner_text("#fillspace")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"autofill-start-{scheme}.png"))
    pg.emulate_media(color_scheme="light")

    # the first read arrives (Claude's): the copy appears by itself
    assert server.req("POST", "/api/autofill/linkedin/current", FILL_NOW)[0] == 200
    pg.wait_for_selector("#fill-tabs .fv-tab.on")
    assert "你的 LinkedIn 副本" in pg.inner_text(".fill-sync")
    head = pg.locator("[data-edit='basics|headline']")
    assert head.input_value() == "Student at HCU"
    # the resume's wording, one click away
    hint = pg.locator(".ff:has([data-edit='basics|headline']) .ff-ref")
    assert "MSc Computer Science student" in hint.inner_text()
    hint.locator("button").click()
    pg.wait_for_function("() => document.querySelector(\"[data-edit='basics|headline']\").value === 'MSc Computer Science student'")
    assert pg.locator(".ff.changed [data-edit='basics|headline']").count() == 1
    assert "LinkedIn 上现在是：" in pg.inner_text(".ff:has([data-edit='basics|headline'])")
    # typing in the copy: saved, the caret stays
    about = pg.locator("[data-edit='basics|about']")
    about.click()
    pg.keyboard.type("Open to ML roles.")
    wait_until(lambda: fill_view(server)["local"]["basics"].get("about") == "Open to ML roles.", msg="typing saved")
    assert about.evaluate("e => e === document.activeElement")
    # remove one, add one from the resume
    old = pg.locator(".fi.copy", has_text="Old hackathon thing")
    old.locator("button[aria-label='从副本中删除']").click()
    pg.wait_for_selector(".fi.removed >> text=Old hackathon thing")
    pg.click(".fi.suggest:has-text('Plant Disease Classifier') button")
    pg.wait_for_selector(".fi.copy.open:has(.op-add) >> text=Plant Disease Classifier")
    wait_until(lambda: len(fill_view(server)["pending"]) == 4, msg="four changes to push")
    pg.wait_for_selector("#fill-tab-push .fs-n >> text=4")
    pg.locator(".fill-scroll").evaluate("e => { e.scrollTop = 0; }")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"autofill-copy-{scheme}.png"))
    pg.locator(".fill-scroll").evaluate("e => { e.scrollTop = e.scrollHeight; }")
    pg.screenshot(path=str(ART / "autofill-copy-end-light.png"))
    pg.emulate_media(color_scheme="light")
    no_native(pg)

    # meanwhile the site got an About too: a conflict, settled here
    pg.locator(".fill-scroll").evaluate("e => e.focus()")
    now = {**FILL_NOW, "basics": {"headline": "Student at HCU", "about": "Hello from LinkedIn."}}
    assert server.req("POST", "/api/autofill/linkedin/current", now)[0] == 200
    pg.wait_for_selector(".ff.conflict .ff-conflict")
    assert "Hello from LinkedIn." in pg.inner_text(".ff-conflict") and "Open to ML roles." in pg.inner_text(".ff-conflict")
    assert pg.locator("#fill-tabs .fv-tab .fs-n.warn").count() == 1
    pg.screenshot(path=str(ART / "autofill-conflict-light.png"))
    pg.click(".ff-conflict button >> text=保留我的版本")
    pg.wait_for_selector(".ff-conflict", state="detached")

    # the changes to push: git-style, ticked, one unticked
    pg.click("#fill-review")
    pg.wait_for_selector(".fill-plan .fi[data-id='basics.headline'] .diff")
    assert "Student at HCU" in pg.inner_text(".fi[data-id='basics.headline'] .dl-del")
    assert "MSc Computer Science student" in pg.inner_text(".fi[data-id='basics.headline'] .dl-add")
    removal = pg.locator(".fill-plan .fi.op-remove")
    assert removal.count() == 1 and pg.locator(".fill-plan .fi.op-add").count() == 1
    removal.locator(".check").click()
    wait_until(lambda: not next(p for p in fill_view(server)["pending"] if p["op"] == "remove")["checked"], msg="unticked")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"autofill-push-{scheme}.png"), full_page=True)
    pg.emulate_media(color_scheme="light")

    # push: a chat with only the ticked changes; Claude's browser commands show as steps
    data = Path(server.inst.studio.store.path)
    (data.parent / "resume-studio-data" / ".cache" / "server.json").write_text(json.dumps({"url": server.url}), encoding="utf-8")
    b = [sys.executable, str(ROOT / "scripts" / "browser.py"), "--data", str(data)]
    monkeypatch.setenv("FAKE_CLAUDE_RUN", json.dumps({"逐条": [
        {"argv": b + ["open", server.url + "api/instance"]}, {"argv": b + ["screenshot"]},
        {"argv": b + ["report", "linkedin", "basics.headline", "done"]}]}))
    pg.click("#fill-apply")
    wait_until(lambda: any("## The approved changes" in c["stdin"] for c in calls(fake_claude)), timeout=30, msg="apply sent")
    sent = next(c["stdin"] for c in calls(fake_claude) if "## The approved changes" in c["stdin"])
    assert "basics.about" in sent and "Open to ML roles." in sent and '"op": "remove"' not in sent and "Plant Disease Classifier" in sent
    pg.wait_for_selector("#chat-log .msg-user, #chat-log .msg-action")
    assert "LinkedIn" in pg.inner_text("#chat-context")                        # the chat is told which profile is open
    pg.wait_for_selector("#chat-log .msg-ai .md >> text=已经处理", timeout=30000)
    steps = pg.locator("#chat-log .tool-step .tn").all_inner_texts()
    assert steps[:3] == ["打开 127.0.0.1/api/instance", "截图", "已完成：标题"], steps
    pg.wait_for_function("() => { const i = document.querySelector('#chat-log .step-shot img'); return i && i.complete && i.naturalWidth > 0; }")
    assert "browser.py" not in pg.inner_text("#chat-log .msg-ai")
    pg.screenshot(path=str(ART / "autofill-steps-light.png"))
    # done: the headline is on the site now, so it no longer waits
    pg.wait_for_selector(".fill-banner.ok")
    assert pg.locator(".fill-plan .fi[data-id='basics.headline']").count() == 0
    chats = server.req("GET", "/api/chats")[1]["chats"]
    assert [(c.get("workspace"), c.get("site")) for c in chats] == [(None, "linkedin")]   # a data-folder chat, like any other

    # BOSS直聘: a copy of its own, read once first
    pg.click(".fs-site[data-site='boss']")
    pg.wait_for_selector(".fs-site.on[data-site='boss']")
    pg.wait_for_selector("#fillspace .fill-start")
    pg.screenshot(path=str(ART / "autofill-boss-light.png"))
    # Resume shows every chat again; Ctrl+3 comes back to the same site
    pg.keyboard.press("Control+1")
    pg.wait_for_selector("#editor", state="visible")
    pg.keyboard.press("Control+3")
    pg.wait_for_selector(".fs-site.on[data-site='boss']")
    server.inst.browser.stop()


def test_autofill_sites_are_the_users_to_pick(page_all, server):
    pg = page_all
    open_platform(pg)
    pg.wait_for_selector("#fillspace .fill-start")
    # any other site, by its address, with notes for Claude
    pg.click("#fill-add-site")
    pg.wait_for_selector(".modal.site-dialog")
    assert "支持的网站" not in pg.inner_text(".site-dialog")   # LinkedIn and BOSS are on the list already
    pg.click(".site-dialog .btn.primary")                         # no address: says so, stays open
    assert pg.is_visible(".modal.site-dialog")
    pg.fill("#site-name", "SEEK")
    pg.fill("#site-home", "www.seek.com.au/profile/me")
    pg.fill("#site-notes", "Career history is under Profile.")
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.screenshot(path=str(ART / f"autofill-add-site-{scheme}.png"))
    pg.emulate_media(color_scheme="light")
    pg.click(".site-dialog .btn.primary")
    pg.wait_for_selector(".fs-site.on[data-site='seek']")
    assert not pg.is_visible(".modal.site-dialog")
    assert "读取 SEEK" in pg.inner_text("#fill-check")
    sites = server.req("GET", "/api/autofill")[1]["sites"]
    assert [s["id"] for s in sites] == ["linkedin", "boss", "seek"] and sites[2]["home"] == "https://www.seek.com.au/profile/me"
    assert sites[2]["notes"] == "Career history is under Profile."

    # its settings: rename, move it first
    pg.click("#fill-site-settings")
    pg.click(".menu-item >> text=网站设置…")
    pg.fill("#site-name", "SEEK AU")
    pg.click(".site-dialog .btn.primary")
    pg.wait_for_selector(".fs-site[data-site='seek'] >> text=SEEK AU")
    pg.click(".fs-site[data-site='seek']", button="right")
    pg.click(".menu-item >> text=左移")
    pg.click(".fs-site[data-site='seek']", button="right")
    pg.click(".menu-item >> text=左移")
    wait_until(lambda: [s.get_attribute("data-site") for s in pg.locator(".fs-site[data-site]").all()] == ["seek", "linkedin", "boss"], msg="moved")

    # every site can go (asked first); the page then offers what to add
    for sid in ("seek", "linkedin", "boss"):
        pg.click(f".fs-site[data-site='{sid}']", button="right")
        pg.click(".menu-item >> text=从列表中移除")
        pg.click(".modal .btn.danger-soft")
        pg.wait_for_selector(f".fs-site[data-site='{sid}']", state="detached")
    pg.wait_for_selector("#fillspace >> text=你在哪些网站上有个人资料？")
    pg.screenshot(path=str(ART / "autofill-no-sites-light.png"))
    assert "LinkedIn" not in pg.inner_text("#chat .chat-welcome")   # no site to name
    assert pg.locator("#fillspace .btn.primary").count() == 0                 # the known sites lead, "another" doesn't outshine them
    pg.click("#fillspace .btn:has-text('其他网站')")
    pg.wait_for_selector(".site-dialog >> text=Resume Studio 支持的网站")
    pg.wait_for_timeout(400)   # the dialog's fade-in
    pg.screenshot(path=str(ART / "autofill-add-site-presets-light.png"))
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".modal.site-dialog", state="detached")
    pg.click("#fillspace .btn >> text=LinkedIn")
    pg.wait_for_selector(".fs-site.on[data-site='linkedin']")
    pg.wait_for_selector("#fillspace .fill-start")
    no_native(pg)


def test_an_answer_that_comes_late_never_draws_an_older_copy(page_all):
    """2026-11-10: an edit's answer came after the reload a site reading set off, and the tabs were drawn from it:
    a conflict on screen with 0 conflicts on its tab. The newer answer stays; the late one sets off a fresh read."""
    out = page_all.evaluate("""async () => {
      const old = {conflicts: {}, pending: [], local: {}, browser: null};
      const now = {conflicts: {'basics.about': {}}, pending: [], local: {}, browser: null};
      const realApi = api;
      let release, gets = 0;
      api = (method, path) => method === 'PUT' ? new Promise((ok) => { release = () => ok(old); })
        : (gets++, Promise.resolve(now));
      F.site = 'linkedin';
      const edit = editCopy({op: 'set', item: 'basics', field: 'about', value: 'x'}, {repaint: false});
      await new Promise((r) => setTimeout(r, 30));
      await loadFillSite('linkedin', {quiet: true});      // the reading's reload answers first
      release();                                           // then the edit's, from before it
      await edit;
      await new Promise((r) => setTimeout(r, 60));
      api = realApi;
      return {conflicts: Object.keys(F.view.linkedin.conflicts).length, gets};
    }""")
    assert out == {"conflicts": 1, "gets": 2}, out


def test_the_plan_is_one_document_with_versions(page_all, server, data_file):
    """docs/PLAN.md: empty it says one line; Claude writes it from its command and the window follows; the
    user ticks a box, edits it with a reason, sees every version, compares one with now and goes back."""
    import subprocess
    pg = page_all
    plan = lambda: server.req("GET", "/api/plan")[1]
    pg.keyboard.press("Control+4")
    pg.wait_for_selector("#planspace .plan-empty:not(.loading)")   # "Loading…" sits in the same place
    assert pg.inner_text("#planspace").strip() == "有任何顾虑，都可以告诉 AI，它会协助你在此梳理清楚。"   # one line, no template
    assert "向 Claude 说明你的顾虑" in pg.inner_text("#chat .chat-welcome")
    assert pg.locator("#chat .chat-welcome .suggest").count() == 0
    assert "你的计划" in pg.inner_text("#chat-context")
    assert pg.is_disabled("#plan-versions")
    doc = "# 实习\n\n## 这周\n- [ ] 每天 2 道题\n- [ ] 读懂自己的项目\n\n## 进展\n- 2026-10-18 盘点\n"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "plan.py"), "--data", str(data_file), "write", "--stdin", "--why", "第一次盘点"],
                       input=doc.encode("utf-8"), capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr
    pg.wait_for_selector("#planspace .plan-doc h1 >> text=实习")               # Claude's write shows up by itself
    assert pg.locator("#planspace li.task .check").count() == 2
    pg.wait_for_selector("#plan-versions >> text=版本（1）")

    pg.click("#planspace li.task >> nth=0 >> .check")
    wait_until(lambda: "- [x] 每天 2 道题" in plan()["text"], msg="ticked")
    assert len(plan()["versions"]) == 1                                         # a tick goes into the latest version
    pg.wait_for_selector("#planspace li.task >> nth=0 >> .check.on")

    pg.click("#plan-edit")
    pg.wait_for_selector("#planspace .plan-text")
    assert pg.input_value("#planspace .plan-text") == plan()["text"]
    pg.fill("#planspace .plan-text", plan()["text"].replace("每天 2 道题", "每天 1 道题"))
    pg.fill("#planspace .plan-why", "期末周，减量")
    pg.click("#plan-save")
    pg.wait_for_selector("#planspace .plan-doc >> text=每天 1 道题")
    assert [v["why"] for v in plan()["versions"]] == ["第一次盘点", "期末周，减量"]

    pg.click("#plan-versions")
    pg.wait_for_selector(".plan-side .ps-item >> text=期末周，减量")
    pg.click(".plan-side .ps-item >> text=第一次盘点")
    pg.wait_for_selector(".plan-vbar >> text=v1")
    assert "你的计划的 v1" in pg.inner_text("#chat-context")
    assert pg.locator("#planspace .plan-doc li.task .check.ro").count() == 2   # an old version's boxes don't tick
    pg.click(".plan-vbar [role=radio] >> text=之后的改动")
    pg.wait_for_selector(".plan-diff .dl-del >> text=每天 2 道题")
    pg.wait_for_selector(".plan-diff .dl-add >> text=每天 1 道题")
    pg.click(".plan-vbar .btn >> text=恢复到此版本")
    wait_until(lambda: len(plan()["versions"]) == 3, msg="restored as v3")
    assert "每天 2 道题" in plan()["text"] and plan()["versions"][-1]["why"] == "恢复到 v1"
    pg.wait_for_selector(".plan-side .ps-item.on >> text=当前")
    no_native(pg)


def test_scan_learning_button_starts_and_releases(page_all):
    pg = page_all
    pg.evaluate("""() => {
      ui.chat.available = true;
      window.learnMessage = null;
      sendChat = (message) => { window.learnMessage = message; return new Promise((resolve) => { window.finishLearn = resolve; }); };
      const card = postingsCard({company: {id: 'C81', name: 'Test Company'}, scan: {needs: 'claude'}, postings: []});
      card.id = 'learn-test'; document.body.append(card);
    }""")
    button = pg.locator('#learn-test button[data-ai="learn:C81"]')
    button.click()
    pg.wait_for_function("() => window.learnMessage !== null")
    assert "Test Company" in pg.evaluate("window.learnMessage")
    assert button.is_disabled()
    pg.evaluate("window.finishLearn(false)")
    pg.wait_for_function("() => !document.querySelector('#learn-test button[data-ai]').disabled")


AI_CHECK = """async ({run, data, finish = true}) => {
  ui.chat.available = true; ui.chat.id = 'check-chat';
  window.sent = [];
  sendChat = async (m) => { window.sent.push(m); return true; };
  chatState = () => 'running';
  loadHunt = async () => { H.data = window.huntAfter; };
  window.huntAfter = data;
  const before = window.sent.length;
  eval(run);
  for (let i = 0; i < 100 && window.sent.length === before; i++) await new Promise((r) => setTimeout(r, 30));
  await new Promise((r) => setTimeout(r, 30));
  // the chat ends; work not done is sent back by itself (AI_AGAIN times), and each of those ends the same way
  const autos = [];
  for (let n = 0; finish && n < 5; n++) {
    const k = window.sent.length;
    aiRunDone('check-chat', null);
    await new Promise((r) => setTimeout(r, 60));
    if (window.sent.length === k) break;
    autos.push(document.querySelector('#toast span').textContent);
  }
  await new Promise((r) => setTimeout(r, 30));
  const el = document.querySelector('#toast');
  return {say: el.querySelector('span').textContent, warn: el.classList.contains('warn'), again: el.querySelector('button') ? el.querySelector('button').textContent : null,
          sent: window.sent, autos};
}"""


def test_an_ai_button_checks_its_work_when_the_chat_ends(page_all):
    """docs/AI_BUTTON_CHECKS.md: a chat that ended is not work done. Learning a site counts only once the
    company has a scan route; until then the window says so and offers to ask again in the same chat."""
    pg = page_all
    c = {"id": "C91", "name": "测试大学", "postings": {"needs": "claude", "unrouted": ["https://join.test.example/Default.aspx"]}}
    # the server is told the AI tried (C91 exists only in this page, so the request is caught here)
    pg.evaluate("""() => { window.tried = []; const real = api;
      api = async (m, p, b) => p.endsWith('/learn-tried') ? (window.tried.push([p, b]), {}) : real(m, p, b); }""")
    out = pg.evaluate(AI_CHECK, {"run": "askLearn([{id: 'C91', name: '测试大学', unrouted: ['https://join.test.example/Default.aspx']}])",
                                 "data": {"companies": [c], "applications": []}})
    # it says what is so, never whose fault it is (思摩尔国际, 2026-11-04: the AI found the list, the scan didn't know it)
    assert out["warn"] and out["say"] == "AI 已尝试，扫描仍无法读取：join.test.example/Default.aspx。其调查结果见对话。", out
    assert pg.evaluate("window.tried") == [["/api/hunt/companies/C91/learn-tried", {"sites": ["https://join.test.example/Default.aspx"]}]] * 3
    # the AI is handed the sites no scan reads, by name, and told to leave the others alone (星河科技, 2026-11-03)
    assert "仅限这些：测试大学（https://join.test.example/Default.aspx）" in out["sent"][0] and "请勿改动" in out["sent"][0], out["sent"]
    # not done is never handed in ("AI老是不负责任地撂挑子", 2026-11-09): sent back by itself twice, with what is
    # missing and that stopping needs proof, before the window asks the user
    assert len(out["sent"]) == 3 and len(out["autos"]) == 2, out
    assert all(a.startswith("尚未完成，") and "join.test.example" in a for a in out["autos"]), out["autos"]
    assert all("scan-route" in m and "完成之前请勿停止" in m for m in out["sent"][1:]), out["sent"]
    assert out["again"] == "让 AI 重新执行"
    pg.click("#toast button")
    pg.wait_for_function("() => window.sent.length === 4")
    assert "scan-route" in pg.evaluate("window.sent[3]") and pg.evaluate("AI_WORK.has('learn:C91')")
    # recorded: it says what was done
    pg.evaluate("() => { AI_WORK.clear(); }")
    c["postings"] = {"needs": "", "unrouted": []}
    out = pg.evaluate(AI_CHECK, {"run": "askLearn([{id: 'C91', name: '测试大学'}])", "data": {"companies": [c], "applications": []}})
    assert not out["warn"] and out["say"] == "测试大学 的招聘网址现已全部支持自动扫描" and out["again"] is None, out


def test_every_hunt_ai_button_checks_its_own_result(page_all):
    pg = page_all
    url = "https://jobs.example.com/p/7"
    co = {"id": "C92", "name": "星河", "kept": ["size"], "industry": "芯片", "sources": {"industry": "https://a.example"},
          "strength": "估值 10 亿", "hq": None, "places": [], "sites": [{"url": "https://c.example"}], "logo": "", "about": {"summary": "做芯片"}}
    data = {"companies": [co], "applications": []}
    # a pasted link with no role added
    out = pg.evaluate(AI_CHECK, {"run": f"formModal = async () => ({{url: '{url}'}}); addFromLink()", "data": data})
    assert out["warn"] and out["say"] == "未添加该链接对应的岗位" and url in out["sent"][0], out
    data["applications"] = [{"id": "A9", "company": "C92", "role": "芯片验证", "url": url + "/", "jd": ""}]
    pg.evaluate("() => AI_WORK.clear()")
    out = pg.evaluate(AI_CHECK, {"run": f"formModal = async () => ({{url: '{url}'}}); addFromLink()", "data": data})
    assert not out["warn"] and out["say"] == "已添加：星河 的 芯片验证", out
    # fill in: empty fields and a field with no source, never the one the user keeps
    pg.evaluate("() => AI_WORK.clear()")
    out = pg.evaluate(AI_CHECK, {"run": "askFill([{id: 'C92', name: '星河'}])", "data": data})
    assert out["warn"] and "星河：尚未填写：总部, 招聘城市, Logo" in out["say"] and "星河：缺少出处：实力" in out["say"], out
    assert "规模" not in out["say"]
    # its roles are part of filling it in (user, 2026-11-04): asked for in the same message, and a site the scan
    # still can't read is not done
    assert "导入其在招岗位" in out["sent"][0] and "需先学习读取方式再扫描" in out["sent"][0], out["sent"]
    assert out["sent"][0].startswith("填写 星河（C92）"), out["sent"]
    # short (user, 2026-11-10): one line for the fields, one for its roles; the check names any field left empty
    assert len(out["sent"][0]) < 90, out["sent"]
    # added from the window: its row is written before the chat starts, and the AI is told it is new, with its id, or
    # it finds the row and says the company was already on the list (小米集团, 2026-11-09)
    pg.evaluate("() => AI_WORK.clear()")
    out = pg.evaluate(AI_CHECK, {"run": "askFill([{id: 'C92', name: '星河'}], {adding: true})", "data": data})
    assert out["sent"][0].startswith("填写刚添加的公司 星河（C92）"), out["sent"]
    assert "导入其在招岗位" in out["sent"][0], out["sent"]
    full = {**co, "kept": ["size", "hq", "places", "logo"], "sources": {k: "https://a.example" for k in ("industry", "strength", "hq", "size")},
            "postings": {"needs": "claude", "unrouted": ["https://campus.example.com/"]}}
    pg.evaluate("() => AI_WORK.clear()")
    out = pg.evaluate(AI_CHECK, {"run": "askFill([{id: 'C92', name: '星河'}])", "data": {"companies": [full], "applications": []}})
    assert out["warn"] and out["say"] == "星河：岗位尚未导入，扫描仍无法读取 campus.example.com", out
    full["postings"] = {"needs": "", "unrouted": [], "open": 12}
    pg.evaluate("() => AI_WORK.clear()")
    out = pg.evaluate(AI_CHECK, {"run": "askFill([{id: 'C92', name: '星河'}])", "data": {"companies": [full], "applications": []}})
    assert not out["warn"] and out["say"] == "已填写完毕，每项均附出处，岗位已导入：星河", out
    # the chat ended while its scan still ran (货拉拉, 2026-11-10): judged then, it had no "can't read" yet and passed.
    # Now the button keeps spinning, and the work is judged once the scan ends, so the site it can't read goes back
    pg.evaluate("() => AI_WORK.clear()")
    mid = {**full, "postings": {"needs": "", "unrouted": [], "open": 0}}
    out = pg.evaluate("""async ({mid, after}) => {
      ui.chat.available = true; ui.chat.id = 'check-chat';
      window.sent = [];
      sendChat = async (m) => { window.sent.push(m); return true; };
      chatState = () => 'running';
      window.huntAfter = {companies: [mid], applications: [], scan: {running: {C92: 'scan'}}};
      loadHunt = async () => { H.data = window.huntAfter; };
      askFill([{id: 'C92', name: '星河'}]);
      for (let i = 0; i < 100 && !window.sent.length; i++) await new Promise((r) => setTimeout(r, 30));
      await new Promise((r) => setTimeout(r, 30));   // the button learns its chat once the send answers
      aiRunDone('check-chat', null);
      await new Promise((r) => setTimeout(r, 2500));
      const during = {sent: window.sent.length, spinning: AI_WORK.has('fill:C92')};
      window.huntAfter = {companies: [after], applications: [], scan: {running: {}}};
      for (let i = 0; i < 200 && window.sent.length === during.sent; i++) await new Promise((r) => setTimeout(r, 50));
      return {during, sent: window.sent, chat: ui.chat.id, work: [...AI_WORK.keys()], toast: document.querySelector('#toast span').textContent};
    }""", {"mid": mid, "after": {**full, "postings": {"needs": "claude", "unrouted": ["https://join.example.cn/#"], "open": 0}}})
    assert out["during"] == {"sent": 1, "spinning": True}, out
    assert len(out["sent"]) == 2 and "join.example.cn" in out["sent"][1], out
    # reading a posting: done only once the role has a description
    pg.evaluate("() => AI_WORK.clear()")
    run = "askRead({id: 'A9', role: '芯片验证', url: '" + url + "', jd: ''}, {name: '星河'})"
    out = pg.evaluate(AI_CHECK, {"run": run, "data": data})
    assert out["warn"] and out["say"] == "芯片验证 的岗位描述为空" and out["again"] == "让 AI 重新执行", out
    data["applications"][0]["jd"] = "职责：验证"
    pg.evaluate("() => AI_WORK.clear()")
    out = pg.evaluate(AI_CHECK, {"run": run, "data": data})
    assert not out["warn"] and out["say"] == "芯片验证 的岗位描述已填写", out
    # rewriting in a company's language
    pg.evaluate("() => AI_WORK.clear()")
    co["research"] = {"rewrite": "en", "rewrite_name": "英语", "lang_name": "中文"}
    out = pg.evaluate(AI_CHECK, {"run": "askRewrite([{id: 'C92', name: '星河', research: {lang_name: '中文'}}])", "data": data})
    assert out["warn"] and "仍未使用各自的语言：星河" in out["say"], out


def test_postings_read_by_hand_say_the_site_has_no_scan_route(page_all):
    """Postings the AI read by hand, with no route recorded: the card says the next scan can't update them,
    never a plain "scanned · read by AI" that looks set up."""
    pg = page_all
    out = pg.evaluate("""() => {
      const at = new Date(Date.now() - 30 * 60e3).toISOString();
      const say = (scan) => {
        const card = postingsCard({company: {id: 'C83', name: 'Test Company'}, scan, postings: []});
        const s = card.querySelector('#company-no-route');
        return {warn: s ? s.textContent : null, learn: !!card.querySelector('button[data-ai="learn:C83"]')};
      };
      const unrouted = ['https://campus.test.example/', 'https://careers.test.example/'];
      return {hand: say({at, ok: true, how: 'claude', needs: 'claude', found: 11, unrouted}),
              part: say({at, ok: true, how: 'route', needs: 'claude', found: 3, unrouted}),
              route: say({at, ok: true, how: 'route', needs: '', found: 3})};
    }""")
    assert "AI 手动读取" in out["hand"]["warn"] and "campus.test.example, careers.test.example 尚不支持自动扫描" in out["hand"]["warn"] and out["hand"]["learn"]
    assert "campus.test.example, careers.test.example 尚不支持自动扫描" in out["part"]["warn"] and out["part"]["learn"]
    assert out["route"] == {"warn": None, "learn": False}


def test_a_site_the_scan_missed_is_named_on_the_status_and_its_row(page_all):
    """货拉拉, 2026-11-10: its 北森 site read nothing, and the card said "read by mokahr · some pages didn't read"
    while the site's row showed a green "scanned automatically". Now both say which site, and how much was missed."""
    pg = page_all
    out = pg.evaluate("""() => {
      const at = new Date(Date.now() - 60e3).toISOString();
      const sites = ['https://join.huolala.example/#/', 'https://app.mokahr.example/campus-recruitment/huolala/1'];
      const say = (missed) => postingsCard({company: {id: 'C86', name: '货拉拉'}, postings: [],
        scan: {at, ok: true, how: 'mokahr', error: 'https://join.huolala.example/#/: its list gave no answer', missed}})
        .querySelector('#company-read-in-part').textContent;
      const ed = sitesEditor({id: 'C86', sites: sites.map((url) => ({url, kind: 'social'}))},
        {[sites[0]]: 'api', [sites[1]]: 'api'}, {}, {[sites[0]]: 'none'});
      const rows = [...ed.querySelectorAll('.co-site-edit')].map((r) => [...r.querySelectorAll('.site-scan')].map((s) => s.textContent));
      const off = sitesEditor({id: 'C86', sites: sites.map((url) => ({url, kind: 'social'}))},
        {[sites[0]]: '', [sites[1]]: 'api'}, {}, {[sites[0]]: 'unreached'});
      const offRow = [...off.querySelector('.co-site-edit').querySelectorAll('.site-scan')].map((s) => s.textContent);
      return {none: say({[sites[0]]: 'none'}), part: say({[sites[0]]: 'part'}), old: say(null), rows,
              unreached: say({[sites[0]]: 'unreached'}), offRow};
    }""")
    # it didn't answer which system it runs on (join.huolala.cn, 2026-11-11): never "the AI has to learn it"
    assert out["unreached"].endswith("读取方式：mokahr · join.huolala.example 本次连接失败，将在下次扫描时重试"), out
    assert out["offRow"] == ["未检查", "本次连接失败"], out
    assert out["none"].endswith("读取方式：mokahr · join.huolala.example 本次未能读取"), out
    assert out["part"].endswith("读取方式：mokahr · join.huolala.example 部分页面未能读取"), out
    assert out["old"].endswith("读取方式：mokahr · 部分页面未能读取"), out   # a route's page: as before
    assert out["rows"] == [["支持自动扫描（内置读取方式）", "本次未能读取"], ["支持自动扫描（内置读取方式）"]], out


def test_scan_wording_names_sites_never_a_company(page_all):
    """User, 2026-11-03: "1 家公司还没有扫描路线" for a company whose Feishu site scans and whose two own sites
    don't. Every scan message names the sites; every site's row says whether it is scanned automatically."""
    pg = page_all
    out = pg.evaluate("""() => {
      const sites = ['https://campus.xinghe.example/', 'https://xinghe.jobs.feishu.cn/', 'https://careers.xinghe.example/', 'https://new.xinghe.example/'];
      // the toast at the end of a scan
      H.lastRun = 1; H.data = {scan: {last_run: {at: 2, read: 1, new: 0, closed: 0, unrouted: [sites[0], sites[2]]}}};
      scanDone();
      const toastSay = document.querySelector('#toast span').textContent;
      // the table's Roles cell
      const cell = scanTrouble({postings: {needs: 'claude', unrouted: [sites[0], sites[2]]}}).textContent;
      // each site's row on the company page
      const ed = sitesEditor({id: 'C95', sites: sites.map((url, i) => ({url, kind: 'campus', system: i ? '' : 'own site'}))},
        {[sites[0]]: 'no', [sites[1]]: 'api', [sites[2]]: 'route', [sites[3]]: ''});
      const rows = [...ed.querySelectorAll('.co-site-edit')].map((r) => { const s = r.querySelector('.site-scan'); return [s.textContent, s.className]; });
      return {toastSay, cell, rows, system: ed.querySelector('.co-site-edit .muted').textContent};
    }""")
    assert out["toastSay"] == "扫描完成：0 个新增 · 0 个已关闭 · 2 个网址尚不支持自动扫描：campus.xinghe.example, careers.xinghe.example", out
    assert out["cell"] == "campus.xinghe.example, careers.xinghe.example 尚不支持自动扫描：需先由 AI 学习读取方式", out
    # read by code built into the app, or by a route the AI recorded once: told apart (user, 2026-11-12)
    assert out["rows"] == [["尚不支持自动扫描", "site-scan warn"], ["支持自动扫描（内置读取方式）", "site-scan ok"],
                           ["支持自动扫描（AI 记录的读取方式）", "site-scan ok"], ["未检查", "site-scan "]], out
    assert out["system"] == "公司自建"                                       # never the AI's English "own site"


def test_postings_probably_over_are_folded_away_with_their_dates(page_all):
    pg = page_all
    out = pg.evaluate("""() => {
      const card = postingsCard({company: {id: 'C84', name: 'Test University'}, scan: {at: new Date().toISOString(), ok: true, how: 'route', new: ['P1', 'P2']},
        postings: [{id: 'P1', title: '2022年度招聘启事', posted: '2022-05-30', stale: 'old'},
                   {id: 'P2', title: '行政管理岗', posted: '2026-10-28', deadline: '2030-11-15', stale: ''}]});
      const shown = [...card.querySelectorAll('.post-list .post-row')].filter((r) => !r.closest('details')).map((r) => r.querySelector('.pr-title').textContent);
      const stale = card.querySelector('#company-stale');
      return {shown, staleSay: stale && stale.querySelector('summary').textContent,
              staleRows: stale ? [...stale.querySelectorAll('.pr-title')].map((a) => a.textContent) : [],
              newChips: [...card.querySelectorAll('.post-row')].filter((r) => r.querySelector('.new-chip')).map((r) => r.dataset.id),
              meta: card.querySelector('.post-row[data-id="P2"] .pr-meta').textContent};
    }""")
    assert out["shown"] == ["行政管理岗"]
    assert "1 个可能已过期" in out["staleSay"] and out["staleRows"] == ["2022年度招聘启事"]
    assert "2026-10-28 发布" in out["meta"] and "2030-11-15 截止" in out["meta"]
    assert out["newChips"] == ["P2"]                                                # a stale one is never "new"


def test_one_piece_of_work_shows_one_spinner(page_all):
    """A scan or a read going on spins in its button only: no second "Scanning…" or "Reading…" beside it."""
    pg = page_all
    counts = pg.evaluate("""() => {
      const busy = (el) => el.querySelectorAll('.spin, [aria-busy="true"]').length;
      const say = (el, word) => (el.textContent.match(new RegExp(word, 'g')) || []).length;
      const scan = postingsCard({company: {id: 'C82', name: 'Test Company'}, scan: {running: true, at: '2026-10-30T00:00:00Z', ok: true}, postings: []});
      F.site = 'linkedin';
      const route = {recorded: true, pages: 2, broken: 'gone'};
      const checking = syncCard({synced: true, synced_at: '2026-10-30T00:00:00Z', checking: {page: 1, pages: 2}, route, last_check: {}});
      const reading = syncCard({synced: true, synced_at: '2026-10-30T00:00:00Z', reading: true, route, last_check: {}});
      return {scan: [busy(scan), say(scan, '…')], checking: [busy(checking), say(checking, '…')], reading: [busy(reading), say(reading, '…')]};
    }""")
    assert counts == {"scan": [1, 1], "checking": [1, 1], "reading": [1, 1]}, counts
    pg.evaluate("""() => { const card = postingsCard({company: {id: 'C82', name: 'Test Company'}, scan: {running: true}, postings: []});
      card.id = 'one-spinner'; card.style.cssText = 'position: fixed; top: 16px; left: 16px; width: 900px; z-index: 9999'; document.body.append(card); }""")
    pg.locator("#one-spinner").screenshot(path=str(ART / "hunt-scan-one-spinner.png"))


def test_working_timer_restarts_for_each_reply(page_all):
    pg = page_all
    result = pg.evaluate("""async () => {
      ui.chat.available = true;
      ui.chat.id = 'timer-test'; ui.chat.running = false;
      ui.chat.messages = [{role: 'user', text: 'old', at: Date.now()/1000 - 720}];
      ui.chat.liveSince = {chat: ui.chat.id, at: Date.now() - 720000};
      saveDraft = async () => {};
      const originalApi = api;
      api = async (method, path) => {
        if (method === 'POST' && path.endsWith('/send')) return {text: 'new', files: []};
        if (method === 'GET' && path === '/api/chats') return {chats: []};
        return originalApi(method, path);
      };
      await sendChat('new');
      const firstAge = Date.now() - workStart();
      const messageHasTime = !!ui.chat.messages.at(-1).at;
      // Authoritative live timing survives reloading, even if a queued message was sent much earlier.
      ui.chat.live = {blocks: [], started_at: Date.now()/1000 - 5};
      ui.chat.messages.at(-1).at = Date.now()/1000 - 600;
      const serverAge = Date.now() - workStart();
      // Even the fallback for older servers belongs to this reply, not this chat.
      ui.chat.messages = []; ui.chat.live = {blocks: []};
      // the start first: workStart() sets it to now, and a Date.now() taken before it was 1 ms early now and then
      const fallbackStart = workStart();
      const fallbackAge = Date.now() - fallbackStart;
      ui.chat.running = false; ui.chat.live = null;
      await sendChat('another new message');
      const secondAge = Date.now() - workStart();
      document.querySelectorAll('.working').forEach(paintWorking);
      return {firstAge, secondAge, serverAge, fallbackAge, messageHasTime,
        displayed: document.querySelector('.working .wk-t')?.textContent};
    }""")
    assert result["messageHasTime"]
    assert 0 <= result["firstAge"] < 2000
    assert 0 <= result["secondAge"] < 2000
    assert 4900 <= result["serverAge"] < 7000
    assert 0 <= result["fallbackAge"] < 2000
    assert "0s" in result["displayed"]
    pg.screenshot(path=str(ART / "working-timer.png"))


def test_tracks_are_set_in_a_dialog_and_pick_roles_from_every_company(page, server):
    """2026-11-04: scanned roles weren't narrowed to the ones they'd want, for more than one kind of role.
    Tracks are set in a dialog (saved on Done), and "Matching roles" lists every company's roles in a track."""
    pg = page
    hunt = server.inst.hunt
    posts = hunt.scanner.postings
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    posts.merge("C1", [{"title": t, "url": f"https://careers.xinghe.example/job/{i}"} for i, t in
                       enumerate(["AI 应用工程师", "AI 产品经理", "财务专员", "大模型算法工程师"])], how="route")
    posts.set_scan_state("C1", short=[4, 9])
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=picked]")
    pg.wait_for_selector(".trk-empty")
    pg.click(".trk-empty .btn:not(.primary)")                      # set them up myself
    pg.fill(".trk-row .trk-name", "AI 应用")
    pg.fill(".trk-row .trk-kw", "AI, 大模型")
    pg.fill(".trk-row .trk-ex", "产品")
    assert "[object" not in pg.inner_text(".trk-modal")             # the resume picker is drawn, not its object
    pg.click(".trk-more .btn:first-child")                          # a second track
    pg.locator(".trk-row .trk-name").nth(1).fill("AI 产品")
    pg.locator(".trk-row .trk-kw").nth(1).fill("AI+产品")
    pg.click(".trk-modal .btn.primary")
    pg.wait_for_selector(".picked-row")
    assert sorted(pg.locator(".picked-row .pr-title").all_inner_texts()) == ["AI 产品经理", "AI 应用工程师", "大模型算法工程师"]
    assert [t["name"] for t in posts.tracks()] == ["AI 应用", "AI 产品"]
    pg.click(".trk-seg:has-text('AI 产品')")
    assert pg.locator(".picked-row .pr-title").all_inner_texts() == ["AI 产品经理"]
    assert "星河科技" in pg.inner_text(".picked-tools ~ .note-bar.warn")    # read in part: roles may be missing
    posts.settings(tracks=[])


def test_want_to_apply_asks_the_ai_to_assess_the_role(page, server, fake_claude):
    """2026-11-14 (user): "先用不消耗 AI 的方式读 JD 给出初始匹配分，点击想投之后再 AI 读 JD 然后给出进一步的匹配分".
    Want to apply adds the role and, with an agent there, starts a chat of its own that assesses it."""
    pg = page
    hunt = server.inst.hunt
    posts = hunt.scanner.postings
    hunt.add_company({"name": "星河科技", "site": "https://careers.xinghe.example/campus"})
    posts.merge("C1", [{"title": "AI 产品经理", "url": "https://careers.xinghe.example/job/1"}], how="route")
    posts.settings(tracks=[{"name": "AI 产品", "keywords": "AI+产品", "subset": "RES-TECH-EN"}])

    class Pages:
        def read_page(self, url, script, timeout=60.0):
            return {"jd": "岗位职责：负责 AI 产品规划。任职要求：熟悉用户研究、竞品分析、需求分析；熟练使用 Axure、Figma", "text": ""}

        def status(self):
            return {"state": "running"}
    hunt.scanner.browser = Pages()
    chats = lambda: server.req("GET", "/api/chats")[1]["chats"]  # noqa: E731
    before = len(chats())
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=picked]")
    pg.wait_for_selector(".picked-row")
    pg.click(".picked-row > button.btn.sm:not(.pr-added)")                # 待投递 (Want to apply)
    wait_until(lambda: len(chats()) == before + 1, msg="a chat of its own")
    first = lambda: (server.req("GET", f"/api/chats/{chats()[0]['id']}")[1]["messages"] or [{}])[0]  # noqa: E731
    wait_until(lambda: "匹配程度" in first().get("text", "") and "AI 产品经理" in first().get("text", ""), msg="asks for the fit")
    posts.settings(tracks=[])


def test_long_company_details_show_whole_and_push_no_neighbour_down(page, server):
    """2026-11-11: 总部 (a place picker, tall) sat beside 规模, which sank to the bottom of its row; 实力 and 规模, a
    sentence the AI wrote, were cut off in one-line boxes. Fields of varying height get a row each and wrap."""
    pg = page
    hunt = server.inst.hunt
    hunt.add_company({"name": "TP-Link联洲"})
    long_size = "联洲：1000-5000人（高校就业平台）；集团：超1.3万人（官网，2026-11-05 查询），两者口径不同，以签约主体为准"
    hunt.fill("C1", {"industry": "网络通信设备、消费电子、企业网络与安防、软件及云服务",
                     "strength": "TP-Link 集团覆盖 170 多个国家和地区；官网称员工超过 1.3 万人（查询于 2026-11-05，集团口径）",
                     "size": long_size, "location": "深圳"})
    pg.set_viewport_size({"width": 1100, "height": 900})
    pg.keyboard.press("Control+3")
    pg.click(".hunt-tab[data-tab=companies]")
    pg.click(".dbv-view tr[data-id='C1'] .dbv-title")
    pg.wait_for_selector("#company-hq")
    out = pg.evaluate("""() => {
        const card = document.querySelector('#company-hq').closest('.card');
        const fields = [...card.querySelectorAll('.field')].filter((f) => f.querySelector(':scope > label'));
        const box = (f) => f.getBoundingClientRect();
        const cut = [...card.querySelectorAll('input, textarea')].filter((i) => i.scrollWidth > i.clientWidth + 1 || i.scrollHeight > i.clientHeight + 1)
            .map((i) => i.closest('.field').querySelector('label').firstChild.textContent);
        // in a row of fields, each one's label starts at the row's top
        const sunk = fields.filter((f) => fields.some((g) => g !== f && Math.abs(box(g).top - box(f).top) < box(f).height
            && box(g).left !== box(f).left && Math.abs(box(f).top - box(g).top) > 2)).map((f) => f.querySelector('label').firstChild.textContent);
        const size = fields.find((f) => f.querySelector('label').firstChild.textContent === '规模');
        return {cut, sunk, size: size.querySelector('textarea').value};
    }""")
    assert out["cut"] == [] and out["sunk"] == [], out
    assert out["size"] == hunt.company("C1")["size"]                  # as kept, all of it
    # Enter keeps the one value, as in an input: no second line
    pg.click("#company-hq >> xpath=preceding::textarea[1]")
    pg.keyboard.press("End")
    pg.keyboard.press("Enter")
    assert "\n" not in pg.evaluate("() => document.querySelector('#company-hq').previousElementSibling.querySelector('textarea').value")


# ---------------------------------------------------------------- skills as chips (web/skills.js)
def test_skills_are_chips_with_how_well_you_have_them(page, server, data_file):
    """Tech / keywords are chips: a comma inside brackets stays in one, a click sets how well the user has the skill
    (kept in the file's private `skills:` list), and a skills line in a subset dims a skill no entry it picks uses."""
    pg = page
    v = subset(server)
    v["entries"] = [e for e in v["entries"] if e != "EXP-002"]       # QuizLoom out: nothing else in it uses Next.js
    server.req("PUT", "/api/item", {"kind": "subset", "id": "RES-TECH-EN", "data": v, "base": None})
    open_entry(pg, "EXP-005")
    chip = "#active .skx-chip:has(.skx-name:text-is('Next.js'))"
    pg.wait_for_selector(chip + ".off")                              # left out of this resume, still there to show
    assert pg.locator(chip + " .skx-name").evaluate("n => getComputedStyle(n).textDecorationLine") == "line-through"
    assert pg.locator(".skx-strip[data-sec=skills]").count() == 1      # the levels and the AI's readings, above the section

    pg.click("#active .skx-input")
    pg.keyboard.type("Python (NumPy, SciPy), Docker")
    pg.keyboard.press("Enter")
    wait_until(lambda: "Python (NumPy, SciPy)" in json.dumps(next(e for e in state(server)["doc"]["entries"] if e["id"] == "EXP-005")["tech"]),
               msg="the bracketed item saved as one")
    tech = next(e for e in state(server)["doc"]["entries"] if e["id"] == "EXP-005")["tech"]
    assert tech[-2:] == ["Python (NumPy, SciPy)", "Docker"]

    pg.click("#active .skx-chip:has(.skx-name:text-is('Docker'))")
    pg.click(".skx-pop .skx-levels button:text-is('能用')")
    pg.wait_for_selector("#active .skx-chip:has(.skx-name:text-is('Docker')) .skx-lv:text-is('能用')")
    assert next(r for r in state(server)["skills"] if r["key"] == "docker")["level"] == "working"
    assert "level: working" in data_file.read_text(encoding="utf-8")

    pg.click(chip)                                                   # show it on this resume anyway
    pg.click(".skx-pop button:text-is('仍然显示')")
    wait_until(lambda: subset(server).get("show_skills") == ["Next.js"], msg="pinned on the subset")
    pg.wait_for_selector(chip + ":not(.off)")


def test_typing_a_skill_searches_the_ones_you_have_and_enter_makes_a_new_one(page, server):
    """2026-11-15: the tech field is a combobox. Typing searches the skills already in the catalog (any entry's);
    Enter takes the highlighted one under its own name, or, when nothing matches, makes what was typed."""
    pg = page
    open_entry(pg, "EXP-005")
    have = pg.evaluate("() => entryById('EXP-005').tech.map((x) => skillKey(display(x)))")
    other = next(r for r in state(server)["skills"] if r["key"] not in have and len(r["name"]) > 3)
    pg.click("#active .skx-input")
    pg.keyboard.type(other["name"][:3].lower())
    opt = pg.wait_for_selector(".skx-suggest:not([hidden]) .lb-opt.sel-hl")
    assert opt.inner_text().startswith(other["name"]) or pg.locator(".skx-suggest .lb-opt", has_text=other["name"]).count() == 1
    while pg.locator(".skx-suggest .lb-opt.sel-hl .lb-label").inner_text() != other["name"]:
        pg.keyboard.press("ArrowDown")
    pg.keyboard.press("Enter")
    pg.wait_for_selector(f"#active .skx-chip:has(.skx-name:text-is('{other['name']}'))")   # the catalog's spelling
    assert pg.locator("#active .skx-input").input_value() == ""

    pg.keyboard.type("Zzqx Lang")                      # in no entry: the one choice is to make it
    opts = pg.locator(".skx-suggest:not([hidden]) .lb-opt")
    assert opts.count() == 1 and "Zzqx Lang" in opts.first.inner_text()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#active .skx-chip:has(.skx-name:text-is('Zzqx Lang'))")
    wait_until(lambda: "Zzqx Lang" in json.dumps(next(e for e in state(server)["doc"]["entries"] if e["id"] == "EXP-005")["tech"]),
               msg="the new skill saved")

    pg.keyboard.type("zzqx lang")                      # already on this line: not offered again, nor made twice
    assert pg.locator(".skx-suggest:not([hidden])").count() == 0
    pg.keyboard.press("Control+a")
    pg.keyboard.press("Backspace")
    pg.keyboard.press("ArrowDown")                     # ↓ on an empty field lists what there is; Esc closes it
    pg.wait_for_selector(".skx-suggest:not([hidden])")
    pg.keyboard.press("Escape")
    assert pg.locator(".skx-suggest:not([hidden])").count() == 0
    assert pg.locator("#active").count() == 1          # Esc closed the list, not the card

    pg.keyboard.type("Kub")                            # half a search, then a click elsewhere: kept in the box, nothing made
    pg.click("#active .field > label >> nth=0")
    assert pg.locator("#active .skx-input").input_value() == "Kub"
    assert pg.locator(".skx-suggest:not([hidden])").count() == 0
    pg.wait_for_timeout(600)
    assert "Kub" not in json.dumps(next(e for e in state(server)["doc"]["entries"] if e["id"] == "EXP-005")["tech"])
