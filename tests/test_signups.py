"""Sign-ups (one careers-site account, several roles in order) and which subset version went out."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.i18n import Prefs
from jobs.base import ToolError
from jobs.career_ops import CareerOps

FIXTURE = Path(__file__).parent / "fixtures" / "career-ops"


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    target = tmp_path / "career-ops"
    shutil.copytree(FIXTURE, target)
    (target / "node_modules" / "js-yaml").mkdir(parents=True)
    return target


@pytest.fixture
def tool(tmp_path: Path, checkout: Path) -> CareerOps:
    t = CareerOps(Prefs(tmp_path / "local"), home=tmp_path / "local" / "providers" / "career-ops", cache=tmp_path / "cache",
                  skill_md=Path("SKILL.md"))
    t.use_folder(checkout)
    return t


@pytest.fixture
def jobs(server, data_file, checkout):
    Prefs(data_file.parent / "local").save(career_ops={"path": str(checkout)})
    return server


def tracker(checkout: Path) -> str:
    return (checkout / "data" / "applications.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- rows through career-ops' own writer
def test_rows_go_in_through_career_ops_reserve_and_merge(tool, checkout):
    ids = tool.add_applications([
        {"company": "星河科技", "role": "服务端研发工程师", "status": "applied", "notes": "网申 · 志愿 1"},
        {"company": "星河科技", "role": "算法工程师", "status": "applied", "notes": "tab\there"},
    ])
    assert ids == ["4", "5"]
    apps = {a["id"]: a for a in tool.applications()}
    assert apps["4"]["role"] == "服务端研发工程师" and apps["5"]["status"] == "applied"
    assert "tab here" in tracker(checkout)                                   # one TSV cell, never two
    assert not list((checkout / "batch" / "tracker-additions").glob("*.tsv"))  # all merged
    assert sorted(p.name for p in (checkout / "batch" / "tracker-additions" / "merged").iterdir()) == ["004-resume-studio.tsv", "005-resume-studio.tsv"]
    assert not list((checkout / "reports").glob("*-RESERVED.md"))            # the numbers are released once used


def test_a_row_career_ops_folds_into_another_is_reported(tool):
    tool.add_applications([{"company": "Acme", "role": "Backend", "url": "https://acme.example/jobs/1"}])
    with pytest.raises(ToolError, match="Frontend"):   # names the role it lost
        tool.add_applications([{"company": "Acme", "role": "Frontend", "url": "https://acme.example/jobs/1"}])


# ---------------------------------------------------------------- sign-ups over HTTP
def test_a_sign_up_goes_out_with_a_pinned_version(jobs, checkout, data_file):
    code, s = jobs.req("POST", "/api/jobs/signups", {"company": "星河科技", "site": "https://careers.xinghe.example/campus",
                                                     "subset": "RES-TECH-ZH", "roles": ["服务端研发工程师", "算法工程师", "算法工程师", " "]})
    assert code == 200 and [r["role"] for r in s["roles"]] == ["服务端研发工程师", "算法工程师"]   # in order, no repeats or blanks
    assert s["roles"][0]["app"] is None and s["last"] is None and s["subset_label"] == "ML 工程（中文）"
    sid = s["id"]
    before = tracker(checkout)
    # reorder the choices; nothing reaches the tracker before it is sent
    code, s = jobs.req("PUT", f"/api/jobs/signups/{sid}", {"roles": [s["roles"][1], s["roles"][0]]})
    assert [r["role"] for r in s["roles"]] == ["算法工程师", "服务端研发工程师"] and tracker(checkout) == before

    code, r = jobs.req("POST", f"/api/jobs/signups/{sid}/sent")
    assert code == 200 and r["n"] == 1 and r["added"] == 2
    s = r["signup"]
    assert [x["app"] for x in s["roles"]] == ["4", "5"] and all(x["status"] == "applied" for x in s["roles"])
    assert s["last"]["subset"] == "RES-TECH-ZH" and s["last"]["n"] == 1
    assert "第 1 志愿" in tracker(checkout)
    assert r["subset_versions"]["RES-TECH-ZH"]["latest"] == 1                 # the version that went out was kept
    vs = json.loads((data_file.parent / ".studio" / "subsets" / "RES-TECH-ZH.json").read_text(encoding="utf-8"))
    assert vs["versions"][0]["note"].endswith("星河科技")
    # the overview: every role knows which resume reached it
    ov = jobs.req("GET", "/api/jobs")[1]
    a4 = next(a for a in ov["applications"] if a["id"] == "4")
    assert a4["sent"]["n"] == 1 and a4["signup"] == sid and a4["subsets"] == [{"id": "RES-TECH-ZH", "label": "ML 工程（中文）"}]
    assert ov["signups"][0]["id"] == sid

    # a new choice added later, and sent again: only the new role is added; the same version is not kept twice
    roles = [*s["roles"], {"role": "数据分析师"}]
    jobs.req("PUT", f"/api/jobs/signups/{sid}", {"roles": roles})
    code, r = jobs.req("POST", f"/api/jobs/signups/{sid}/sent")
    assert r["added"] == 1 and r["n"] == 1 and [x["app"] for x in r["signup"]["roles"]] == ["4", "5", "6"]
    assert len(r["signup"]["sent"]) == 2
    # deleting the sign-up leaves career-ops' rows alone
    assert jobs.req("DELETE", f"/api/jobs/signups/{sid}")[0] == 200
    assert "数据分析师" in tracker(checkout) and jobs.req("GET", "/api/jobs")[1]["signups"] == []


def test_a_sign_up_needs_a_subset_and_roles(jobs):
    assert jobs.req("POST", "/api/jobs/signups", {"company": ""})[0] == 400
    assert jobs.req("POST", "/api/jobs/signups", {"company": "X", "subset": "NOPE"})[0] == 404
    sid = jobs.req("POST", "/api/jobs/signups", {"company": "X"})[1]["id"]
    assert jobs.req("POST", f"/api/jobs/signups/{sid}/sent")[0] == 409        # no subset yet
    jobs.req("PUT", f"/api/jobs/signups/{sid}", {"subset": "RES-TECH-EN"})
    assert jobs.req("POST", f"/api/jobs/signups/{sid}/sent")[0] == 409        # no roles yet
    assert jobs.req("GET", "/api/jobs/signups/S99")[0] == 404


def test_an_ordinary_posting_goes_out_with_a_pinned_version(jobs):
    code, r = jobs.req("POST", "/api/jobs/app/2/sent")                        # nothing linked yet
    assert code == 409
    code, r = jobs.req("POST", "/api/jobs/app/2/sent", {"subset_id": "RES-TECH-EN"})
    assert code == 200 and r["n"] == 1
    a = r["application"]
    assert a["status"] == "applied" and a["sent"]["subset"] == "RES-TECH-EN" and a["subsets"][0]["id"] == "RES-TECH-EN"
    d = jobs.req("GET", "/api/jobs/app/2")[1]
    assert [x["n"] for x in d["sent_all"]] == [1]


def test_sending_after_going_back_reuses_that_version(jobs):
    jobs.req("POST", "/api/subsets/RES-TECH-EN/versions", {"note": "first"})
    doc = jobs.req("GET", "/api/state")[1]["doc"]
    v = next(x for x in doc["subsets"] if x["id"] == "RES-TECH-EN")
    jobs.req("PUT", "/api/item", {"kind": "subset", "id": "RES-TECH-EN", "data": {**v, "template": "modern"}, "base": None})
    jobs.req("POST", "/api/subsets/RES-TECH-EN/versions", {"note": "second"})
    jobs.req("POST", "/api/subsets/RES-TECH-EN/versions/1/restore")                # now says what v1 said
    code, r = jobs.req("POST", "/api/jobs/app/2/sent", {"subset_id": "RES-TECH-EN"})
    assert code == 200 and r["n"] == 1                                            # sent as v1, not as a copy of it
    assert r["subset_versions"]["RES-TECH-EN"]["count"] == 2


def test_what_was_sent_follows_a_renamed_subset(jobs):
    jobs.req("POST", "/api/jobs/app/2/sent", {"subset_id": "RES-TECH-EN"})
    st = jobs.req("GET", "/api/state")[1]
    v = next(x for x in st["doc"]["subsets"] if x["id"] == "RES-TECH-EN")
    jobs.req("PUT", "/api/item", {"kind": "subset", "id": "RES-TECH-EN", "data": {**v, "id": "RES-EN"}, "base": None})
    a = next(x for x in jobs.req("GET", "/api/jobs")[1]["applications"] if x["id"] == "2")
    assert a["sent"]["subset"] == "RES-EN"
