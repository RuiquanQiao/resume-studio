"""A role's skills and the track its description reads like, worked out by rules once it is picked (jobhunt/skills.py).

User, 2026-11-14: screening sorts roles into tracks by title only, with no AI; at Want to apply the description is
read, without AI, to check the track and to keep the skills it asks for on the role, for every field, not only IT.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jobhunt.postings import Postings
from jobhunt.skills import Lexicon, skills_of, track_hint
from jobhunt.store import Hunt

AGENT_JD = """岗位职责
1. 负责 AI Agent 的设计与开发，搭建多智能体协作框架；
2. 负责 Agent 的工具调用、记忆与规划模块；
3. 优化 Agent 的评测体系，提升智能体任务成功率；
4. 与前端同学协作完成产品交付。
任职要求
1. 计算机、软件工程等相关专业本科及以上学历；
2. 熟练掌握 Python、Go 中的一种，熟悉 LangChain、LangGraph 等 Agent 框架；
3. 有 RAG 或向量数据库项目经验者优先；
4. 良好的沟通能力和团队合作精神。
"""

FINANCE_JD = """工作内容
负责公司月度结账、财务报表编制与分析。
任职要求
1. 财务、会计相关专业本科以上学历，持有CPA证书者优先；
2. 熟悉企业会计准则、税务筹划及合并报表；
3. 熟练使用Excel、SAP等财务软件；
4. 有审计或成本核算工作经验；
5. 责任心强，具备良好的沟通能力。
"""

EN_JD = """Responsibilities
- Plan and deliver marketing campaigns across channels.
Requirements
- Bachelor's degree in Marketing or a related field.
- 3+ years of experience with Google Analytics, HubSpot and SEO.
- Proficient in Excel and Tableau; familiarity with A/B testing.
- Excellent communication skills.
"""


def test_skills_are_what_the_requirements_name_in_any_field(tmp_path):
    lex = Lexicon(tmp_path)
    agent = skills_of(AGENT_JD, lex)
    assert {"Python", "Go", "LangChain", "LangGraph", "RAG", "向量数据库"} <= set(agent)
    finance = skills_of(FINANCE_JD, lex)
    assert {"CPA", "企业会计准则", "税务筹划", "合并报表", "Excel", "SAP", "审计", "成本核算"} <= set(finance)
    english = skills_of(EN_JD, lex)
    assert {"Google Analytics", "HubSpot", "SEO", "Excel", "Tableau", "A/B testing"} & set(english) >= {"HubSpot", "SEO", "Excel", "Tableau"}
    everything = agent + finance + english
    # a degree's subjects, qualities and filler are never skills
    for never in ("计算机", "软件工程", "会计", "沟通能力", "团队合作精神", "责任心强", "Marketing", "communication"):
        assert never not in everything, never
    assert len(agent) == len(set(agent))                                       # each once


def test_a_skill_one_description_names_is_found_in_the_next(tmp_path):
    """The list grows from the descriptions read: no word list made for one field."""
    lex = Lexicon(tmp_path)
    for _ in range(2):   # named after 熟悉 in two descriptions
        skills_of("任职要求\n熟悉合并报表编制。", lex)
    assert "合并报表编制" in skills_of("任职要求\n负责集团合并报表编制工作。", lex)
    assert Lexicon(tmp_path).terms()                                           # kept in hunt/skills.json


def test_the_description_says_when_it_reads_like_another_track():
    tracks = [{"id": "T1", "name": "AI 全栈", "keywords": ["全栈", "AI+应用开发"], "exclude": [], "natures": []},
              {"id": "T2", "name": "AI Agent", "keywords": ["Agent", "智能体"], "exclude": [], "natures": []},
              {"id": "T3", "name": "国央企 Agent", "keywords": ["Agent"], "exclude": [], "natures": ["cn.central_soe"]}]
    h = track_hint(AGENT_JD, ["T1"], tracks, "")
    assert h["track"] == "T2" and h["from"] == "T1"
    assert dict(map(tuple, h["words"]))["Agent"] == 3                          # lines, not hits
    assert track_hint(AGENT_JD, ["T2"], tracks, "") is None                    # in the right one already
    assert track_hint(FINANCE_JD, ["T1"], tracks, "") is None                  # nothing like another track


def test_a_picked_role_keeps_its_skills_and_can_be_moved_to_another_track(tmp_path):
    hunt = Hunt(tmp_path / "data")
    posts = Postings(hunt)
    posts.settings(tracks=[{"id": "T1", "name": "AI 全栈", "keywords": ["AI+应用开发"]},
                           {"id": "T2", "name": "AI Agent", "keywords": ["Agent"]}])
    hunt.add_company({"name": "星河科技"})
    posts.merge("C1", [{"title": "AI应用开发工程师", "url": "https://jobs.example.com/1"}], how="route", complete=True)
    a = posts.apply(posts.all()[0]["id"])
    assert "skills" not in a or a["skills"] == []
    a = hunt.update_application(a["id"], {"jd": AGENT_JD})                     # read from its page
    assert "LangGraph" in a["skills"]
    assert posts.matched()["postings"][0]["tracks"] == ["T1"]                  # by its title
    hunt.update_application(a["id"], {"track": "T2"})                           # the user moves it
    m = posts.matched()
    assert m["postings"][0]["tracks"] == ["T2"] and m["counts"] == {"T1": 0, "T2": 1}
    a = hunt.update_application(a["id"], {"skills": ["LangGraph", "RAG"]})     # and corrects its skills
    assert a["skills"] == ["LangGraph", "RAG"]
    a = hunt.update_application(a["id"], {"note": "x"})
    assert a["skills"] == ["LangGraph", "RAG"]                                 # kept until a new description


def test_roles_read_before_skills_existed_get_them_once(tmp_path):
    hunt = Hunt(tmp_path / "data")
    hunt.add_company({"name": "星河科技"})
    a = hunt.add_application("星河科技", {"role": "会计"})
    data = hunt._apps()
    next(x for x in data["applications"] if x["id"] == a["id"])["jd"] = FINANCE_JD   # an old record: no skills
    hunt._write("applications.json", data)
    assert hunt.fill_skills() == 1
    assert "税务筹划" in hunt.application(a["id"])["skills"]
    assert hunt.fill_skills() == 0


def test_a_skill_taken_off_one_role_is_off_every_role_and_one_typed_in_is_found_everywhere(tmp_path):
    hunt = Hunt(tmp_path / "data")
    hunt.add_company({"name": "星河科技"})
    a = hunt.add_application("星河科技", {"role": "会计", "jd": FINANCE_JD})
    b = hunt.add_application("星河科技", {"role": "审计", "jd": FINANCE_JD.replace("会计", "审计")})
    assert "SAP" in a["skills"] and "SAP" in b["skills"]
    hunt.update_application(a["id"], {"skills": [x for x in a["skills"] if x != "SAP"] + ["月度结账"]})
    b = hunt.application(b["id"])
    assert "SAP" not in b["skills"] and "月度结账" in b["skills"]              # its duties say 月度结账
    c = hunt.add_application("星河科技", {"role": "出纳", "jd": FINANCE_JD})
    assert "SAP" not in c["skills"] and "月度结账" in c["skills"]              # and every role read after


@pytest.mark.parametrize("jd", ["", "   ", "岗位职责\n负责日常工作。"])
def test_a_description_with_no_skills_names_none(tmp_path, jd):
    assert skills_of(jd, Lexicon(Path(tmp_path))) == []
