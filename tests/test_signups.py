"""Rows into career-ops' tracker through its own writer, and which subset version went out to a posting there.
(Sign-ups on a company's own site are the job-hunt tracker's now: tests/test_hunt.py.)"""
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


# ---------------------------------------------------------------- a posting in career-ops' tracker
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
