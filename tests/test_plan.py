"""The plan (docs/PLAN.md): one Markdown document with no fixed shape, its versions, and Claude's command."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import ROOT
from core.i18n import t
from studyplan.store import ConflictError, Plan, add_line, tasks

PLAN_PY = ROOT / "scripts" / "plan.py"

DOC = """# 实习

## 处境
时间不多。

## 这周
- [ ] 每天 2 道题
- [x] 定方向，每天复盘

```
- [ ] 代码块里的不算
```

## 进展
- 2026-10-18 盘点了一次
"""


def cli(data_file, *args, stdin: str = "") -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(PLAN_PY), "--data", str(data_file), *args], input=stdin.encode("utf-8"),
                       capture_output=True, timeout=60)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


def test_a_rewrite_keeps_a_version_and_a_small_change_goes_into_the_latest(tmp_path):
    p = Plan(tmp_path / "resume-studio-data")
    assert p.state()["text"] == "" and p.state()["versions"] == []           # nothing until the user and Claude write it
    r = p.write(DOC, why="第一次盘点", by="claude")
    assert r["made"]["n"] == 1 and r["made"]["by"] == "claude" and r["kept"]
    assert (tmp_path / "resume-studio-data" / "plan" / "plan.md").read_text(encoding="utf-8") == DOC   # plain Markdown in the data folder
    assert p.write(DOC, why="again")["made"] is None                          # nothing changed: no version
    assert len(tasks(DOC)) == 2                                               # the box in the code block isn't one
    p.tick(0, True)
    p.note("做了 2 道双指针", on="2026-10-19")
    vs = p.versions()
    assert len(vs) == 1 and vs[0]["edited"] and vs[0]["text"] == p.text()      # both went into v1, which says what the plan says
    assert "- [x] 每天 2 道题" in p.text()
    assert p.text().rstrip().endswith("- 2026-10-18 盘点了一次\n- 2026-10-19 做了 2 道双指针")   # at the end of its section
    p.tick_item("定方向", done=False)
    assert "- [ ] 定方向，每天复盘" in p.text()
    with pytest.raises(KeyError):
        p.tick_item("没有这一项")
    with pytest.raises(ValueError):
        p.tick_item("每天")                                                    # two boxes say it: be more specific
    r = p.write(p.text().replace("时间不多。", "时间不多，经历也少。"), why="补上经历")
    assert r["made"]["n"] == 2 and [v["why"] for v in p.versions()] == ["第一次盘点", "补上经历"]


def test_going_back_keeps_every_version_and_an_outside_edit_is_never_lost(tmp_path):
    p = Plan(tmp_path / "resume-studio-data")
    p.write("# v1\n", why="one")
    p.write("# v2\n", why="two")
    r = p.restore(1)
    assert r["made"]["n"] == 3 and p.text() == "# v1\n" and [v["n"] for v in p.versions()] == [1, 2, 3]
    assert p.restore(1)["made"] is None                                       # it says that already
    p.md.write_text("# 在别的编辑器里改的\n", encoding="utf-8")                 # another editor
    st = p.state()
    assert not st["kept"] and st["text"] == "# 在别的编辑器里改的\n"
    p.write("# v4\n", why="four")
    vs = p.versions()
    assert [(v["by"], v["text"]) for v in vs[-2:]] == [("outside", "# 在别的编辑器里改的\n"), ("you", "# v4\n")]
    with pytest.raises(ConflictError):                                        # the window edited an older copy
        p.write("# mine\n", base="0" * 16)
    with pytest.raises(KeyError):
        p.get(99)


def test_a_line_of_progress_finds_its_section_or_starts_one():
    assert add_line("# 计划\n\n## 下一步\n- [ ] x\n", "做完了", on="2026-10-19").endswith(f"## 下一步\n- [ ] x\n\n## {t('Progress log')}\n\n- 2026-10-19 做完了\n")   # in the interface language
    two = "# 计划\n\n## 学习日志\n- a\n\n## 以后\n- b\n"
    assert add_line(two, "c", on="2026-10-19") == "# 计划\n\n## 学习日志\n- a\n- 2026-10-19 c\n\n## 以后\n- b\n"
    assert "## 以后\n- b\n- 2026-10-19 c" in add_line(two, "c", under="以后", on="2026-10-19")
    with pytest.raises(ValueError):
        add_line(two, "c", on="10/19")


def test_claude_writes_the_plan_from_its_command_without_the_window(data_file):
    code, out = cli(data_file, "show")
    assert code == 0 and "the plan is empty" in out
    code, out = cli(data_file, "write", "--stdin", "--why", "第一次盘点", stdin=DOC)
    assert code == 0 and "kept as v1: 第一次盘点" in out
    code, out = cli(data_file, "note", "读完了 Prisma schema", "--on", "2026-10-19")
    assert code == 0 and "saved (in v1)" in out
    code, out = cli(data_file, "done", "每天 2 道题")
    assert code == 0
    code, out = cli(data_file, "show")
    assert "- [x] 每天 2 道题" in out and "- 2026-10-19 读完了 Prisma schema" in out and "v1" in out and "by Claude: 第一次盘点" in out
    code, out = cli(data_file, "done", "不存在的事")
    assert code != 0 and "error:" in out
    code, out = cli(data_file, "versions")
    assert out.strip().startswith("v1")


def test_the_window_edits_ticks_and_goes_back_through_the_api(server, data_file):
    code, st = server.req("GET", "/api/plan")
    assert code == 200 and st["text"] == "" and st["versions"] == []
    code, st = server.req("PUT", "/api/plan", {"text": DOC, "why": "", "base": st["digest"]})
    assert code == 200 and st["versions"][-1]["n"] == 1 and st["versions"][-1]["by"] == "you"
    code, st2 = server.req("POST", "/api/plan/tick", {"index": 0, "done": True, "base": st["digest"]})
    assert code == 200 and "- [x] 每天 2 道题" in st2["text"] and len(st2["versions"]) == 1
    code, err = server.req("PUT", "/api/plan", {"text": "# stale\n", "base": st["digest"]})
    assert code == 409                                                        # changed since the window read it
    code, st3 = server.req("PUT", "/api/plan", {"text": "# 新计划\n", "why": "换方向", "base": None})
    assert code == 200 and st3["versions"][-1]["why"] == "换方向"
    code, v1 = server.req("GET", "/api/plan/versions/1")
    assert code == 200 and "每天 2 道题" in v1["text"]
    code, st4 = server.req("POST", "/api/plan/versions/1/restore")
    assert code == 200 and st4["text"] == v1["text"] and [v["n"] for v in st4["versions"]] == [1, 2, 3]
    assert server.req("GET", "/api/plan/versions/9")[0] == 404
    # every chat in the data folder is told where the plan stands and how to write it
    note = "\n".join(n() for n in server.inst.chats.notes)
    assert "scripts" in note and "plan.py" in note and "no fixed shape" in note and "v3" in note
    assert str(ROOT / "skills" / "planning" / "SKILL.md") in note and (ROOT / "skills" / "planning" / "SKILL.md").is_file()   # the method and the calendars
    saved = json.loads((data_file.parent / "resume-studio-data" / "plan" / "versions.json").read_text(encoding="utf-8"))
    assert [v["n"] for v in saved] == [1, 2, 3]
