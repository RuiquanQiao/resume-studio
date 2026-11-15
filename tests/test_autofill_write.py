"""Autofill write recipes (autofill/recipe.py): the AI records one per kind of change the first time it pushes it,
then "Make these changes" replays it with no AI, reads the section back with the read route, and stops safely on
anything else: a redesign, a value the site has no option for, a sign-in, a save the site doesn't show.

Runs against tests/fake_sites/editor/profile.html (an online resume editor with React-like inputs, a native
select, an AntD-like dropdown, searchable pickers and date pickers) in the headless browser."""
from __future__ import annotations

import dataclasses
import json
import sys
import time

import pytest

from conftest import calls
from test_autofill import BROWSER_PY, fake_site, headless, live, run_steps  # noqa: F401 (fixtures)

from autofill import recipe
from autofill.sites import PRESETS

ROUTE_SCRIPT = r"""async (h) => {
  const d = (s) => { s = (s || "").trim(); return s === "至今" ? "present" : s; };
  const dates = (li) => h.text(h.one("[data-k=dt]", li)).split("–").map(d);
  const exp = h.all("li", h.section("工作经历")).map((li) => {
    const [start, end] = dates(li);
    return {section: "experience", fields: {title: h.text(h.one("b", li)), org: h.text(h.one("[data-k=org]", li)),
      location: h.text(h.one("[data-k=city]", li)), start, end, description: h.text(h.one("[data-k=desc]", li))}};
  });
  const edu = h.all("li", h.section("教育经历")).map((li) => {
    const [start, end] = dates(li);
    return {section: "education", fields: {org: h.text(h.one("b", li)), degree: h.text(h.one("[data-k=degree]", li)), start, end}};
  });
  return {basics: {about: h.text(h.one("#about"))}, items: [...exp, ...edu]};
}"""

DATES = [{"do": "date", "field": "开始时间", "value": "{start}", "parts": ["{start.year}", "{start.month}月"]},
         {"do": "date", "field": "结束时间", "value": "{end}", "parts": ["{end.year}", "{end.month}月"], "present": "至今"}]
CITIES = ["北京", "上海", "深圳", "杭州", "悉尼", "堪培拉"]
EXP_FIELDS = [{"do": "type", "field": "职位名称", "value": "{title}"},
              {"do": "search", "field": "公司名称", "value": "{org}"},
              {"do": "select", "field": "城市", "value": "{location}", "options": CITIES},
              *DATES,
              {"do": "type", "field": "工作内容", "value": "{description}"},
              {"do": "save", "name": "保存"}]


def recipes(url: str) -> dict:
    """What the AI records on the fake editor, per kind of change."""
    op = {"do": "open", "url": url}
    return {
        "experience.update": {"cancel": "取消", "steps": [op, {"do": "click", "kinds": ["button"], "name": "编辑",
                                                               "near": ["{site.title}", "{site.org}"]}, *EXP_FIELDS]},
        "experience.add": {"cancel": "取消", "steps": [op, {"do": "click", "name": "添加工作经历"}, *EXP_FIELDS]},
        "experience.remove": {"steps": [op, {"do": "click", "kinds": ["button"], "name": "删除", "near": ["{site.title}", "{site.org}"]},
                                        {"do": "confirm", "name": "确定"}]},
        "education.update": {"cancel": "取消", "steps": [
            op, {"do": "click", "kinds": ["button"], "name": "编辑", "near": ["{site.org}", "{site.degree}"]},
            {"do": "search", "field": "学校", "value": "{org}"},
            {"do": "pick", "field": "学历", "value": "{degree}", "options": ["大专", "本科", "硕士", "博士"]},
            {"do": "date", "field": "入学时间", "value": "{start}", "parts": ["{start.year}", "{start.month}月"]},
            {"do": "date", "field": "毕业时间", "value": "{end}", "parts": ["{end.year}", "{end.month}月"], "present": "至今"},
            {"do": "save", "name": "保存"}]},
        "basics.about": {"cancel": "取消", "steps": [op, {"do": "click", "name": "编辑个人优势"},
                                                    {"do": "type", "field": "个人优势", "value": "{about}"}, {"do": "save", "name": "保存"}]},
    }


@pytest.fixture
def editor(live, fake_site, monkeypatch):
    """BOSS直聘 pointed at the fake editor, its read route recorded and the first copy read."""
    url = fake_site + "/editor/profile.html"
    monkeypatch.setitem(PRESETS, "boss", dataclasses.replace(PRESETS["boss"], home=url, domains=["127.0.0.1"], login_paths=["/login"]))
    b = live.inst.browser
    b.open(url)
    b._eval_page("localStorage.clear(); 1")
    b.open(url)
    st, r = live.req("POST", "/api/autofill/boss/route", {"pages": [{"url": url, "reads": ["basics", "experience", "education"],
                                                                     "script": ROUTE_SCRIPT}]})
    assert st == 200 and "Running it read this" in r["text"], r
    st, r = live.req("POST", "/api/autofill/boss/sync", {})
    assert st == 200 and "synced." in r["text"], r
    live.editor_url = url
    return live


def db(server) -> dict:
    b = server.inst.browser
    b.open(server.editor_url)
    return json.loads(b._eval_page("localStorage.getItem('editor-db')"))


def flag(server, name: str, on: bool = True) -> None:
    b = server.inst.browser
    b.open(server.editor_url)
    b._eval_page(f"localStorage.{'setItem' if on else 'removeItem'}({name!r}, '1'); 1")


def view(server) -> dict:
    return server.req("GET", "/api/autofill/boss")[1]


def item(v: dict, section: str, **match) -> dict:
    return next(i for i in v["local"]["items"] if i["section"] == section and all(i["fields"].get(k) == x for k, x in match.items()))


def edit(server, **op) -> dict:
    st, v = server.req("PUT", "/api/autofill/boss/local", op)
    assert st == 200, v
    return v


def settle(server, timeout: float = 90) -> dict:
    """Wait until the push and the check after it are over."""
    end = time.time() + timeout
    while time.time() < end:
        v = view(server)
        if not v["pushing"] and not v["applying"] and not v["checking"]:
            return v
        time.sleep(0.3)
    raise TimeoutError("the push didn't finish")


def put_recipes(server, keys) -> None:
    for k in keys:
        st, r = server.req("POST", "/api/autofill/boss/recipe", {"key": k, "recipe": recipes(server.editor_url)[k]})
        assert st == 200, r


# ---- recording is checked like a read route --------------------------------------------------------
def test_a_recipe_is_checked_before_it_is_kept():
    site = dataclasses.replace(PRESETS["boss"], home="https://www.zhipin.com/web/geek/resume")
    url = "https://www.zhipin.com/web/geek/resume"
    good = recipes(url)
    for k, r in good.items():
        out, errs = recipe.validate(r, site, k)
        assert not errs and out["steps"], (k, errs)

    def errs_of(key: str, steps: list, **extra) -> str:
        return "\n".join(recipe.validate({"steps": steps, **extra}, site, key)[1])
    op = {"do": "open", "url": url}
    save = {"do": "save", "name": "保存"}
    assert "never pressed" in errs_of("basics.about", [op, {"do": "click", "name": "立即沟通"}, save])
    assert "never pressed" in errs_of("basics.about", [op, {"do": "click", "name": "求职状态"}, save])
    assert "only a experience.remove" in errs_of("experience.update", [op, {"do": "click", "name": "删除", "near": ["{site.org}"]}, save])
    assert "confirms" in errs_of("basics.about", [op, {"do": "confirm", "name": "确定"}])
    assert "page of BOSS直聘" in errs_of("basics.about", [{"do": "open", "url": "https://evil.example.com/"}, save])
    assert "steps[0] must open" in errs_of("basics.about", [save])
    assert "passwords or codes" in errs_of("basics.about", [op, {"do": "type", "field": "验证码", "value": "{about}"}, save])
    assert "isn't one of" in errs_of("basics.about", [op, {"do": "type", "field": "个人优势", "value": "{salary}"}, save])
    assert "find the item first" in errs_of("experience.update", [op, {"do": "type", "field": "职位名称", "value": "{title}"}, save])
    assert "ends by saving" in errs_of("basics.about", [op, {"do": "type", "field": "个人优势", "value": "{about}"}])
    assert "isn't a recipe" in "\n".join(recipe.validate(good["basics.about"], site, "awards.add")[1])   # BOSS直聘 has no awards


def test_a_static_dropdown_value_is_checked_before_anything_is_touched():
    site = PRESETS["boss"]
    r, _ = recipe.validate(recipes("https://www.zhipin.com/x")["experience.add"], site, "experience.add")
    with pytest.raises(recipe.RecipeFailed) as e:
        recipe.plan(site, r, "add", {"title": "x", "org": "腾讯", "location": "火星", "start": "2023-07"}, {}, None)
    assert e.value.kind == "value" and "火星" in str(e.value) and "堪培拉" in str(e.value) and not e.value.saved
    # an update runs only the steps for the fields it changes
    steps = recipe.plan(site, r, "update", {"title": "y", "location": "火星"}, {}, ["title"])
    assert [s["do"] for _, s, _ in steps] == ["open", "click", "type", "save"]
    assert recipe.render("{start.year}年{start.month}月 {end.mon}", {"start": "2024-02", "end": "2025-11"}, {}) == "2024年2月 Nov"


# ---- the first push records, every push after it needs no AI ---------------------------------------
def test_the_ai_records_each_kind_once_then_pushes_need_no_ai(editor, data_file, fake_claude):
    live = editor
    v = view(live)
    assert v["route"]["covers"] == ["basics", "experience", "education"] and v["recipes"] == []
    csiro, bd = item(v, "experience", org="CSIRO"), item(v, "experience", org="字节跳动")
    edu = item(v, "education", org="Harbour City University")
    edit(live, op="set", item=csiro["id"], field="title", value="Research Engineer")
    edit(live, op="remove", item=bd["id"])
    added = edit(live, op="add", section="experience", fields={"title": "算法实习生", "org": "腾讯", "location": "深圳",
                                                               "start": "2023-07", "end": "2023-09", "description": "推荐模型。"})["item"]
    edit(live, op="set", item=edu["id"], field="degree", value="博士")
    edit(live, op="set", item="basics", field="about", value="熟悉 Python、PyTorch 与地理信息系统。")
    v = view(live)
    assert {p["id"]: p["via"] for p in v["pending"]} == {"basics.about": "ai", f"item.{csiro['id']}": "ai",
                                                        f"item.{bd['id']}": "ai", f"item.{added}": "ai", f"item.{edu['id']}": "ai"}

    # the first push: the AI records a recipe for each kind, and makes each change through it
    b = [sys.executable, str(BROWSER_PY), "--data", str(data_file)]
    rs = recipes(live.editor_url)
    steps = []
    for key, cid in (("experience.update", f"item.{csiro['id']}"), ("experience.remove", f"item.{bd['id']}"),
                     ("experience.add", f"item.{added}"), ("education.update", f"item.{edu['id']}"), ("basics.about", "basics.about")):
        steps += [{"argv": b + ["recipe", "boss", key, "--stdin"], "stdin": json.dumps(rs[key], ensure_ascii=False)},
                  {"argv": b + ["push", "boss", cid]}]
    run_steps(live, data_file, {"Record a recipe as you go": steps})
    st, r = live.req("POST", "/api/autofill/boss/apply", {})
    assert st == 200 and r["mode"] == "claude", r
    live.wait_idle(r["chat"]["id"], timeout=180)
    ctx = calls(fake_claude)[-1]
    runs = calls(type(fake_claude)(str(fake_claude) + ".run"))
    assert all(x["code"] == 0 for x in runs), runs
    assert [x["out"].split(".")[0].split(" ")[0] for x in runs] == ["saved", "done:"] * 5, [x["out"] for x in runs]
    v = settle(live)
    assert v["pending"] == [] and sorted(v["recipes"]) == sorted(rs), v["pending"]
    d = db(live)
    assert [(p["title"], p["org"], p["city"], p["start"], p["end"]) for p in d["exp"]] == [
        ("Research Engineer", "CSIRO", "堪培拉", "2024-02", "2024-11"), ("算法实习生", "腾讯", "深圳", "2023-07", "2023-09")]
    assert d["edu"][0]["degree"] == "博士" and d["about"] == "熟悉 Python、PyTorch 与地理信息系统。"
    assert not d.get("greeted") and not d.get("status_changed")

    # the second push: every kind has a recipe, so it goes up with no AI at all, and each is read back
    n_claude = len(calls(fake_claude))
    v = view(live)
    csiro, tx = item(v, "experience", org="CSIRO"), item(v, "experience", org="腾讯")
    edit(live, op="set", item=csiro["id"], field="description", value="Mapped flood risk for 3 councils.")
    edit(live, op="set", item=csiro["id"], field="end", value="present")
    edit(live, op="remove", item=tx["id"])
    added = edit(live, op="add", section="experience", fields={"title": "数据工程实习生", "org": "阿里巴巴", "location": "杭州",
                                                               "start": "2026-01", "description": "ETL。"})["item"]
    edit(live, op="set", item=item(v, "education", org="Harbour City University")["id"], field="degree", value="硕士")
    edit(live, op="set", item="basics", field="about", value="熟悉 Python 与 GIS。")
    v = view(live)
    assert {p["via"] for p in v["pending"]} == {"recipe"} and len(v["pending"]) == 5
    t0 = time.time()
    st, r = live.req("POST", "/api/autofill/boss/apply", {})
    assert st == 200 and r["mode"] == "recipe" and r["chat"] is None and len(r["recipe"]) == 5, r
    v = settle(live)
    assert len(calls(fake_claude)) == n_claude   # no AI
    assert v["pending"] == [], v["pending"]
    assert all(a["state"] == "done" and a["by"] == "recipe" for a in v["applied"].values()), v["applied"]
    assert time.time() - t0 < 90
    d = db(live)
    assert [(p["org"], p["end"], p["desc"]) for p in d["exp"]] == [("CSIRO", "present", "Mapped flood risk for 3 councils."),
                                                                  ("阿里巴巴", "", "ETL。")]
    assert d["edu"][0]["degree"] == "硕士" and d["about"] == "熟悉 Python 与 GIS。"


# ---- every failure stops that change, safely, and says where -----------------------------------------
def test_failures_stop_safely_and_say_where(editor, data_file, fake_claude):
    live = editor
    put_recipes(live, ["experience.update", "education.update", "basics.about"])
    n_claude = len(calls(fake_claude))

    def push_one(**op) -> dict:
        edit(live, **op)
        st, r = live.req("POST", "/api/autofill/boss/apply", {})
        assert st == 200 and r["mode"] == "recipe", r
        return settle(live)

    v = view(live)
    csiro = item(v, "experience", org="CSIRO")
    edu = item(v, "education", org="Harbour City University")
    saves = db(live)["saves"]

    # a value a static dropdown doesn't have: fails before anything is touched
    v = push_one(op="set", item=csiro["id"], field="location", value="火星")
    a = v["applied"][f"item.{csiro['id']}"]
    assert a["state"] == "failed" and a["kind"] == "value" and "火星" in a["note"] and not a["saved"], a
    assert "experience.update" not in v["recipe_broken"]   # the recipe is fine: the value isn't one the site has
    edit(live, op="revert", change=f"item.{csiro['id']}")
    # the custom dropdown, the same
    v = push_one(op="set", item=edu["id"], field="degree", value="院士")
    assert v["applied"][f"item.{edu['id']}"]["kind"] == "value"
    edit(live, op="revert", change=f"item.{edu['id']}")
    # a searchable picker with no suggestion that reads the value: nothing is saved, the dialog is closed
    v = push_one(op="set", item=csiro["id"], field="org", value="Nowhere Corp")
    a = v["applied"][f"item.{csiro['id']}"]
    assert a["state"] == "failed" and a["kind"] == "value" and "Nowhere Corp" in a["note"] and a["step"] == 4, a
    assert db(live)["saves"] == saves and not live.inst.browser.dialogs()
    edit(live, op="revert", change=f"item.{csiro['id']}")

    # the site drops what was typed: saved, but the read-back doesn't show it, so it isn't done
    flag(live, "editor-drop-desc")
    v = push_one(op="set", item=csiro["id"], field="description", value="Flood maps for 3 councils.")
    a = v["applied"][f"item.{csiro['id']}"]
    assert a["state"] == "failed" and a["kind"] == "verify" and a["saved"] and "description" in a["note"], a
    assert "experience.update" in v["recipe_broken"]
    assert next(p for p in v["pending"] if p["id"] == f"item.{csiro['id']}")["via"] == "ai"   # never replayed blindly again
    flag(live, "editor-drop-desc", False)
    # the check after the push read the emptied description: a change on both sides, for the user to settle
    v = settle(live)
    assert list(v["conflicts"]) == [f"{csiro['id']}.description"]
    edit(live, op="settle", key=f"{csiro['id']}.description", take="site")

    # a redesign: the edit button is renamed, the recipe stops at its step, nothing is saved
    flag(live, "editor-redesign")
    saves = db(live)["saves"]
    v = push_one(op="set", item=edu["id"], field="degree", value="博士")
    a = v["applied"][f"item.{edu['id']}"]
    assert a["state"] == "failed" and a["kind"] == "broken" and a["step"] == 2 and "编辑" in a["note"] and not a["saved"], a
    assert db(live)["saves"] == saves and v["recipe_broken"]["education.update"]["step"] == 1
    assert len(calls(fake_claude)) == n_claude   # still no AI
    # the way on: the AI makes it and records the recipe anew, told what broke and shown the old one
    st, r = live.req("POST", "/api/autofill/boss/apply", {"only": [f"item.{edu['id']}"], "ai": True})
    assert st == 200 and r["mode"] == "claude", r
    live.wait_idle(r["chat"]["id"], timeout=60)
    sent = calls(fake_claude)[-1]["stdin"]
    assert "Record a recipe as you go" in sent and "`education.update`" in sent and "stopped at step 2" in sent
    assert '"name": "编辑"' in sent   # the old recipe, to fix
    settle(live)
    flag(live, "editor-redesign", False)

    # a sign-in asked for mid-replay: that change fails, the rest wait, the window asks to sign in
    flag(live, "editor-signout")
    edit(live, op="set", item="basics", field="about", value="新的个人优势。")
    st, r = live.req("POST", "/api/autofill/boss/apply", {"only": ["basics.about", f"item.{edu['id']}"]})
    assert st == 200 and r["mode"] == "mixed", r   # the education recipe is broken: that one would go to the AI
    v = settle(live)
    assert v["applied"]["basics.about"]["kind"] == "signed_out" and v["signed_out"], v["applied"]
    assert f"item.{edu['id']}" not in v["applied"] or v["applied"][f"item.{edu['id']}"]["state"] != "done"
    flag(live, "editor-signout", False)
    assert db(live)["about"] == "熟悉 Python 与地理信息系统。"
