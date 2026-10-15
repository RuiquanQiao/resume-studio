"""Autofill: the browser Resume Studio drives (core/browser.py, against tests/fake_sites in a headless
Edge), the profile a resume version gives a site, the plan between that and what Claude read there, and
the routes plus scripts/browser.py that Claude works through."""
from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, calls

from autofill import profile
from autofill.sites import PRESETS
from core.store import YamlStore

BROWSER_PY = ROOT / "scripts" / "browser.py"
SITES_DIR = Path(__file__).resolve().parent / "fake_sites"
LI = PRESETS["linkedin"]


def load(path: Path):
    return YamlStore(path).load()[0]


# ---- what a resume version gives a site ----------------------------------------------------
def test_desired_profile_from_a_version(data_file):
    doc = load(data_file)
    all_v = next(v for v in doc["versions"] if v["id"] == "ALL")
    want = profile.desired(doc, all_v, "en", LI)
    assert want["basics"] == {"headline": "MSc Computing student"}
    by_key = {i["key"]: i for i in want["items"]}
    assert by_key["EXP-001"]["section"] == "education"
    assert by_key["EXP-001"]["fields"]["org"] == "Australian National University"
    assert by_key["EXP-001"]["fields"]["degree"] == "Master of Computing"
    assert by_key["EXP-001"]["fields"]["start"] == "2025-07"
    p2e = by_key["EXP-002"]
    assert p2e["section"] == "projects" and p2e["fields"]["end"] == "present"
    # the resume's markup is gone, one line per bullet
    assert p2e["fields"]["description"].splitlines()[1] == \
        "• Built a multi-stage LLM pipeline that turns research papers into reading-comprehension exams"
    assert "notes" not in json.dumps(want) and "AI 写的" not in json.dumps(want, ensure_ascii=False)   # fact layer stays out
    assert want["skills"][:3] == ["Python", "TypeScript", "Java"] and want["skills"].count("Next.js") == 1


def test_desired_follows_the_job_and_the_language(data_file):
    doc = load(data_file)
    job = next(v for v in doc["versions"] if v["id"] == "RES-TECH-ZH")
    job["entries"] = ["EXP-003", "EXP-001"]
    want = profile.desired(doc, job, "zh", PRESETS["boss"])
    assert [i["key"] for i in want["items"]] == ["EXP-001", "EXP-003"]   # the job's section order: education first
    assert want["items"][0]["fields"]["org"] == "澳大利亚国立大学"
    assert want["basics"] == {} and want["skills"] == []                 # BOSS has no headline or skills list


def plan_for(data_file, current, site=LI, choices=None):
    doc = load(data_file)
    want = profile.desired(doc, next(v for v in doc["versions"] if v["id"] == "ALL"), "en", site)
    cur, errs = profile.validate_current(current, site)
    assert errs == []
    return profile.make_plan(site, want, cur, choices)


SITE_NOW = {
    "url": "https://www.linkedin.com/in/alex/",
    "basics": {"headline": "Student at ANU", "about": ""},
    "items": [
        {"section": "education", "key": "EXP-001", "fields": {"org": "The Australian National University", "degree": "Master of Computing",
                                                           "start": "Jul 2025", "end": "Jun 2027",
                                                           "description": "Coursework: Statistical ML, Deep Learning, Computer Vision, Software Engineering"}},
        {"section": "projects", "key": None, "fields": {"name": "Paper2Exam", "start": "2025-08", "end": "present",
                                                       "description": "Turns papers into exams"}},
        {"section": "projects", "key": None, "fields": {"name": "Old hackathon thing", "start": "2022-03"}},
    ],
    "skills": ["python", "React"],
    "drafts": {"about": "MSc Computing student building LLM tools."},
}


def test_plan_compares_field_by_field(data_file):
    plan = plan_for(data_file, SITE_NOW)
    items = {i["id"]: i for i in plan["items"]}
    assert items["basics.headline"]["op"] == "update" and items["basics.headline"]["checked"]
    about = items["basics.about"]
    assert about["op"] == "update" and about["drafted"] and about["fields"][0]["to"].startswith("MSc Computing student building")
    assert about["fields"][0]["limit"] == 2600
    # same school, dates written differently on the site: nothing to change
    assert items["education:EXP-001"]["op"] == "same" and not items["education:EXP-001"]["checked"]
    # matched by name although Claude gave no key; only the description differs
    p2e = items["projects:EXP-002"]
    assert p2e["op"] == "update" and [f["name"] for f in p2e["fields"]] == ["link", "description"]
    assert p2e["fields"][1]["from"] == "Turns papers into exams"
    assert items["projects:EXP-003"]["op"] == "add" and items["projects:EXP-003"]["checked"]
    # only on the site: offered, never ticked by default
    gone = items["projects:site2"]
    assert gone["op"] == "remove" and not gone["checked"] and gone["label"] == "Old hackathon thing"
    skills = items["skills"]
    assert skills["op"] == "add" and "Python" not in skills["fields"][0]["to"] and "TypeScript" in skills["fields"][0]["to"]
    assert plan["counts"] == {"add": 2, "update": 3, "remove": 1, "same": 1}


def test_choices_tick_and_edit(data_file):
    long = "x" * 2601
    plan = plan_for(data_file, SITE_NOW, choices={"projects:site2": {"checked": True},
                                                  "basics.about": {"fields": {"about": long}},
                                                  "basics.headline": {"checked": False}})
    items = {i["id"]: i for i in plan["items"]}
    assert items["projects:site2"]["checked"] and not items["basics.headline"]["checked"]
    f = items["basics.about"]["fields"][0]
    assert f["edited"] and f["over"] and f["to"] == long


def test_current_is_checked():
    _, errs = profile.validate_current({"items": [{"section": "volunteering", "fields": {}},
                                                  {"section": "projects", "fields": {"company": "x"}}],
                                        "basics": {"tagline": "x"}, "skills": "Python"}, LI)
    text = "\n".join(errs)
    assert "items[0].section" in text and "company not known for projects" in text
    assert "basics.tagline" in text and "`skills` must be a list" in text
    _, errs = profile.validate_current({"items": [{"section": "skills", "fields": {}}]}, PRESETS["boss"])
    assert errs   # BOSS has no such section


def test_signed_out_by_address():
    assert LI.signed_out("https://www.linkedin.com/authwall?trk=x")
    assert LI.signed_out("https://www.linkedin.com/login")
    assert not LI.signed_out("https://www.linkedin.com/in/alex/")
    assert not LI.signed_out("https://example.com/login")
    assert PRESETS["boss"].signed_out("https://www.zhipin.com/web/user/?ka=header-login")


# ---- the routes Claude and the window use ---------------------------------------------------
def test_routes_from_choice_to_plan_to_report(server, data_file):
    st, r = server.req("GET", "/api/autofill")
    assert st == 200 and [s["id"] for s in r["sites"]] == ["linkedin", "boss"]
    assert r["sites"][0]["changes"] is None and r["browser"]["state"] == "off"
    st, v = server.req("GET", "/api/autofill/linkedin")
    assert v["version_id"] == "ALL" and v["lang"] == "en" and v["plan"]["read"] is False
    st, v = server.req("GET", "/api/autofill/boss")
    assert v["lang"] == "zh"   # the site's usual language when the file has it
    st, v = server.req("PUT", "/api/autofill/linkedin", {"version_id": "RES-TECH-EN", "lang": "zh"})
    assert st == 200 and v["version_id"] == "RES-TECH-EN" and v["lang"] == "zh"
    assert server.req("PUT", "/api/autofill/linkedin", {"version_id": "NOPE"})[0] == 404
    assert server.req("PUT", "/api/autofill/linkedin", {"lang": "fr"})[0] == 400
    server.req("PUT", "/api/autofill/linkedin", {"version_id": "ALL", "lang": "en"})
    assert server.req("POST", "/api/autofill/linkedin/apply", {})[0] == 409   # nothing read yet

    st, r = server.req("POST", "/api/autofill/linkedin/current", {"items": [{"section": "pets"}]})
    assert st == 400 and "items[0].section" in r["error"]
    st, r = server.req("POST", "/api/autofill/linkedin/current", SITE_NOW)
    assert st == 200 and "2 to add, 3 to change, 1 only on LinkedIn, 1 already the same" in r["text"]
    st, v = server.req("GET", "/api/autofill/linkedin")
    assert v["plan"]["read"] and v["read_at"] and v["current"]["url"] == SITE_NOW["url"]
    # state lives next to the data file
    assert (data_file.parent / ".studio" / "autofill" / "linkedin.json").is_file()

    assert server.req("POST", "/api/autofill/linkedin/report", {"id": "nope", "state": "done"})[0] == 404
    assert server.req("POST", "/api/autofill/linkedin/report", {"id": "skills", "state": "great"})[0] == 400
    assert server.req("POST", "/api/autofill/linkedin/report", {"id": "skills", "state": "done", "note": "added 7"})[0] == 200
    st, v = server.req("GET", "/api/autofill/linkedin")
    assert next(i for i in v["plan"]["items"] if i["id"] == "skills")["applied"]["state"] == "done"
    assert server.req("GET", "/api/autofill/nowhere")[0] == 404


def test_the_user_picks_the_sites(server, data_file):
    st, r = server.req("GET", "/api/autofill")
    assert [p["id"] for p in r["presets"]] == ["linkedin", "boss"] and all(p["added"] for p in r["presets"])
    server.req("POST", "/api/autofill/linkedin/current", SITE_NOW)

    # off the list, and back with what was read there
    st, r = server.req("DELETE", "/api/autofill/linkedin")
    assert st == 200 and [s["id"] for s in r["sites"]] == ["boss"] and not r["presets"][0]["added"]
    assert server.req("GET", "/api/autofill/linkedin")[0] == 404
    st, r = server.req("POST", "/api/autofill/sites", {"preset": "linkedin"})
    assert st == 200 and r["site"]["id"] == "linkedin" and [s["id"] for s in r["sites"]] == ["boss", "linkedin"]
    assert server.req("GET", "/api/autofill/linkedin")[1]["plan"]["read"]

    # any other site, by its address: every section until a read says what it has
    assert server.req("POST", "/api/autofill/sites", {"name": "x", "home": "not an address"})[0] == 400
    assert server.req("POST", "/api/autofill/sites", {"home": "https://a.com", "lang": "fr"})[0] == 400
    st, r = server.req("POST", "/api/autofill/sites", {"name": "SEEK", "home": "www.seek.com.au/profile/me", "lang": "en",
                                                       "notes": "My profile is under Profile → Career history."})
    seek = r["site"]
    assert seek["id"] == "seek" and seek["home"] == "https://www.seek.com.au/profile/me" and not seek["preset"]
    assert seek["sections"] == ["experience", "education", "projects", "awards", "skills"] and not seek["learned"]
    v = server.req("GET", "/api/autofill/seek")[1]
    assert v["lang"] == "en" and v["plan"]["read"] is False
    # a second site on the same domain gets an id of its own
    assert server.req("POST", "/api/autofill/sites", {"name": "SEEK 2", "home": "https://seek.com.au"})[1]["site"]["id"] == "seek-2"
    server.req("DELETE", "/api/autofill/seek-2")

    # the first read has to say what the site has; the plan then fits it
    now = {"url": "https://www.seek.com.au/profile/me", "basics": {"about": ""}, "drafts": {"about": "MSc student."},
           "items": [{"section": "experience", "key": None, "fields": {"title": "Tutor", "org": "ANU", "start": "2026-02"}}]}
    st, r = server.req("POST", "/api/autofill/seek/current", now)
    assert st == 400 and "`site`" in r["error"]
    st, r = server.req("POST", "/api/autofill/seek/current", {**now, "site": {"sections": ["experience", "pets"]}})
    assert st == 400 and "site.sections" in r["error"]
    st, r = server.req("POST", "/api/autofill/seek/current", {**now, "site": {
        "sections": ["experience", "education", "skills"], "basics": ["about"], "limits": {"basics.about": 500},
        "lacks": {"experience": ["location"]}}})
    assert st == 200, r
    v = server.req("GET", "/api/autofill/seek")[1]
    assert v["site"]["learned"] and v["site"]["sections"] == ["experience", "education", "skills"]
    assert v["site"]["fields"]["experience"] == ["title", "org", "start", "end", "description"]
    assert {i["section"] for i in v["plan"]["items"]} <= {"basics", "experience", "education", "skills"}
    about = next(i for i in v["plan"]["items"] if i["id"] == "basics.about")
    assert about["fields"][0]["limit"] == 500
    # later reads may leave `site` out
    assert server.req("POST", "/api/autofill/seek/current", now)[0] == 200

    # the user changes any site; an empty field goes back to the preset's own
    st, r = server.req("PUT", "/api/autofill/linkedin/settings", {"name": "LinkedIn (work)", "notes": "Skip the Featured card.", "index": 0})
    assert st == 200 and [s["id"] for s in r["sites"]] == ["linkedin", "boss", "seek"] and r["sites"][0]["name"] == "LinkedIn (work)"
    assert server.req("PUT", "/api/autofill/seek/settings", {"home": ""})[0] == 400   # a site of one's own needs its address
    server.req("PUT", "/api/autofill/linkedin/settings", {"name": ""})
    assert server.req("GET", "/api/autofill")[1]["sites"][0]["name"] == "LinkedIn"
    saved = json.loads((data_file.parent / ".studio" / "autofill" / "sites.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["sites"]] == ["linkedin", "boss", "seek"] and "name" not in saved["sites"][0]

    # what Claude is told: the user's notes, and (for a site of their own) to say what it has
    from autofill import prompts
    from autofill.service import Autofill
    from resume.service import Studio
    a = Autofill(Studio(data_file))
    li = a.site("linkedin")
    assert "Skip the Featured card." in prompts.read_context(li, "d.yaml", {"items": []}, "en")
    assert "new to Resume Studio" not in prompts.read_context(li, "d.yaml", {"items": []}, "en")
    ctx = prompts.read_context(a.site("seek"), "d.yaml", {"items": []}, "en")
    assert "Profile → Career history." in ctx and '"site":' in ctx and "Last time it had" in ctx

    # every site can go, presets too: the page then asks which to add
    for sid in ("linkedin", "boss", "seek"):
        server.req("DELETE", "/api/autofill/" + sid)
    assert server.req("GET", "/api/autofill")[1]["sites"] == []


def test_over_the_limit_blocks_apply(server):
    server.req("POST", "/api/autofill/linkedin/current", SITE_NOW)
    server.req("PUT", "/api/autofill/linkedin", {"choices": {"basics.about": {"fields": {"about": "y" * 3000}}}})
    st, r = server.req("POST", "/api/autofill/linkedin/apply", {})
    assert st == 409 and "about" in r["error"]


def run_steps(server, data_file, steps: dict):
    """What fake Claude runs when a message has these words (scripts/browser.py, as Bash)."""
    os.environ["FAKE_CLAUDE_RUN"] = json.dumps(steps)


@pytest.fixture
def fake_site():
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(SITES_DIR))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def headless(monkeypatch):
    monkeypatch.setenv("RESUME_STUDIO_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("RESUME_STUDIO_BROWSER_PACE", "0")
    monkeypatch.setenv("RESUME_STUDIO_BROWSER", "msedge")


@pytest.fixture
def live(headless, server, data_file, fake_site, monkeypatch):
    """The server with a headless browser, and LinkedIn pointed at the fake profile page."""
    import dataclasses
    fake = dataclasses.replace(LI, home=fake_site + "/linkedin/profile.html", domains=["127.0.0.1"])
    monkeypatch.setitem(PRESETS, "linkedin", fake)
    (data_file.parent / ".studio").mkdir(exist_ok=True)
    (data_file.parent / ".studio" / "server.json").write_text(json.dumps({"url": server.url}), encoding="utf-8")
    server.inst.browser.headless, server.inst.browser.pace = True, 0
    yield server
    os.environ.pop("FAKE_CLAUDE_RUN", None)


def cli(data_file, *args, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BROWSER_PY), "--data", str(data_file), *args], input=stdin.encode("utf-8"),
                          capture_output=True, timeout=120)


def out(p) -> str:
    return (p.stdout + p.stderr).decode("utf-8", errors="replace")


def ref_of(text: str, needle: str) -> str:
    line = next(l for l in text.splitlines() if needle in l)
    return line.split("]")[0].strip(" [")


def test_browser_command_drives_a_real_page(live, data_file, fake_site):
    """scripts/browser.py → the window → CDP → a headless Edge with its own profile."""
    r = cli(data_file, "open", fake_site + "/linkedin/profile.html")
    assert r.returncode == 0, out(r)
    assert "ok: Alex Chen | Profile" in out(r)
    snap = out(cli(data_file, "snapshot"))
    # the whole document, also below the fold
    assert "## Education" in snap and "Australian National University" in snap and "[1] button \"Edit intro\"" in snap
    r = cli(data_file, "click", ref_of(snap, '"Edit intro"'))
    assert r.returncode == 0, out(r)
    assert 'target: button "Edit intro"' in out(r)   # what the chat shows as the step
    dlg = out(cli(data_file, "snapshot", "--dialog"))
    assert dlg.splitlines()[1].startswith("=== dialog \"Edit intro\"") and "maxlength=220" in dlg
    r = cli(data_file, "type", ref_of(dlg, '"Headline*"'), "--stdin", stdin="AI Agent Engineer · 大模型工具")
    assert 'the field now holds: "AI Agent Engineer · 大模型工具"' in out(r)
    cli(data_file, "click", ref_of(dlg, 'button "Save"'))
    snap = out(cli(data_file, "snapshot"))
    assert "AI Agent Engineer · 大模型工具" in snap.splitlines()[2]
    # what the page's own script holds came from trusted input events
    page = live.inst.browser._eval_page("JSON.stringify({h: DB.headline, untrusted: !!state.untrusted})")
    assert json.loads(page) == {"h": "AI Agent Engineer · 大模型工具", "untrusted": False}
    shot = out(cli(data_file, "screenshot"))
    path = shot.split("saved: ")[1].strip()
    assert Path(path).is_file() and Path(path).is_relative_to(data_file.parent / ".studio" / "browser")
    assert "only files in" in out(cli(data_file, "upload", "1", str(ROOT / "README.md")))
    assert "not a text field" in out(cli(data_file, "type", "1", "--text", "x"))
    assert "is not on the page" in out(cli(data_file, "click", "999"))
    assert cli(data_file, "press", "Escape").returncode == 0
    # its own profile, inside the app's folder (never the user's everyday browser profile)
    prof = data_file.parent / "local" / "browser" / "msedge"
    assert (prof / "DevToolsActivePort").is_file()
    st, b = live.req("GET", "/api/browser")
    assert b["state"] == "running" and b["browser"] == "Microsoft Edge" and b["url"].endswith("/linkedin/profile.html")
    live.req("POST", "/api/browser/stop", {})
    assert live.req("GET", "/api/browser")[1]["state"] == "off"


def test_read_then_apply_with_claude(live, data_file, fake_claude):
    b = [sys.executable, str(BROWSER_PY), "--data", str(data_file)]
    now = {**SITE_NOW, "url": PRESETS["linkedin"].home}
    run_steps(live, data_file, {
        "读取": [{"argv": b + ["open", PRESETS["linkedin"].home]}, {"argv": b + ["snapshot"]},
                 {"argv": b + ["profile", "linkedin", "--stdin"], "stdin": json.dumps(now)}],
        "逐条": [{"argv": b + ["report", "linkedin", "basics.headline", "doing"]},
                 {"argv": b + ["report", "linkedin", "basics.headline", "done"]},
                 {"argv": b + ["report", "linkedin", "projects:EXP-003", "failed", "--note", "no Projects card"]}],
    })
    st, r = live.req("POST", "/api/autofill/linkedin/open", {})
    assert st == 200 and r["signed_out"] is False and r["title"] == "Alex Chen | Profile"
    st, r = live.req("POST", "/api/autofill/linkedin/read", {})
    assert st == 200
    chat = r["chat"]
    assert chat["workspace"] == "autofill" and chat["site"] == "linkedin"
    live.wait_idle(chat["id"], timeout=60)
    first = calls(fake_claude)[-1]
    assert "LinkedIn" in first["stdin"] and "All entries" not in first["stdin"]
    # the chat shows only the short message; Claude gets the procedure with it
    st, ch = live.req("GET", "/api/chats/" + chat["id"])
    assert "READ the" not in ch["messages"][0]["text"] and "LinkedIn" in ch["messages"][0]["text"]
    ctx = Path(first["argv"][first["argv"].index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    assert str(BROWSER_PY) in ctx   # every message in these chats: how to use the browser
    assert "READ the" in first["stdin"] and '"key": "EXP-002"' in first["stdin"] and "Notify network" in first["stdin"]
    runs = calls(Path(str(fake_claude) + ".run"))
    assert all(x["code"] == 0 for x in runs), runs
    assert "saved. The window now shows" in runs[-1]["out"]
    st, v = live.req("GET", "/api/autofill/linkedin")
    assert v["plan"]["read"] and v["read_chat"] == chat["id"]

    # the user unticks two and edits one; only the rest go to Claude, with the edited text
    live.req("PUT", "/api/autofill/linkedin", {"choices": {"basics.about": {"checked": False}, "skills": {"checked": False},
                                                           "projects:EXP-002": {"checked": False},
                                                           "basics.headline": {"fields": {"headline": "ML engineer in training"}}}})
    st, r = live.req("POST", "/api/autofill/linkedin/apply", {})
    assert st == 200, r
    live.wait_idle(r["chat"]["id"], timeout=60)
    last = calls(fake_claude)[-1]
    block = last["stdin"][last["stdin"].index("## The approved changes"):]
    approved = json.loads(block.split("```")[1])
    assert [a["id"] for a in approved] == ["basics.headline", "projects:EXP-003"]
    assert approved[0]["set"] == {"headline": "ML engineer in training"}
    st, v = live.req("GET", "/api/autofill/linkedin")
    items = {i["id"]: i for i in v["plan"]["items"]}
    assert items["basics.headline"]["applied"]["state"] == "done"
    assert items["projects:EXP-003"]["applied"] == {**items["projects:EXP-003"]["applied"], "state": "failed", "note": "no Projects card"}
    assert v["applying"] == []   # cleared when the chat ended
    # the chat list knows these chats belong to Autofill
    st, lst = live.req("GET", "/api/chats")
    mine = [c for c in lst["chats"] if c.get("workspace") == "autofill"]
    assert len(mine) == 2


def test_signed_out_shows_up(live, data_file, fake_site, monkeypatch):
    import dataclasses
    monkeypatch.setitem(PRESETS, "linkedin", dataclasses.replace(PRESETS["linkedin"], home=fake_site + "/login/index.html",
                                                               login_paths=["/login"]))
    st, r = live.req("POST", "/api/autofill/linkedin/open", {})
    assert st == 200 and r["signed_out"] is True
    assert live.req("GET", "/api/autofill")[1]["sites"][0]["signed_out"] is True
    r = cli(data_file, "signed-out", "linkedin")
    assert "sign in" in out(r)


def test_degree_first_resumes_and_fields_a_site_lacks(data_file):
    doc = load(data_file)
    edu = next(e for e in doc["entries"] if e["id"] == "EXP-001")
    edu["title"], edu["subtitle"] = edu["subtitle"], edu["title"]   # "Master of Computing" on top, the school under it
    want = profile.desired(doc, next(v for v in doc["versions"] if v["id"] == "ALL"), "en", LI)
    by_key = {i["key"]: i for i in want["items"]}
    assert by_key["EXP-001"]["fields"]["org"] == "Australian National University"
    assert by_key["EXP-001"]["fields"]["degree"] == "Master of Computing"
    # LinkedIn's projects have no role field: it leads the description instead of disappearing
    p2e = by_key["EXP-002"]["fields"]
    assert "role" not in p2e and p2e["description"].startswith("Solo developer\n• Built")
    assert by_key["EXP-002"]["label"] == "Paper2Exam"
    boss = profile.desired(doc, next(v for v in doc["versions"] if v["id"] == "ALL"), "zh", PRESETS["boss"])
    assert next(i for i in boss["items"] if i["key"] == "EXP-002")["fields"]["role"] == "独立开发"
    _, errs = profile.validate_current({"items": [{"section": "projects", "fields": {"role": "x"}}]}, LI)
    assert errs and "not known for projects on LinkedIn" in errs[0]


def test_same_school_with_the_degree_worded_differently_is_one_item(data_file):
    plan = plan_for(data_file, {"items": [{"section": "education", "key": None, "fields": {
        "org": "Australian National University", "degree": "Master's degree, Computing", "start": "2025-07", "end": "2027-06"}}]})
    items = {i["id"]: i for i in plan["items"]}
    assert "education:site0" not in items                     # not "remove this" plus "add that"
    edu = items["education:EXP-001"]
    assert edu["op"] == "update" and [f["name"] for f in edu["fields"]] == ["degree", "description"]

def test_browser_commands_become_steps():
    from core.browser import steps_of

    def one(cmd):
        got = steps_of(cmd)
        assert len(got) == 1, got
        return got[0]

    b = 'python "E:\\Forge\\resume-studio\\scripts\\browser.py" --data "E:\\data\\resume.yaml"'
    assert one(b + " click 12") == {"cmd": "click", "ref": "12"}
    assert one(b + " open https://www.linkedin.com/in/me/") == {"cmd": "open", "url": "https://www.linkedin.com/in/me/"}
    assert one(b + " snapshot --dialog") == {"cmd": "snapshot", "part": "dialog"}
    assert one(b + ' type 13 --text "AI Agent Engineer"') == {"cmd": "type", "ref": "13", "text": "AI Agent Engineer"}
    assert one(b + " type 14 --stdin <<'EOF'\nline one; two | three\nline two\nEOF") == \
        {"cmd": "type", "ref": "14", "text": "line one; two | three\nline two"}
    assert one(b + ' select 15 "March"') == {"cmd": "select", "ref": "15", "option": "March"}
    assert one(b + ' report linkedin skills failed --note "no card"') == \
        {"cmd": "report", "site": "linkedin", "id": "skills", "state": "failed", "note": "no card"}
    assert one(b + " profile linkedin --stdin <<'JSON'\n{}\nJSON")["site"] == "linkedin"
    assert one(b + " screenshot 2>&1 | head -5") == {"cmd": "screenshot"}
    assert steps_of("git status") == [] and steps_of("python scripts/render.py ALL") == []
    # what Claude really writes: the command in a variable, several calls, loops, filters
    real = ("B='python E:/Forge/resume-studio/scripts/browser.py --data E:/Forge/resume-studio/resume.yaml'; "
            "for s in experience education; do $B open \"https://l.com/details/$s/\" | head -3; done; "
            "$B text | grep -i 'a|b'; $B type 13 --text \"a; b && c\" && $B screenshot")
    assert steps_of(real) == [{"cmd": "open", "url": "https://l.com/details/$s/", "loop": True}, {"cmd": "text"},
                              {"cmd": "type", "ref": "13", "text": "a; b && c"}, {"cmd": "screenshot"}]
    # older chats kept only the first 160 characters of a command: a cut-off call is left out
    assert steps_of("B='python E:/x/scripts/browser.py --data E:/x/r.yaml'; $B text | sed -n 1p; $B screensho") == [{"cmd": "text"}]
