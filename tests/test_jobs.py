"""The job-posting tool connector (career-ops) and the Applications API, against a fixture checkout.

tests/fixtures/career-ops holds the files the connector reads, in the formats career-ops documents,
plus tiny fake scripts (doctor, set-status, updater). Node.js runs those; tests that need it skip
without it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import calls
from core.i18n import Prefs
from jobs import career_ops
from jobs.career_ops import CareerOps, parse_report, parse_tracker
from jobs.cv_markdown import cv_markdown, proof_points
from resume.service import Studio

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "career-ops"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    target = tmp_path / "career-ops"
    shutil.copytree(FIXTURE, target)
    (target / "node_modules" / "js-yaml").mkdir(parents=True)
    return target


@pytest.fixture
def tool(tmp_path: Path, checkout: Path) -> CareerOps:
    prefs = Prefs(tmp_path / "local")
    t = CareerOps(prefs, home=tmp_path / "local" / "providers" / "career-ops", cache=tmp_path / "cache",
                  skill_md=Path("SKILL.md"))
    t.use_folder(checkout)
    return t


@pytest.fixture
def jobs(server, data_file, checkout):
    """The test server, connected to a copy of the fixture checkout."""
    Prefs(data_file.parent / "local").save(career_ops={"path": str(checkout)})
    return server


# ---------------------------------------------------------------- reading career-ops' files
def test_tracker_rows_are_read_by_column_name(tool):
    apps = tool.applications()
    assert [a["id"] for a in apps] == ["3", "2", "1"]                      # newest first
    a = next(x for x in apps if x["id"] == "1")
    assert (a["company"], a["role"], a["score"], a["score_max"]) == ("Northwind AI", "Graduate AI Engineer", 4.3, 5.0)
    assert a["status"] == "applied" and a["status_label"] == "Applied"
    assert a["url"] == "https://boards.greenhouse.io/northwind/jobs/4001"  # the href of a [label](url) cell
    assert a["report"] == "reports/001-northwind-ai-2026-10-02.md"          # ../reports from data/, made data-root relative
    assert next(x for x in apps if x["id"] == "3")["status"] == "skip"     # "SKIP" is a label, matched case-insensitively


def test_tracker_columns_follow_the_alias_table():
    text = "| # | Fecha | Empresa | Puesto | Score | Status |\n|---|---|---|---|---|---|\n| 7 | 2026-01-02 | ACME | Dev | N/A | aplicado |\n"
    rows = parse_tracker(text, {"#": "num", "fecha": "date", "empresa": "company", "puesto": "role"})
    assert rows == [{"num": "7", "date": "2026-01-02", "company": "ACME", "role": "Dev", "score": "N/A", "status": "aplicado"}]
    assert parse_tracker("no table here", {}) == []


def test_report_machine_summary_and_sections():
    r = parse_report((FIXTURE / "reports" / "001-northwind-ai-2026-10-02.md").read_text(encoding="utf-8"))
    s = r["summary"]
    assert r["structured"] and r["score"] == 4.3 and r["url"].startswith("https://boards.greenhouse.io")
    assert s["decision"] == "Apply" and s["verdict"].startswith("**Apply.**")
    assert s["comp"] == "AUD 95,000 – 105,000 + super" and s["work_auth"] == "not_needed"
    assert len(s["requirements"]) == 5 and s["requirements"][4] == {
        "requirement": "Production operations", "importance": "meaningful", "match": "missing", "evidence": "inferred", "signal": ""}
    assert "hard_stops" not in s                                           # empty lists are left out
    titles = [x["title"] for x in r["sections"]]
    assert "A) Role Summary" in titles and not any(t.startswith(("Machine Summary", "Job Description", "Verdict")) for t in titles)
    assert r["posting"].startswith("Graduate AI Engineer — Sydney")


def test_a_report_without_a_machine_summary_still_shows():
    r = parse_report((FIXTURE / "reports" / "003-acme-robotics-2026-10-06.md").read_text(encoding="utf-8"))
    assert not r["structured"] and r["score"] == 2.4 and r["summary"] == {}
    assert [x["title"] for x in r["sections"]] == ["A. Role Summary", "E. Verdict"]


def test_application_detail_and_unknown_ids(tool):
    a = tool.application("1")
    assert a["title"] == "Evaluation: Northwind AI — Graduate AI Engineer" and a["summary"]["decision"] == "Apply"
    assert Path(a["report_path"]).is_file() and "## Machine Summary" in a["raw"]
    with pytest.raises(KeyError):
        tool.application("99")


def test_data_root_follows_career_ops_marker(tool, checkout, tmp_path, monkeypatch):
    elsewhere = tmp_path / "my-job-data"
    (elsewhere / "data").mkdir(parents=True)
    shutil.copy(checkout / "data" / "applications.md", elsewhere / "data" / "applications.md")
    (checkout / ".career-ops-data").write_text(str(elsewhere), encoding="utf-8")
    assert tool.data_root() == elsewhere.resolve() and tool.tracker_path() == elsewhere.resolve() / "data" / "applications.md"
    monkeypatch.setenv("CAREER_OPS_DATA_DIR", str(checkout))            # the environment wins over the marker
    assert tool.data_root() == checkout.resolve()


# ---------------------------------------------------------------- what Resume Studio relies on
@needs_node
def test_checks_pass_on_a_matching_checkout_and_name_what_changed(tool, checkout):
    assert all(c["ok"] for c in tool.checks()), tool.checks()
    (checkout / "modes" / "oferta.md").write_text("# a newer oferta without the summary block\n", encoding="utf-8")
    (checkout / "set-status.mjs").unlink()
    bad = {c["id"] for c in tool.checks() if not c["ok"]}
    assert bad == {"report", "status"}
    tool.application("1")                                                  # reading still works


def test_symlink_stubs_are_detected(tool, checkout):
    (checkout / ".claude" / "skills" / "career-ops" / "SKILL.md").write_text("../../../.agents/skills/career-ops/SKILL.md", encoding="utf-8")
    assert tool._skill_stubs() == [".claude/skills/career-ops/SKILL.md"]


def test_use_folder_refuses_something_else(tool, tmp_path):
    from jobs.base import ToolError
    with pytest.raises(ToolError):
        tool.use_folder(tmp_path)


# ---------------------------------------------------------------- the CV career-ops reads
def test_cv_markdown_has_the_shape_career_ops_parses(data_file):
    doc, _ = Studio(data_file).store.load()
    md = cv_markdown(doc, "en")
    assert md.splitlines()[2] == "# Alex Chen" and "MSc Computing student" in md
    assert "## Education" in md and "### Australian National University — Canberra, AU" in md
    assert "**Master of Computing**" in md and "Jul 2025 – Jun 2027" in md
    assert "- Built a **multi-stage LLM pipeline**" in md
    assert "- **Languages:** Python, TypeScript, Java, C" in md
    assert "别写 GPA" not in md                                             # the fact layer never reaches the CV
    zh = cv_markdown(doc, "zh")
    assert "## Education" in zh and "### 澳大利亚国立大学 — 堪培拉" in zh      # headings stay English, text follows the language
    notes = proof_points(doc, "en")
    assert "别写 GPA" in notes and "## Paper2Exam — Solo developer" in notes
    assert "求职方向" not in notes            # the profile's notes are targeting, not proof points a letter may quote


def test_sync_writes_then_never_overwrites_edits_made_in_career_ops(tool, checkout, data_file):
    doc, _ = Studio(data_file).store.load()
    assert tool.sync_cv(doc)["state"] == "written"
    cv = checkout / "cv.md"
    assert "# Alex Chen" in cv.read_text(encoding="utf-8") and (checkout / "article-digest.md").exists()
    assert tool.sync_cv(doc)["state"] == "same"
    doc["entries"][1]["title"] = "Paper2Exam v2"                           # the library changed: follow it
    assert tool.sync_cv(doc)["wrote"] == ["cv.md", "article-digest.md"]
    cv.write_text(cv.read_text(encoding="utf-8") + "\n- Added by a career-ops mode\n", encoding="utf-8")
    r = tool.sync_cv(doc)
    assert r["state"] == "conflict" and r["conflicts"] == ["cv.md"]
    assert "Added by a career-ops mode" in cv.read_text(encoding="utf-8")   # left alone
    assert tool.cv_state()["conflicts"] == ["cv.md"]
    assert "+- Added by a career-ops mode" in tool.cv_diff(doc, "cv.md", "resume.yaml")
    assert tool.sync_cv(doc, force=True)["wrote"] == ["cv.md"]
    assert "Added by" not in cv.read_text(encoding="utf-8") and tool.cv_state()["conflicts"] == []


def test_an_existing_cv_is_a_conflict_from_the_start(tool, checkout, data_file):
    (checkout / "cv.md").write_text("# My own CV, written in career-ops\n", encoding="utf-8")
    doc, _ = Studio(data_file).store.load()
    assert tool.sync_cv(doc)["conflicts"] == ["cv.md"]
    assert (checkout / "cv.md").read_text(encoding="utf-8").startswith("# My own CV")


def test_sync_settings_language_notes_and_off(tool, checkout, data_file):
    doc, _ = Studio(data_file).store.load()
    tool.save_settings(cv_lang="zh")
    tool.sync_cv(doc)
    assert "澳大利亚国立大学" in (checkout / "cv.md").read_text(encoding="utf-8")
    tool.save_settings(notes=False)
    tool.sync_cv(doc)
    assert not (checkout / "article-digest.md").exists()                   # ours, unchanged: removed again
    tool.save_settings(sync=False)
    (checkout / "cv.md").write_text("hand-made\n", encoding="utf-8")
    assert tool.sync_cv(doc)["state"] == "off" and (checkout / "cv.md").read_text(encoding="utf-8") == "hand-made\n"
    assert tool.cv_state()["conflicts"] == []


# ---------------------------------------------------------------- the API
def test_not_connected(server):
    code, r = server.req("GET", "/api/jobs")
    assert code == 200 and r["status"]["installed"] is False and r["applications"] == []
    assert r["status"]["tool"]["license"] == "MIT"
    code, err = server.req("POST", "/api/jobs/evaluate", {"posting": "https://example.com/job"})
    assert code == 409


def test_overview_lists_applications_and_syncs_the_cv(jobs, checkout):
    code, r = jobs.req("GET", "/api/jobs")
    assert code == 200 and r["status"]["installed"] and r["status"]["version"] == "1.36.0"
    assert [a["company"] for a in r["applications"]] == ["Acme Robotics", "Kestrel Labs", "Northwind AI"]
    assert [s["id"] for s in r["states"]][:2] == ["evaluated", "applied"]
    assert {a["id"] for a in r["actions"]} == {"interview", "research", "cover", "outreach"}
    assert (checkout / "cv.md").exists()                                    # written from resume.yaml on the way
    code, d = jobs.req("GET", "/api/jobs/app/1")
    assert d["summary"]["decision"] == "Apply" and d["subsets"] == []
    assert jobs.req("GET", "/api/jobs/app/42")[0] == 404


@needs_node
def test_status_goes_through_career_ops_own_writer(jobs, checkout):
    code, d = jobs.req("PUT", "/api/jobs/app/2/status", {"state": "interview"})
    assert code == 200 and d["status"] == "interview"
    assert "| Interview |" in (checkout / "data" / "applications.md").read_text(encoding="utf-8")
    assert jobs.req("PUT", "/api/jobs/app/2/status", {"state": "nonsense"})[0] == 409


def test_evaluate_runs_in_the_career_ops_folder(jobs, checkout, fake_claude, tmp_path):
    code, r = jobs.req("POST", "/api/jobs/evaluate", {"posting": "https://jobs.example.com/123\nSenior Agent Engineer"})
    assert code == 200 and r["chat"]["workspace"] == "career-ops"
    jobs.wait_idle(r["chat"]["id"])
    c = calls(fake_claude)[-1]
    assert Path(c["cwd"]).resolve() == checkout.resolve()                  # career-ops' CLAUDE.md / AGENTS.md load
    assert "https://jobs.example.com/123\nSenior Agent Engineer" in c["stdin"] and "PDF" in c["stdin"]
    argv = c["argv"]
    assert "Bash(node *)" in argv[argv.index("--allowedTools") + 1]
    ctx = Path(argv[argv.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    assert "career-ops" in ctx and "cv.md" in ctx and "AskUserQuestion" in ctx
    dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert any(Path(d).resolve() == Path(jobs.inst.studio.data_dir).resolve() for d in dirs)   # it may edit the library
    assert c["env"]["npm_config_cache"].endswith("npm")                    # npm's cache stays in the app's folder
    assert "--resume" not in argv
    # the next message in that chat continues the same session in the same folder
    jobs.req("POST", f"/api/chats/{r['chat']['id']}/send", {"text": "and the salary?"})
    jobs.wait_idle(r["chat"]["id"])
    c2 = calls(fake_claude)[-1]
    assert Path(c2["cwd"]).resolve() == checkout.resolve() and "--resume" in c2["argv"]


def test_empty_posting_is_refused(jobs):
    assert jobs.req("POST", "/api/jobs/evaluate", {"posting": "  "})[0] == 400


def test_actions_ask_in_plain_language(jobs, fake_claude):
    code, r = jobs.req("POST", "/api/jobs/app/1/action", {"action": "interview"})
    assert code == 200
    jobs.wait_idle(r["chat"]["id"])
    msg = calls(fake_claude)[-1]["stdin"]
    assert "interview-prep" in msg and "Northwind AI" in msg and "reports/001-northwind-ai-2026-10-02.md" in msg
    assert jobs.req("POST", "/api/jobs/app/1/action", {"action": "nope"})[0] == 409


def test_setup_fills_its_profile_from_the_fact_layer_and_follows_changes(jobs, data_file, fake_claude):
    assert jobs.req("GET", "/api/jobs")[1]["status"]["profile"] == {"filled": False, "stale": False}
    code, r = jobs.req("POST", "/api/jobs/setup", {"mode": "plan"})
    jobs.wait_idle(r["chat"]["id"])
    c = calls(fake_claude)[-1]
    assert str(data_file) in c["stdin"] and "profile.notes" in c["stdin"] and "subsets" in c["stdin"]
    assert "config/profile.yml" in c["stdin"] and c["argv"][c["argv"].index("--permission-mode") + 1] == "plan"
    assert jobs.req("GET", "/api/jobs")[1]["status"]["profile"] == {"filled": True, "stale": False}
    text = data_file.read_text(encoding="utf-8").replace("可接受远程。", "可接受远程，最低 90k。")   # the fact layer changed
    data_file.write_text(text, encoding="utf-8")
    assert jobs.req("GET", "/api/jobs")[1]["status"]["profile"]["stale"] is True
    code, r = jobs.req("POST", "/api/jobs/setup", {"update": True})
    jobs.wait_idle(r["chat"]["id"])
    assert "earlier chats" in calls(fake_claude)[-1]["stdin"] or "更新" in calls(fake_claude)[-1]["stdin"]
    assert jobs.req("GET", "/api/jobs")[1]["status"]["profile"]["stale"] is False


def test_versions_are_ranked_for_a_posting_and_one_is_linked(jobs, data_file, fake_claude):
    code, f = jobs.req("GET", "/api/jobs/app/1/fit")
    assert code == 200 and f["linked"] == [] and f["requirements"] == 5
    ids = [v["id"] for v in f["subsets"]]
    assert "ALL" not in ids and set(ids) == {"RES-TECH-EN", "RES-TECH-ZH"}
    top = f["subsets"][0]
    assert 0 <= top["score"] <= 100 and len(top["covered"]) + len(top["missing"]) == 5
    code, r = jobs.req("PUT", "/api/jobs/app/1/resume", {"subset_id": "RES-TECH-EN"})
    assert code == 200 and r["linked"] == [{"id": "RES-TECH-EN", "label": "ML 工程（英文）"}]
    jobs.req("PUT", "/api/jobs/app/2/resume", {"subset_id": "RES-TECH-EN"})       # one subset, many postings
    v = next(x for x in jobs.req("GET", "/api/state")[1]["doc"]["subsets"] if x["id"] == "RES-TECH-EN")
    assert v["postings"] == [{"tool": "career-ops", "id": "1"}, {"tool": "career-ops", "id": "2"}]
    code, r = jobs.req("PUT", "/api/jobs/app/1/resume", {"subset_id": "RES-TECH-ZH"})   # one posting, one subset
    assert r["linked"] == [{"id": "RES-TECH-ZH", "label": "ML 工程（中文）"}]
    assert jobs.req("GET", "/api/jobs")[1]["applications"][-1]["subsets"][0]["id"] == "RES-TECH-ZH"
    assert jobs.req("PUT", "/api/jobs/app/1/resume", {"subset_id": None})[1]["linked"] == []
    assert jobs.req("PUT", "/api/jobs/app/1/resume", {"subset_id": "ALL"})[0] == 404
    code, r = jobs.req("POST", "/api/jobs/app/1/choose", {})
    jobs.wait_idle(r["chat"]["id"])
    c = calls(fake_claude)[-1]
    assert "RES-TECH-EN" in c["stdin"] and "postings" in c["stdin"] and r["chat"].get("workspace") is None


def test_an_older_single_posting_still_counts(jobs, data_file):
    text = data_file.read_text(encoding="utf-8").replace("  - id: RES-TECH-ZH\n", "  - id: RES-TECH-ZH\n    posting: {tool: career-ops, id: '2'}\n")
    data_file.write_text(text, encoding="utf-8")
    assert jobs.req("GET", "/api/jobs/app/2")[1]["subsets"] == [{"id": "RES-TECH-ZH", "label": "ML 工程（中文）"}]
    jobs.req("PUT", "/api/jobs/app/2/resume", {"subset_id": "RES-TECH-EN"})
    vs = {x["id"]: x for x in jobs.req("GET", "/api/state")[1]["doc"]["subsets"]}
    assert "posting" not in vs["RES-TECH-ZH"] and vs["RES-TECH-EN"]["postings"] == [{"tool": "career-ops", "id": "2"}]


def test_a_new_version_is_for_a_kind_of_role_not_a_company(jobs, data_file, checkout, fake_claude):
    code, r = jobs.req("POST", "/api/jobs/app/1/tailor", {"label": "AI Engineer"})
    assert code == 200
    vid = r["subset_id"]
    v = next(x for x in r["doc"]["subsets"] if x["id"] == vid)
    assert v["label"] == "AI Engineer" and v["postings"] == [{"tool": "career-ops", "id": "1"}]
    assert v["entries"] == []                                               # Claude picks them, the user reviews
    assert r["chat"].get("workspace") is None                               # a resume chat, in the data folder
    jobs.wait_idle(r["chat"]["id"])
    c = calls(fake_claude)[-1]
    assert Path(c["cwd"]).resolve() == data_file.parent.resolve()
    assert vid in c["stdin"] and "001-northwind-ai-2026-10-02.md" in c["stdin"]
    assert any(Path(c["argv"][i + 1]).resolve() == checkout.resolve() for i, a in enumerate(c["argv"]) if a == "--add-dir")
    code, d = jobs.req("GET", "/api/jobs/app/1")
    assert d["subsets"] == [{"id": vid, "label": "AI Engineer"}]
    # without a name it is named after the role, never the company
    code, r = jobs.req("POST", "/api/jobs/app/2/tailor", {})
    assert next(x for x in r["doc"]["subsets"] if x["id"] == r["subset_id"])["label"] == "Machine Learning Engineer Intern"
    jobs.wait_idle(r["chat"]["id"])
    # adjusting an existing subset links it instead
    code, r2 = jobs.req("POST", "/api/jobs/app/1/tailor", {"subset_id": "RES-TECH-EN"})
    jobs.wait_idle(r2["chat"]["id"])
    assert jobs.req("GET", "/api/jobs/app/1")[1]["subsets"] == [{"id": "RES-TECH-EN", "label": "ML 工程（英文）"}]

def test_cv_conflict_is_resolved_in_a_chat_without_overwriting(jobs, checkout, fake_claude):
    jobs.req("GET", "/api/jobs")
    cv = checkout / "cv.md"
    cv.write_text(cv.read_text(encoding="utf-8") + "- Led a hackathon team\n", encoding="utf-8")
    code, r = jobs.req("GET", "/api/jobs")
    assert r["status"]["cv"]["conflicts"] == ["cv.md"]
    code, r = jobs.req("POST", "/api/jobs/cv/resolve", {"file": "cv.md"})
    assert code == 200
    jobs.wait_idle(r["chat"]["id"])
    assert "+- Led a hackathon team" in calls(fake_claude)[-1]["stdin"]
    assert "Led a hackathon team" in cv.read_text(encoding="utf-8")          # the chat's own sync didn't overwrite it
    code, r = jobs.req("POST", "/api/jobs/cv/sync", {"force": True})
    assert r["sync"]["wrote"] == ["cv.md"] and "hackathon" not in cv.read_text(encoding="utf-8")


def test_settings_are_this_computers(jobs, data_file, checkout):
    code, r = jobs.req("PUT", "/api/jobs/settings", {"cv_lang": "zh", "notes": False})
    assert r["status"]["settings"]["cv_lang"] == "zh" and r["status"]["settings"]["notes"] is False
    saved = json.loads((data_file.parent / "local" / "prefs.json").read_text(encoding="utf-8"))["career_ops"]
    assert saved["cv_lang"] == "zh" and saved["path"] == str(checkout)
    assert "陈亚历" in (checkout / "cv.md").read_text(encoding="utf-8")


@needs_node
def test_update_check_uses_career_ops_own_updater(jobs):
    code, r = jobs.req("GET", "/api/jobs/update")
    assert code == 200 and r["status"] == "update-available" and r["remote"] == "1.37.0"


def test_fork_keeps_the_chat_in_its_folder(jobs, fake_claude):
    code, r = jobs.req("POST", "/api/jobs/evaluate", {"posting": "x"})
    cid = r["chat"]["id"]
    jobs.wait_idle(cid)
    jobs.req("POST", f"/api/chats/{cid}/send", {"text": "second"})
    jobs.wait_idle(cid)
    code, f = jobs.req("POST", f"/api/chats/{cid}/fork", {"index": 2})
    assert code == 200 and f["chat"]["workspace"] == "career-ops"


def test_a_chat_for_a_disconnected_tool_says_so(jobs, data_file, fake_claude):
    code, r = jobs.req("POST", "/api/jobs/chat", {})
    Prefs(data_file.parent / "local").save(career_ops={"path": str(data_file.parent / "gone")})
    jobs.inst.chats.workspaces.clear()
    code, err = jobs.req("POST", f"/api/chats/{r['chat']['id']}/send", {"text": "hi"})
    assert code == 409 and "career-ops" in err["error"]


def test_tool_list_is_generic():
    assert issubclass(career_ops.CareerOps, career_ops.JobTool)


# ---------------------------------------------------------------- every mode, and its own web interface
def test_modes_come_from_its_catalog_and_its_mode_files(tool, checkout):
    modes = {m["id"]: m for m in tool.modes()}
    assert "auto" not in modes                                             # the router, not a mode to start
    assert modes["job"] == {"id": "job", "file": "modes/oferta.md", "purpose": "Full A-G evaluation of a single offer"}
    assert modes["triage"]["purpose"] == "First-Pass Quick Score"          # a file without a catalog row yet
    assert modes["interview/plan"] == {"id": "interview/plan", "file": "modes/interview/plan.md", "purpose": "Interview Prep Planner"}
    (checkout / "modes" / "new-thing.md").write_text("# Mode: new-thing — Something added upstream\n", encoding="utf-8")
    assert "new-thing" in {m["id"] for m in tool.modes()}                  # a new version's mode shows up by itself


def test_a_version_without_a_web_interface_or_catalog_is_named(tool, checkout):
    shutil.rmtree(checkout / "web")
    (checkout / "modes" / "README.md").unlink()
    bad = {c["id"] for c in tool.checks() if not c["ok"]}
    assert "web" in bad and not tool.has_web()
    assert {m["id"] for m in tool.modes()} >= {"triage", "interview/plan"}  # the mode files still count


@needs_node
def test_web_is_installed_and_built_only_when_its_source_changed(tool, checkout, monkeypatch):
    import subprocess
    web = checkout / "web"
    (web / "package-lock.json").write_text("{}", encoding="utf-8")
    ran: list[str] = []

    def ci(cmd, timeout=120, cwd=None):
        ran.append("ci:" + Path(cwd).name)
        (web / "node_modules" / "next").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    real_run = subprocess.run

    def build(cmd, **kw):
        if cmd[1:] == ["run", "build"]:
            ran.append("build:" + Path(kw["cwd"]).name)
            (web / ".next").mkdir(exist_ok=True)
            (web / ".next" / "BUILD_ID").write_text("x", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(tool, "_run", ci)
    monkeypatch.setattr(career_ops.subprocess, "run", build)
    steps = []
    spec = tool.web(lambda s, st: steps.append((s, st)))
    assert ran == ["ci:web", "build:web"] and ("web_build", "done") in steps
    assert spec.argv[1:] == ["server.mjs", "start", "-p", "{port}"] and Path(spec.cwd) == web   # its own launcher
    assert spec.env["CAREER_OPS_ROOT"] == str(tool.data_root()) and spec.env["NEXT_TELEMETRY_DISABLED"] == "1"
    assert spec.env["npm_config_cache"].startswith(str(tool.cache))            # nothing in the user profile

    ran.clear()
    tool.web(lambda *a: None)
    assert ran == []                                                         # nothing changed: just start it
    (web / "src").mkdir(exist_ok=True)
    (web / "src" / "page.tsx").write_text("export default 1", encoding="utf-8")
    tool.web(lambda *a: None)
    assert ran == ["build:web"]                                              # an update changed its source
    ran.clear()
    (web / "package-lock.json").write_text('{"v": 2}', encoding="utf-8")
    tool.web(lambda *a: None)
    assert ran == ["ci:web", "build:web"]                                    # …or its dependencies
    ran.clear()
    tool.web(lambda *a: None, rebuild=True)
    assert ran == ["ci:web", "build:web"]


def test_a_mode_runs_as_a_chat_in_its_folder(jobs, checkout, fake_claude):
    code, r = jobs.req("GET", "/api/jobs")
    assert {"triage", "interview/plan", "job"} <= {m["id"] for m in r["modes"]}
    assert r["web"]["available"] and r["web"]["state"] == "off" and r["web"]["routes"]["application"] == "/pipeline/{id}"
    code, r = jobs.req("POST", "/api/jobs/mode", {"mode": "interview/plan"})
    assert code == 200 and r["chat"]["workspace"] == "career-ops"
    jobs.wait_idle(r["chat"]["id"])
    c = calls(fake_claude)[-1]
    assert Path(c["cwd"]).resolve() == checkout.resolve()
    assert "interview/plan" in c["stdin"] and "modes/interview/plan.md" in c["stdin"] and "AskUserQuestion" in c["stdin"]
    assert jobs.req("POST", "/api/jobs/mode", {"mode": "nope"})[0] == 404


def test_its_web_interface_starts_and_stops(jobs, checkout):
    import time
    import urllib.request
    code, w = jobs.req("POST", "/api/jobs/web/start", {})
    assert code == 200 and w["state"] in ("preparing", "starting", "running")
    end = time.time() + 20
    while w["state"] != "running" and time.time() < end:
        time.sleep(0.1)
        w = jobs.req("GET", "/api/jobs/web")[1]
    assert w["state"] == "running" and w["steps"] == {"web_deps": "done", "web_build": "done"}
    window = jobs.url.rstrip("/")
    req = urllib.request.Request(w["url"] + "pipeline/3", headers={"Sec-Fetch-Site": "same-site", "Referer": window + "/"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert "resume-studio-webhost" in r.read().decode("utf-8")   # shown in the window's frame
    assert jobs.req("POST", "/api/jobs/web/stop", {})[1]["state"] == "off"


def test_no_web_interface_says_so(jobs, checkout):
    shutil.rmtree(checkout / "web")
    assert jobs.req("GET", "/api/jobs/web")[1] == {"available": False, "state": "off"}
    assert jobs.req("POST", "/api/jobs/web/start", {})[0] == 409
