"""The skill catalog (resume/skills.py): skills named in entries' `tech` as instances of one record each, how well the
user really has each (a fact layer, never rendered), a list section that shows in a subset only the skills its
chosen entries back, and the job-match scores that read the levels (jobhunt/rank.py)."""
from __future__ import annotations

import copy
import json

from jobhunt.rank import _has, fit
from resume import model, skills, versions
from resume.service import Studio

SECTIONS = [{"id": "projects", "kind": "timeline"}, {"id": "skills", "kind": "list"}]


def doc(**over):
    d = {"schema_version": 3, "sections": copy.deepcopy(SECTIONS),
         "entries": [
             {"id": "P1", "section": "projects", "title": "Shop", "tech": ["Next.js 16", "Prisma"]},
             {"id": "P2", "section": "projects", "title": "Alarm", "tech": ["Kotlin"]},
             {"id": "S1", "section": "skills", "title": "Programming", "tech": ["Java", "Kotlin", "Next.js", "Prisma"]},
         ],
         "subsets": [{"id": "ALL", "lang": "en"}, {"id": "WEB", "lang": "en", "entries": ["P1", "S1"]}]}
    d.update(over)
    return d


def line(d, subset_id="WEB", lang="en"):
    view = model.build_view(d, {**model.get_subset(d, subset_id), "lang": lang})
    return next((e["tech"] for s in view["sections"] for e in s["entries"] if e["id"] == "S1"), None)


# ---- names and keys --------------------------------------------------------------
def test_a_trailing_version_case_and_spacing_name_the_same_skill():
    assert skills.key("Next.js 16") == skills.key("next.js") == skills.key(" Next.js ") == "next.js"
    assert skills.key("Tauri 2") == "tauri" and skills.key("Python 3.12") == "python"
    assert skills.key("Qwen3.5") == "qwen3.5"            # a version glued to the name is part of it
    assert skills.base_name("React 19") == "React"


def test_the_catalog_has_one_record_per_skill_with_where_it_is_used():
    cat = {r["key"]: r for r in skills.catalog(doc())}
    assert set(cat) == {"next.js", "prisma", "kotlin", "java"}
    assert cat["next.js"]["names"] == ["Next.js 16", "Next.js"] and cat["next.js"]["name"] == "Next.js"
    assert cat["kotlin"]["used_in"] == ["P2"] and cat["kotlin"]["lines"] == ["S1"]
    assert cat["java"]["used_in"] == [] and cat["java"]["level"] == ""


def test_an_alias_joins_two_names():
    d = doc(skills=[{"name": "Next.js", "aliases": ["NextJS"]}])
    d["entries"][1]["tech"].append("NextJS")
    cat = {r["key"]: r for r in skills.catalog(d)}
    assert "nextjs" not in cat and cat["next.js"]["used_in"] == ["P1", "P2"]


# ---- a list section in a subset ----------------------------------------------------
def test_a_skills_line_shows_what_the_subsets_entries_back():
    d = doc()
    # Kotlin is used by Alarm only, which WEB leaves out; Java no entry uses at all (coursework): it stays
    assert line(d) == ["Java", "Next.js", "Prisma"]
    assert line(d, "ALL") == ["Java", "Kotlin", "Next.js", "Prisma"]     # the full set shows everything


def test_the_subset_can_show_or_hide_any_skill_anyway():
    d = doc()
    d["subsets"][1]["show_skills"] = ["Kotlin"]
    d["subsets"][1]["hide_skills"] = ["Next.js 16"]                     # by the skill, whatever the name
    assert line(d) == ["Java", "Kotlin", "Prisma"]


def test_a_line_whose_every_skill_is_left_out_goes():
    d = doc()
    d["entries"][2]["tech"] = ["Kotlin"]
    assert line(d) is None


def test_why_a_skill_is_hidden():
    d = doc()
    chosen = [d["entries"][0], d["entries"][2]]
    v = d["subsets"][1]
    assert skills.why_hidden(d, v, "Kotlin", chosen) == "unbacked"
    assert skills.why_hidden(d, v, "Java", chosen) == ""
    v["hide_skills"] = ["Java"]
    assert skills.why_hidden(d, v, "Java", chosen) == "hidden"


# ---- the older files ------------------------------------------------------------------
def test_an_older_file_keeps_showing_what_it_showed():
    d = doc(schema_version=2)
    before = [list(e["tech"]) for e in d["entries"]]
    assert model.migrate(d) is True
    assert d["schema_version"] == model.SCHEMA_VERSION
    assert d["subsets"][1]["show_skills"] == ["Kotlin"]                 # pinned: WEB showed it before
    assert line(d) == ["Java", "Kotlin", "Next.js", "Prisma"]
    assert [e["tech"] for e in d["entries"]] == before
    # once up to date, the pins are the user's: taking one off stays off
    d["subsets"][1].pop("show_skills")
    assert model.migrate(d) is False and line(d) == ["Java", "Next.js", "Prisma"]


def test_an_item_split_at_a_comma_inside_brackets_is_joined_again():
    d = doc(schema_version=2)
    d["entries"][1]["tech"] = ["Kotlin", "Python (NumPy", "SciPy)", "Room"]
    model.migrate(d)
    assert d["entries"][1]["tech"] == ["Kotlin", "Python (NumPy, SciPy)", "Room"]


def test_every_subset_of_the_sample_shows_every_item_of_its_lines_as_before():
    from ruamel.yaml import YAML

    from conftest import SAMPLE
    d = YAML().load(SAMPLE.read_text(encoding="utf-8"))
    model.migrate(d)
    for v in d["subsets"]:
        view = model.build_view(d, v)
        for sec in view["sections"]:
            for e in sec["entries"]:
                assert e["tech"] == [str(x) for x in model.get_item(d, "entry", e["id"]).get("tech") or []]


# ---- the levels -------------------------------------------------------------------------
def test_a_level_is_kept_on_the_skill_and_cleared_without_a_trace():
    d = doc()
    skills.set_level(d, "Next.js 16", "none", why="the AI wrote the front end")
    assert d["skills"] == [{"name": "Next.js", "level": "none", "by": "user", "why": "the AI wrote the front end"}]
    assert skills.levels(d)["next.js"]["level"] == "none"
    skills.set_level(d, "next.js", "")
    assert "skills" not in d


def test_an_ai_reading_waits_for_the_user():
    d = doc(skills=[{"name": "Prisma", "level": "working", "by": "user", "suggest": {"level": "basic", "why": "notes: copied the schema"}},
                    {"name": "Kotlin", "suggest": {"level": "strong", "why": "notes: wrote it all"}}])
    cat = {r["key"]: r for r in skills.catalog(d)}
    assert cat["prisma"]["level"] == "working" and cat["prisma"]["suggest"]["level"] == "basic"
    skills.settle(d, "Prisma", accept=True)
    skills.settle(d, "Kotlin", accept=False)
    assert d["skills"] == [{"name": "Prisma", "level": "basic", "by": "ai", "why": "notes: copied the schema"}]


# ---- versions ---------------------------------------------------------------------------
def test_a_version_keeps_what_its_lines_showed():
    d = doc()
    c = versions.content(d, d["subsets"][1])
    assert c["skills_shown"] == {"S1": ["Java", "Next.js", "Prisma"]}
    assert "show_skills" not in c["subset"]
    vd = versions.as_doc(c)
    view = model.build_view(vd, vd["subsets"][0])
    assert next(e["tech"] for s in view["sections"] for e in s["entries"] if e["id"] == "S1") == ["Java", "Next.js", "Prisma"]
    # nothing left out: nothing extra kept, so the versions kept before this change still count as the same
    d["subsets"][1]["entries"].append("P2")
    assert "skills_shown" not in versions.content(d, d["subsets"][1])


def test_going_back_to_a_version_shows_its_lines_again():
    d = doc()
    c = versions.content(d, d["subsets"][1])
    d["subsets"][1]["show_skills"] = ["Kotlin"]
    d["entries"][0]["tech"] = ["Next.js 16"]           # Prisma is no longer backed by the subset's entries
    versions.restore(d, c)
    assert line(d) == ["Java", "Next.js", "Prisma"]


# ---- the job match ----------------------------------------------------------------------
JD = """岗位要求：
1. 熟悉 Java、Spring Boot、MySQL，了解 Redis；
2. 熟练掌握 Python 编程开发；
3. 有 Docker 使用经验。"""


def test_java_is_not_found_in_javascript():
    assert not _has("javascript react", "java") and _has("java, react", "java")
    assert not _has("c++ and go", "c") and _has("用户研究报告", "用户研究")
    f = fit(JD, "JavaScript React Python Docker")
    assert "Java" in f["miss"] and f["hit"] == ["Python", "Docker"]


def test_mastery_counts_the_levels_whether_or_not_the_resume_names_the_skill():
    lv = {"python": {"name": "Python", "level": "strong"}, "docker": {"name": "Docker", "level": "none"},
          "java": {"name": "Java", "level": "basic"}}
    f = fit(JD, "JavaScript React Python Docker", levels=lv)
    assert f["score"] == 33                                  # coverage: Python and Docker of six
    assert f["real"] == 25                                   # (1 + 0.5 + 0) / 6
    assert (f["have"], f["partial"], f["cant"]) == (["Python"], ["Java"], ["Docker"])
    assert fit(JD, "Python Docker")["real"] is None          # nothing rated yet: no second number
    # a skill the resume names without a level isn't known either way: left out until rated, and listed to rate
    f = fit(JD, "JavaScript Python Docker", levels={"python": {"name": "Python", "level": "strong"}, "docker": {"name": "Docker", "level": ""}})
    assert f["real"] == 20 and f["unrated"] == ["Docker"]    # Python of Java, Spring Boot, MySQL, Redis, Python
    # a word only the bullets have (no chip to rate it by) is left out of the list to rate
    f = fit(JD, "Python Docker", levels={"python": {"name": "Python", "level": "strong"}})
    assert f["unrated"] == [] and f["real"] == 20
    # skills rated, none of them this description's: no 0%, which would read as "can't do any of it"
    f = fit(JD, "Python Docker", levels={"kotlin": {"name": "Kotlin", "level": "strong"}, "python": {"name": "Python", "level": ""}})
    assert f["real"] is None and f["unrated"] == ["Python"]


def test_skill_levels_feed_the_match_through_the_catalog():
    d = doc()
    skills.set_level(d, "Next.js 16", "none")
    lv = skills.levels(d)
    assert lv["next.js"] == {"name": "Next.js", "level": "none"} and lv["kotlin"] == {"name": "Kotlin", "level": ""}


# ---- the window ------------------------------------------------------------------------
def test_the_window_sets_levels_and_knows_which_notes_changed(data_file):
    s = Studio(data_file)
    st = s.state()
    assert {r["key"] for r in st["skills"]} >= {"next.js", "pytorch"}
    assert set(st["skills_review"]) == {"profile", "EXP-001", "EXP-002", "EXP-003"}   # every entry with notes
    st = s.set_skill_level("Next.js", "working")
    assert next(r for r in st["skills"] if r["key"] == "next.js")["level"] == "working"
    assert "skills:" in data_file.read_text(encoding="utf-8")
    doc_, _ = s.store.load()
    s.mark_skills_reviewed(["profile", "EXP-002"], s._notes(doc_))
    assert set(s.skills_review()) == {"EXP-001", "EXP-003"}
    e = model.get_item(doc_, "entry", "EXP-001")
    e["notes"] = "changed"
    s.store.save(doc_)
    assert "EXP-001" in s.skills_review()
    assert json.loads((s.store.state_dir / "skill-review.json").read_text(encoding="utf-8"))


def test_the_api_level_and_settle_routes(server, data_file):
    code, st = server.req("PUT", "/api/skills/level", {"name": "PyTorch", "level": "basic"})
    assert code == 200 and next(r for r in st["skills"] if r["key"] == "pytorch")["level"] == "basic"
    code, _ = server.req("PUT", "/api/skills/level", {"name": "PyTorch", "level": "expert"})
    assert code == 400
    code, _ = server.req("PUT", "/api/skills/level", {"name": "", "level": "basic"})
    assert code == 400
    code, st = server.req("POST", "/api/skills/settle", {"names": [], "accept": True})
    assert code == 200


def test_the_ai_reading_button_sends_the_changed_notes(server, data_file, fake_claude):
    code, chat = server.req("POST", "/api/chats")
    code, r = server.req("POST", f"/api/chats/{chat['id']}/send", {"text": "", "action": "skills"})
    assert code == 200 and "suggest" in r["text"] and "QuizLoom" in r["text"]
    server.wait_idle(chat["id"])
    code, st = server.req("GET", "/api/state")
    assert st["skills_review"] == []                          # read: nothing waits any more
    code, chat2 = server.req("POST", "/api/chats")
    code, r = server.req("POST", f"/api/chats/{chat2['id']}/send", {"text": "", "action": "skills"})
    assert code == 409
