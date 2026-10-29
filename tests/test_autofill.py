"""Autofill: the browser Resume Studio drives (core/browser.py, against tests/fake_sites in a headless
Edge), the profile a resume subset gives a site, the plan between that and what Claude read there, and
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

from autofill import profile, sync
from autofill.sites import PRESETS
from core.browser import profile_holder
from core.store import YamlStore

BROWSER_PY = ROOT / "scripts" / "browser.py"
SITES_DIR = Path(__file__).resolve().parent / "fake_sites"
LI = PRESETS["linkedin"]


def load(path: Path):
    return YamlStore(path).load()[0]


# ---- what a resume subset gives a site ----------------------------------------------------
def test_desired_profile_from_a_version(data_file):
    doc = load(data_file)
    all_v = next(v for v in doc["subsets"] if v["id"] == "ALL")
    want = profile.desired(doc, all_v, "en", LI)
    assert want["basics"] == {"headline": "MSc Computer Science student"}
    by_key = {i["key"]: i for i in want["items"]}
    assert by_key["EXP-001"]["section"] == "education"
    assert by_key["EXP-001"]["fields"]["org"] == "Harbour City University"
    assert by_key["EXP-001"]["fields"]["degree"] == "Master of Computer Science"
    assert by_key["EXP-001"]["fields"]["start"] == "2025-02"
    p2e = by_key["EXP-002"]
    assert p2e["section"] == "projects" and p2e["fields"]["end"] == "present"
    # the resume's markup is gone, one line per bullet
    assert p2e["fields"]["description"].splitlines()[1] == \
        "• Built a multi-stage LLM pipeline that turns lecture slides into practice quizzes"
    assert "notes" not in json.dumps(want) and "同组同学写的" not in json.dumps(want, ensure_ascii=False)   # fact layer stays out
    assert want["skills"][:3] == ["Python", "TypeScript", "Java"] and want["skills"].count("Next.js") == 1


def test_desired_follows_the_job_and_the_language(data_file):
    doc = load(data_file)
    job = next(v for v in doc["subsets"] if v["id"] == "RES-TECH-ZH")
    job["entries"] = ["EXP-003", "EXP-001"]
    want = profile.desired(doc, job, "zh", PRESETS["boss"])
    assert [i["key"] for i in want["items"]] == ["EXP-001", "EXP-003"]   # the job's section order: education first
    assert want["items"][0]["fields"]["org"] == "海港城市大学"
    assert want["basics"] == {} and want["skills"] == []                 # BOSS has no headline or skills list


SITE_NOW = {
    "url": "https://www.linkedin.com/in/alex/",
    "basics": {"headline": "Student at HCU", "about": ""},
    "items": [
        {"section": "education", "key": None, "fields": {"org": "The Harbour City University", "degree": "Master of Computer Science",
                                                       "start": "Feb 2025", "end": "Dec 2026",
                                                       "description": "Coursework: Machine Learning, Distributed Systems, Databases, Human-Computer Interaction"}},
        {"section": "projects", "key": None, "fields": {"name": "QuizLoom", "start": "2025-08", "end": "present",
                                                       "description": "Turns papers into exams"}},
        {"section": "projects", "key": None, "fields": {"name": "Old hackathon thing", "start": "2022-03"}},
    ],
    "skills": ["python", "React"],
}


# ---- the local copy and its sync with the site --------------------------------------------
def _p(*items, basics=None, skills=None):
    return {"basics": basics or {}, "skills": skills or [],
            "items": [{"section": s, "fields": f} for s, f in items]}


def test_sync_brings_site_changes_in_and_keeps_mine():
    first = sync.merge(None, None, _p(("experience", {"title": "Intern", "org": "CSIRO", "start": "2024-02", "description": "Flood maps."}),
                                      ("projects", {"name": "P2E", "description": "old"}),
                                      basics={"headline": "Student"}, skills=["Python", "Go"]))
    base, local = first["base"], first["local"]
    assert [i["id"] for i in local["items"]] == ["i1", "i2"] and sync.pending(base, local) == []
    # mine: the headline, P2E's description, a new award, Go off the skills
    local["basics"]["headline"] = "ML engineer"
    local["items"][1]["fields"]["description"] = "new"
    local["items"].append({"id": "i9", "section": "awards", "fields": {"title": "Prize"}})
    local["skills"].remove("Go")
    # the site's: the internship's description, the headline too, a skill, a new project; dates written its own way
    m = sync.merge(base, local, _p(("experience", {"title": "Intern", "org": "CSIRO", "start": "Feb 2024", "description": "Flood risk maps."}),
                                   ("projects", {"name": "P2E", "description": "old"}),
                                   ("projects", {"name": "Plant Disease Classifier", "description": "z"}),
                                   basics={"headline": "Student at HCU"}, skills=["Python", "Go", "Rust"]))
    assert m["conflicts"] == {"basics.headline": {"site": "Student at HCU", "local": "ML engineer"}}
    loc = {i["id"]: i for i in m["local"]["items"]}
    assert loc["i1"]["fields"]["description"] == "Flood risk maps."          # only the site changed it: in
    assert loc["i1"]["fields"]["start"] == "2024-02"                         # the same date written differently: no change
    assert loc["i2"]["fields"]["description"] == "new"                       # only I changed it: mine stays
    assert loc["i3"]["fields"]["name"] == "Plant Disease Classifier" and "i9" in loc          # new on the site: in; my new one stays
    assert m["local"]["skills"] == ["Python", "Rust"] and m["synced"] == 3
    pend = {p["id"]: p for p in sync.pending(m["base"], m["local"])}
    assert set(pend) == {"basics.headline", "item.i2", "item.i9", "skills"}
    assert pend["item.i9"]["op"] == "add" and pend["skills"]["remove"] == ["Go"] and pend["skills"]["add"] == []
    # pushed: the base moves on, the change is no longer pending
    b2 = sync.pushed(m["base"], m["local"], pend["item.i9"])
    assert "item.i9" not in {p["id"] for p in sync.pending(b2, m["local"])}
    # undone: the copy says what the site says again
    assert sync.revert(m["base"], m["local"], "item.i2")["items"][1]["fields"]["description"] == "old"


def test_sync_removals_and_items_added_on_both_sides():
    first = sync.merge(None, None, _p(("projects", {"name": "A", "description": "a"}), ("projects", {"name": "B", "description": "b"})))
    base, local = first["base"], first["local"]
    local["items"] = [i for i in local["items"] if i["fields"]["name"] != "A"]            # I removed A
    local["items"].append({"id": "i7", "section": "projects", "fields": {"name": "C", "description": "c"}})   # and added C
    # the site: B is gone, and C is there now (the user added it there too)
    m = sync.merge(base, local, _p(("projects", {"name": "A", "description": "a"}), ("projects", {"name": "C", "description": "c"})))
    names = [i["fields"]["name"] for i in m["local"]["items"]]
    assert names == ["C"]                                       # B: gone there, untouched here: gone; A: still removed here
    assert [p["op"] for p in sync.pending(m["base"], m["local"])] == ["remove"]   # only A's removal waits
    # gone from the site but edited here: kept, with a note
    first = sync.merge(None, None, _p(("projects", {"name": "A", "description": "a"})))
    first["local"]["items"][0]["fields"]["description"] = "mine"
    m = sync.merge(first["base"], first["local"], _p())
    assert m["local"]["items"][0]["fields"]["description"] == "mine" and m["notes"][0]["kind"] == "removed_there_changed_here"


def test_a_read_covers_only_its_sections():
    first = sync.merge(None, None, _p(("experience", {"title": "Intern", "org": "CSIRO"}), ("projects", {"name": "A"}), basics={"headline": "H"}))
    m = sync.merge(first["base"], first["local"], _p(("projects", {"name": "A"}), ("projects", {"name": "B"})), scope=["projects"])
    assert [i["section"] for i in m["local"]["items"]] == ["experience", "projects", "projects"]   # experience wasn't read: kept
    assert m["local"]["basics"] == {"headline": "H"}


def test_same_school_with_the_degree_worded_differently_is_one_item():
    old = [{"section": "education", "fields": {"org": "Harbour City University", "degree": "Master of Computer Science"}}]
    new = [{"section": "education", "fields": {"org": "The Harbour City University", "degree": "Master's degree, Computer Science"}}]
    assert sync.match(old, new) == {0: 0}   # not "removed one, added another"


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
def test_routes_from_a_read_to_edits_to_report(server, data_file):
    st, r = server.req("GET", "/api/autofill")
    assert st == 200 and [s["id"] for s in r["sites"]] == ["linkedin", "boss"]
    assert r["sites"][0]["changes"] is None and r["browser"]["state"] == "off"
    st, v = server.req("GET", "/api/autofill/linkedin")
    assert v["subset_id"] == "ALL" and v["lang"] == "en" and v["synced"] is False and not v["route"]["recorded"]
    assert server.req("GET", "/api/autofill/boss")[1]["lang"] == "zh"   # the site's usual language when the file has it
    st, v = server.req("PUT", "/api/autofill/linkedin", {"subset_id": "RES-TECH-EN", "lang": "zh"})
    assert st == 200 and v["subset_id"] == "RES-TECH-EN" and v["lang"] == "zh"
    assert server.req("PUT", "/api/autofill/linkedin", {"subset_id": "NOPE"})[0] == 404
    assert server.req("PUT", "/api/autofill/linkedin", {"lang": "fr"})[0] == 400
    server.req("PUT", "/api/autofill/linkedin", {"subset_id": "ALL", "lang": "en"})
    assert server.req("POST", "/api/autofill/linkedin/apply", {})[0] == 409   # nothing read yet
    assert server.req("PUT", "/api/autofill/linkedin/local", {"op": "set", "item": "basics", "field": "headline", "value": "x"})[0] == 400

    # Claude's reading: the first copy of the profile
    st, r = server.req("POST", "/api/autofill/linkedin/current", {"items": [{"section": "pets"}]})
    assert st == 400 and "items[0].section" in r["error"]
    st, r = server.req("POST", "/api/autofill/linkedin/current", SITE_NOW)
    assert st == 200 and "synced." in r["text"] and "projects: QuizLoom" in r["text"]
    v = server.req("GET", "/api/autofill/linkedin")[1]
    assert v["synced"] and v["synced_at"] and v["pending"] == [] and v["local"] == v["base"]
    assert [i["fields"].get("start") for i in v["local"]["items"]] == ["2025-02", "2025-08", "2022-03"]   # dates as YYYY-MM
    assert (data_file.parent / "resume-studio-data" / "autofill" / "linkedin.json").is_file()   # next to the data file
    # the resume, next to the copy: wording it differs in, entries and skills the copy lacks
    ref = v["reference"]
    p2e = next(i for i in v["local"]["items"] if i["fields"].get("name") == "QuizLoom")
    assert ref["items"][p2e["id"]]["key"] == "EXP-002" and "description" in ref["items"][p2e["id"]]["fields"]
    assert any(m["key"] == "EXP-003" for m in ref["missing"]) and "TypeScript" in ref["skills"]
    assert ref["basics"]["headline"] == "MSc Computer Science student"

    # the user's edits: each one a change to push
    def edit(**op):
        st, v = server.req("PUT", "/api/autofill/linkedin/local", op)
        assert st == 200, v
        return v
    edit(op="set", item="basics", field="headline", value="ML engineer in training")
    edit(op="set", item=p2e["id"], field="description", value=ref["items"][p2e["id"]]["fields"]["description"])
    old = next(i for i in v["local"]["items"] if i["fields"].get("name") == "Old hackathon thing")
    edit(op="remove", item=old["id"])
    v = edit(op="add", section="projects", fields=next(m for m in ref["missing"] if m["key"] == "EXP-003")["fields"])
    new_id = v["item"]
    v = edit(op="add_skills", skills=["TypeScript"])
    assert server.req("PUT", "/api/autofill/linkedin/local", {"op": "set", "item": p2e["id"], "field": "company", "value": "x"})[0] == 400
    assert server.req("PUT", "/api/autofill/linkedin/local", {"op": "remove", "item": "nope"})[0] == 404
    ops = {p["id"]: p["op"] for p in v["pending"]}
    assert ops == {"basics.headline": "update", f"item.{p2e['id']}": "update", f"item.{new_id}": "add",
                   f"item.{old['id']}": "remove", "skills": "update"}
    assert all(p["checked"] for p in v["pending"])
    # untick one, undo one
    v = server.req("PUT", "/api/autofill/linkedin", {"ticks": {"skills": False}})[1]
    assert not next(p for p in v["pending"] if p["id"] == "skills")["checked"]
    v = edit(op="revert", change=f"item.{old['id']}")
    assert f"item.{old['id']}" not in {p["id"] for p in v["pending"]}

    # reports from the push: done moves the site's side on
    assert server.req("POST", "/api/autofill/linkedin/report", {"id": "nope", "state": "done"})[0] == 404
    assert server.req("POST", "/api/autofill/linkedin/report", {"id": "basics.headline", "state": "great"})[0] == 400
    assert server.req("POST", "/api/autofill/linkedin/report", {"id": "basics.headline", "state": "done"})[0] == 200
    v = server.req("GET", "/api/autofill/linkedin")[1]
    assert v["base"]["basics"]["headline"] == "ML engineer in training"
    assert "basics.headline" not in {p["id"] for p in v["pending"]}
    assert server.req("GET", "/api/autofill/nowhere")[0] == 404

    # the site changed the same field meanwhile: a conflict, and pushing waits for it
    now = json.loads(json.dumps(SITE_NOW))
    now["basics"]["headline"] = "ML engineer in training"
    now["items"][1]["fields"]["description"] = "Turns lecture slides into quizzes"
    server.req("POST", "/api/autofill/linkedin/current", now)
    v = server.req("GET", "/api/autofill/linkedin")[1]
    key = f"{p2e['id']}.description"
    assert list(v["conflicts"]) == [key] and v["conflicts"][key]["site"] == "Turns lecture slides into quizzes"
    st, r = server.req("POST", "/api/autofill/linkedin/apply", {})
    assert st == 409 and "冲突" in r["error"]
    v = edit(op="settle", key=key, take="site")
    assert v["conflicts"] == {} and f"item.{p2e['id']}" not in {p["id"] for p in v["pending"]}


def test_over_the_limit_blocks_apply(server):
    server.req("POST", "/api/autofill/linkedin/current", SITE_NOW)
    server.req("PUT", "/api/autofill/linkedin/local", {"op": "set", "item": "basics", "field": "about", "value": "y" * 3000})
    v = server.req("GET", "/api/autofill/linkedin")[1]
    about = next(p for p in v["pending"] if p["id"] == "basics.about")
    assert about["fields"][0]["limit"] == 2600 and about["fields"][0]["over"]
    st, r = server.req("POST", "/api/autofill/linkedin/apply", {})
    assert st == 409 and "about" in r["error"]


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
    assert server.req("GET", "/api/autofill/linkedin")[1]["synced"]

    # any other site, by its address: every section until a read says what it has
    assert server.req("POST", "/api/autofill/sites", {"name": "x", "home": "not an address"})[0] == 400
    assert server.req("POST", "/api/autofill/sites", {"home": "https://a.com", "lang": "fr"})[0] == 400
    st, r = server.req("POST", "/api/autofill/sites", {"name": "SEEK", "home": "www.seek.com.au/profile/me", "lang": "en",
                                                       "notes": "My profile is under Profile → Career history."})
    seek = r["site"]
    assert seek["id"] == "seek" and seek["home"] == "https://www.seek.com.au/profile/me" and not seek["preset"]
    assert seek["sections"] == ["experience", "education", "projects", "awards", "skills"] and not seek["learned"]
    v = server.req("GET", "/api/autofill/seek")[1]
    assert v["lang"] == "en" and v["synced"] is False
    # a second site on the same domain gets an id of its own
    assert server.req("POST", "/api/autofill/sites", {"name": "SEEK 2", "home": "https://seek.com.au"})[1]["site"]["id"] == "seek-2"
    server.req("DELETE", "/api/autofill/seek-2")

    # the first read has to say what the site has; the copy then fits it
    now = {"url": "https://www.seek.com.au/profile/me", "basics": {"about": ""},
           "items": [{"section": "experience", "key": None, "fields": {"title": "Tutor", "org": "HCU", "start": "2026-02"}}]}
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
    assert v["site"]["limits"] == {"basics.about": 500} and v["local"]["items"][0]["fields"]["title"] == "Tutor"
    # later reads may leave `site` out
    assert server.req("POST", "/api/autofill/seek/current", now)[0] == 200

    # the user changes any site; an empty field goes back to the preset's own
    st, r = server.req("PUT", "/api/autofill/linkedin/settings", {"name": "LinkedIn (work)", "notes": "Skip the Featured card.", "index": 0})
    assert st == 200 and [s["id"] for s in r["sites"]] == ["linkedin", "boss", "seek"] and r["sites"][0]["name"] == "LinkedIn (work)"
    assert server.req("PUT", "/api/autofill/seek/settings", {"home": ""})[0] == 400   # a site of one's own needs its address
    server.req("PUT", "/api/autofill/linkedin/settings", {"name": ""})
    assert server.req("GET", "/api/autofill")[1]["sites"][0]["name"] == "LinkedIn"
    saved = json.loads((data_file.parent / "resume-studio-data" / "autofill" / "sites.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["sites"]] == ["linkedin", "boss", "seek"] and "name" not in saved["sites"][0]

    # what Claude is told: the user's notes, and (for a site of their own) to say what it has
    from autofill import prompts
    from autofill.service import Autofill
    from resume.service import Studio
    a = Autofill(Studio(data_file))
    li = a.site("linkedin")
    assert "Skip the Featured card." in prompts.learn_context(li, "d.yaml", None, "", False)
    assert "new to Resume Studio" not in prompts.learn_context(li, "d.yaml", None, "", False)
    ctx = prompts.learn_context(a.site("seek"), "d.yaml", None, "", True)
    assert "Profile → Career history." in ctx and '"site":' in ctx and "Last time it had" in ctx

    # every site can go, presets too: the page then asks which to add
    for sid in ("linkedin", "boss", "seek"):
        server.req("DELETE", "/api/autofill/" + sid)
    assert server.req("GET", "/api/autofill")[1]["sites"] == []


def run_steps(server, data_file, steps: dict):
    """What fake Claude runs when a message has these words (scripts/browser.py, as Bash)."""
    os.environ["FAKE_CLAUDE_RUN"] = json.dumps(steps)


# Chromium's blocked ports above 1024 (net/base/port_util.cc)
UNSAFE_PORTS = {1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6679,
                6697, 10080}


@pytest.fixture
def fake_site():
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(SITES_DIR))
    handler.log_message = lambda *a, **k: None
    while True:   # a random port can be one browsers refuse to open (ERR_UNSAFE_PORT: 6697 IRC, 5060 SIP, …)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        if srv.server_port not in UNSAFE_PORTS:
            break
        srv.server_close()
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
    (data_file.parent / "resume-studio-data" / ".cache").mkdir(parents=True, exist_ok=True)
    (data_file.parent / "resume-studio-data" / ".cache" / "server.json").write_text(json.dumps({"url": server.url}), encoding="utf-8")
    server.inst.browser.headless, server.inst.browser.pace = True, 0
    yield server
    os.environ.pop("FAKE_CLAUDE_RUN", None)


def cli(data_file, *args, stdin: str = "", chat: str | None = None) -> subprocess.CompletedProcess:
    """scripts/browser.py as a chat runs it (`chat`: the chat's id, which the app puts in its environment)."""
    env = {k: v for k, v in os.environ.items() if k != "RESUME_STUDIO_CHAT"}
    if chat:
        env["RESUME_STUDIO_CHAT"] = chat
    return subprocess.run([sys.executable, str(BROWSER_PY), "--data", str(data_file), *args], input=stdin.encode("utf-8"),
                          capture_output=True, timeout=120, env=env)


def out(p) -> str:
    return (p.stdout + p.stderr).decode("utf-8", errors="replace")


def ref_of(text: str, needle: str) -> str:
    line = next(l for l in text.splitlines() if needle in l)
    return line.split("]")[0].strip(" [")


def test_a_posting_is_read_in_a_tab_of_its_own(live, data_file, fake_site):
    """jobhunt/reader.py on a page drawn by its script after loading: its JD, where and until when; the tab the
    user (or Claude) works in is left where it was, and the reading tab is gone afterwards."""
    from jobhunt.reader import read_posting
    b = live.inst.browser
    b.open(fake_site + "/linkedin/profile.html")
    got = read_posting(b, fake_site + "/jobs/posting.html")
    assert got["jd"].startswith("岗位职责") and "任职要求" in got["jd"] and got["jd"].rstrip().endswith("项目经验者优先。")
    assert "立即投递" not in got["jd"] and "首页" not in got["jd"] and "工作地点" not in got["jd"]
    assert [p["area"] for p in got["places"]] == ["Hangzhou", "Shanghai"] and got["deadline"] == "2026-11-30"
    assert not any("/jobs/posting.html" in x["url"] for x in b.tabs()) and b.here()["url"].endswith("/linkedin/profile.html")
    assert live.req("GET", "/api/browser")[1]["url"].endswith("/linkedin/profile.html")


def test_a_browser_work_started_closes_itself_when_the_work_is_done(live, fake_site):
    """A browser a scan, a check or Claude started closes once nothing uses it; one the user opened stays."""
    b = live.inst.browser
    busy = {"on": False}
    b.busy = lambda: busy["on"]
    try:
        b.stop()
        assert b.ensure() is True and b.ensure() is False          # the first call started it; the second found it running
        assert b.status()["owner"] == "work"
        with b.hold():                                             # a scan reading: never closed under it
            b.read_page(fake_site + "/jobs/posting.html", "async (h) => 1")
            assert not b.close_if_idle(after=0)
        assert b.close_if_idle(after=0) and b.status()["state"] == "off"
        # Claude opened a page during a reply: it stays while a chat replies, and closes after (the page was Claude's)
        b.open(fake_site + "/linkedin/profile.html")
        busy["on"] = True
        assert not b.close_if_idle(after=0) and b.status()["state"] == "running"
        busy["on"] = False
        assert not b.close_if_idle(after=30) and not b.status()["closing"]   # the wait counts from the end of the reply
        b.last_used -= 20                                          # the last seconds: the window may keep it open
        assert not b.close_if_idle(after=30) and b.status()["closing"]
        assert live.req("POST", "/api/browser/keep", {})[1]["owner"] == "user"
        assert not b.close_if_idle(after=0) and not b.status()["closing"] and b.status()["state"] == "running"
        b.stop()
        b.open(fake_site + "/linkedin/profile.html")
        assert b.close_if_idle(after=0) and b.status()["state"] == "off"
        # the user opened it (or Claude showed it to them): it stays until they close it
        live.req("POST", "/api/browser/start", {})
        assert b.status()["owner"] == "user" and not b.close_if_idle(after=0) and b.status()["state"] == "running"
        # and it closes by itself, with no one calling: the watcher started with it
        b.stop()
        b.idle_close = 0.4
        b.ensure()
        deadline = time.time() + 15
        while b.status()["state"] != "off" and time.time() < deadline:
            time.sleep(0.2)
        assert b.status()["state"] == "off"
    finally:
        b.idle_close = 60
        b.busy = lambda: bool(live.inst.chats.running())


def test_browser_command_drives_a_real_page(live, data_file, fake_site):
    """scripts/browser.py → the window → CDP → a headless Edge with its own profile."""
    r = cli(data_file, "open", fake_site + "/linkedin/profile.html")
    assert r.returncode == 0, out(r)
    assert "ok: Alex Chen | Profile" in out(r)
    snap = out(cli(data_file, "snapshot"))
    # the whole document, also below the fold
    assert "## Education" in snap and "Harbour City University" in snap and "[1] button \"Edit intro\"" in snap
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
    assert Path(path).is_file() and Path(path).is_relative_to(data_file.parent / "resume-studio-data" / ".cache" / "browser")
    assert "only files in" in out(cli(data_file, "upload", "1", str(ROOT / "README.md")))
    assert "not a text field" in out(cli(data_file, "type", "1", "--text", "x"))
    assert "is not on the page" in out(cli(data_file, "click", "999"))
    # buttons that show only while the mouse is over their row (BOSS直聘's 编辑 / 删除) are listed, and click reaches them
    edit = next(l for l in snap.splitlines() if "编辑" in l)
    assert "(shows on hover)" in edit and "删除" in snap
    r = cli(data_file, "click", ref_of(snap, '"编辑"'))
    assert r.returncode == 0 and 'target: link "编辑"' in out(r), out(r)
    assert live.inst.browser._eval_page("document.getElementById('edu-edit').textContent") == "编辑 ✓"
    assert cli(data_file, "hover", ref_of(snap, '"Edit intro"')).returncode == 0
    assert cli(data_file, "press", "Escape").returncode == 0
    # its own profile, inside the app's folder (never the user's everyday browser profile)
    prof = data_file.parent / "local" / "browser" / "msedge"
    assert (prof / "DevToolsActivePort").is_file()
    st, b = live.req("GET", "/api/browser")
    assert b["state"] == "running" and b["browser"] == "Microsoft Edge" and b["url"].endswith("/linkedin/profile.html")
    live.req("POST", "/api/browser/stop", {})
    assert live.req("GET", "/api/browser")[1]["state"] == "off"


def test_a_page_busy_for_longer_than_a_command_waits_still_answers(live, data_file, fake_site):
    """BOSS直聘's home page keeps its main thread busy for seconds at a time while it loads; a command
    sent meanwhile waits for the page instead of timing out (the push used to fail on every item)."""
    cli(data_file, "open", fake_site + "/jobs/busy.html")
    snap = out(cli(data_file, "snapshot"))
    r = cli(data_file, "click", ref_of(snap, '"Get busy"'))
    assert r.returncode == 0, out(r)
    time.sleep(0.5)                                  # its script now runs for 33 s; CDP calls give up after 30
    r = cli(data_file, "snapshot")
    assert r.returncode == 0 and "done" in out(r), out(r)


def test_two_chats_driving_at_once_keep_to_their_own_tabs(live, data_file, fake_site):
    """A scan chat reading a careers site while another chat pushes a profile (2026-10-30: the scan's `open` took
    the tab the push was clicking in, and the push's next snapshot landed on the careers page). Each chat works in
    a tab of its own, chosen by the chat id scripts/browser.py sends; so does a check for updates."""
    b = live.inst.browser
    profile_url, posting_url = fake_site + "/linkedin/profile.html", fake_site + "/jobs/posting.html"

    def drive(chat: str, url: str, title: str, rounds: int, seen: list):
        assert cli(data_file, "open", url, chat=chat).returncode == 0
        for _ in range(rounds):
            r = out(cli(data_file, "snapshot", "--max", "300", chat=chat))
            seen.append(r.splitlines()[0])
            if chat == "push":                           # and it clicks on its page meanwhile
                snap = out(cli(data_file, "snapshot", chat=chat))
                r = cli(data_file, "click", ref_of(snap, '"Edit intro"'), chat=chat)
                assert r.returncode == 0, out(r)
                cli(data_file, "press", "Escape", chat=chat)
            else:
                cli(data_file, "open", url, chat=chat)    # a scan goes from page to page

    # one after the other, interleaved: the scan's open comes between the push's open and its snapshot
    assert cli(data_file, "open", profile_url, chat="push").returncode == 0
    assert "星河科技" in out(cli(data_file, "open", posting_url, chat="scan"))
    assert out(cli(data_file, "snapshot", "--max", "300", chat="push")).startswith("Alex Chen | Profile — " + profile_url)
    assert out(cli(data_file, "snapshot", "--max", "300", chat="scan")).startswith("星河科技校园招聘 — " + posting_url)
    # and at the same time, with a check for updates by a route running too
    seen: dict[str, list] = {"push": [], "scan": []}
    errors: list = []

    def run(fn, *a):
        try:
            fn(*a)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    checked: list = []

    def check():
        with b.acting("check:linkedin"):
            for _ in range(3):
                checked.append(b.open(profile_url)["url"])
                checked.append(b.run_script("async (h) => location.href"))

    threads = [threading.Thread(target=run, args=(drive, "push", profile_url, "Alex Chen", 3, seen["push"])),
               threading.Thread(target=run, args=(drive, "scan", posting_url, "星河科技", 3, seen["scan"])),
               threading.Thread(target=run, args=(check,))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(180)
    assert not errors, errors
    assert seen["push"] and all(s == "Alex Chen | Profile — " + profile_url for s in seen["push"]), seen["push"]
    assert seen["scan"] and all(s == "星河科技校园招聘 — " + posting_url for s in seen["scan"]), seen["scan"]
    assert checked and all(u == profile_url for u in checked), checked
    # the user still sees and can use every tab; a chat can't take a tab another chat is working in
    tabs = out(cli(data_file, "tabs"))
    assert profile_url in tabs and posting_url in tabs
    scan_tab = next(int(l.split(":")[0].strip(" *")) for l in tabs.splitlines() if posting_url in l)
    b.active = lambda key: key == "scan"                 # the scan chat is still replying
    try:
        assert "is using tab" in out(cli(data_file, "tab", str(scan_tab), chat="push"))
    finally:
        b.active = live.inst.chats.busy
    # the window shows the tab last worked in
    assert live.req("GET", "/api/browser")[1]["url"] in (profile_url, posting_url)


def test_a_chat_carries_its_id_to_the_browser_command(server, fake_claude):
    """The agent's process has the chat's id in its environment, so scripts/browser.py can send it."""
    chats = server.inst.chats
    chat = chats.store.create(title="t")
    chats.send(chat["id"], "hi")
    server.wait_idle(chat["id"])
    assert calls(fake_claude)[-1]["env"].get("RESUME_STUDIO_CHAT") == chat["id"]


@pytest.fixture
def risk_site():
    """The fake sites, and what each page's script reported back (`seen?webdriver=…`)."""
    seen: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(SITES_DIR), **k)

        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            if "/seen?" in self.path:
                seen.append(self.path.split("?", 1)[1])
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def wait_for(cond, what: str, timeout: float = 20.0):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def test_sign_in_mode_opens_the_same_profile_without_remote_control(live, data_file, risk_site):
    """A site that drops sign-ins from a driven browser (Xinghe's code never came): sign-in mode is the same
    profile with no debugging port, so the page sees an ordinary browser; switching back drives it again."""
    base, seen = risk_site
    page = base + "/login/risk.html"
    assert cli(data_file, "open", page).returncode == 0
    wait_for(lambda: "webdriver=true" in seen, "the driven page")   # driven: the site can tell
    r = cli(data_file, "sign-in", page)
    assert r.returncode == 0 and "without remote control" in out(r), out(r)
    wait_for(lambda: "webdriver=false" in seen, "the plain page")  # the same page, now an ordinary browser
    st = live.req("GET", "/api/browser")[1]
    assert st["state"] == "signin" and st["signin_url"] == page
    prof = data_file.parent / "local" / "browser" / "msedge"
    assert not (prof / "DevToolsActivePort").exists() or live.inst.browser._live_port(prof / "DevToolsActivePort") is None
    # nothing drives it meanwhile: commands are refused with what to do
    r = cli(data_file, "snapshot")
    assert r.returncode != 0 and "switch back" in out(r)
    # switching back: the controlled browser again, at the page signed in to
    seen.clear()
    st = live.req("POST", "/api/browser/sign-in/done", {})[1]
    assert st["state"] == "running" and st["url"] == page and st["signin_url"] == ""
    wait_for(lambda: "webdriver=true" in seen, "the page driven again")
    assert "Sign in" in out(cli(data_file, "snapshot"))
    # the user closing the sign-in window themselves just turns the browser off
    live.req("POST", "/api/browser/sign-in", {"url": page})
    holder = wait_for(lambda: profile_holder(prof), "the sign-in browser")   # (Edge may hand over to another process)
    subprocess.run(["taskkill", "/F", "/PID", str(holder)], capture_output=True)
    wait_for(lambda: live.req("GET", "/api/browser")[1]["state"] == "off", "the browser to be off")
    assert cli(data_file, "open", page).returncode == 0   # and the next command starts it as usual
    assert live.req("POST", "/api/browser/sign-in", {"url": "file:///C:/x"})[0] == 409
    live.req("POST", "/api/browser/stop", {})


FAKE_ROUTE_SCRIPT = """async (h) => ({
  basics: { headline: h.text(h.one('#headline')) },
  items: [
    ...h.all('li', h.section('Experience')).map((li) => ({ section: 'experience', fields: {
      title: h.text(h.one('b', li)), org: h.text(h.one('.co', li)),
      start: h.text(h.one('.dt', li)).split('–')[0].trim(), description: h.text(h.one('.ds', li)) } })),
    ...h.all('li', h.section('Education')).map((li) => ({ section: 'education', fields: {
      org: h.text(li.children[0]), degree: h.text(li.children[1]) } })),
  ],
  skills: h.all('.pill', h.section('Skills')).map((e) => h.text(e)),
})"""


def test_claude_records_a_route_then_checks_need_no_claude(live, data_file, fake_claude, fake_site):
    b = [sys.executable, str(BROWSER_PY), "--data", str(data_file)]
    page = PRESETS["linkedin"].home
    route_json = json.dumps({"pages": [{"url": page, "reads": ["basics", "experience", "education", "skills"], "script": FAKE_ROUTE_SCRIPT}]})
    run_steps(live, data_file, {
        "记录": [{"argv": b + ["open", page]}, {"argv": b + ["extract", "--stdin"], "stdin": FAKE_ROUTE_SCRIPT},
                 {"argv": b + ["route", "linkedin", "--stdin"], "stdin": route_json}, {"argv": b + ["sync", "linkedin"]}],
    })
    # the first check: Claude reads the site and records the route
    st, r = live.req("POST", "/api/autofill/linkedin/check", {})
    assert st == 200 and r["mode"] == "claude"
    chat = r["chat"]
    assert not chat.get("workspace") and chat["site"] == "linkedin"   # a chat in the data folder, like any other (one chat)
    live.wait_idle(chat["id"], timeout=90)
    first = calls(fake_claude)[-1]
    assert "record" in first["stdin"].lower() and "never by generated class names" in first["stdin"]
    ctx = Path(first["argv"][first["argv"].index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    assert "scripts" in ctx and "browser.py" in ctx and "hunt.py" in ctx and "Resume data file" in ctx   # the browser, the tracker, the resume
    st, ch = live.req("GET", "/api/chats/" + chat["id"])
    assert "never by generated" not in ch["messages"][0]["text"]   # the chat shows only the short message
    runs = calls(Path(str(fake_claude) + ".run"))
    assert all(x["code"] == 0 for x in runs), runs
    extracted = json.loads(runs[1]["out"])
    assert extracted["basics"] == {"headline": "Student at HCU"} and extracted["skills"] == ["Python"]
    assert "Running it read this" in runs[2]["out"] and "CSIRO" in runs[2]["out"]
    assert "synced." in runs[3]["out"]
    v = live.req("GET", "/api/autofill/linkedin")[1]
    assert v["route"]["recorded"] and v["synced"] and v["last_check"]["mode"] == "route"
    loc = v["local"]
    intern = next(i for i in loc["items"] if i["section"] == "experience")
    assert intern["fields"] == {"title": "Research Intern", "org": "CSIRO", "start": "2024-02", "description": "Mapped flood risk."}
    assert [i["section"] for i in loc["items"]] == ["experience", "education"] and loc["skills"] == ["Python"]

    # the user edits the copy here, and someone edits the site: "check for updates" runs the route, no Claude
    live.req("PUT", "/api/autofill/linkedin/local", {"op": "set", "item": "basics", "field": "headline", "value": "ML engineer in training"})
    live.inst.browser._eval_page("DB.positions[0].desc = 'Mapped flood risk for 3 councils.'; paint(); 1")
    n_claude = len(calls(fake_claude))
    st, r = live.req("POST", "/api/autofill/linkedin/check", {"wait": True})
    assert st == 200 and r["mode"] == "route" and r["ok"] and r["synced"] == 1, r
    assert len(calls(fake_claude)) == n_claude   # no Claude
    v = live.req("GET", "/api/autofill/linkedin")[1]
    intern = next(i for i in v["local"]["items"] if i["section"] == "experience")
    assert intern["fields"]["description"] == "Mapped flood risk for 3 councils."
    assert [p["id"] for p in v["pending"]] == ["basics.headline"]   # mine still waits to go up

    # push: Claude gets exactly the ticked changes, then the route checks the site
    run_steps(live, data_file, {"逐条": [{"argv": b + ["report", "linkedin", "basics.headline", "doing"]},
                                       {"argv": b + ["report", "linkedin", "basics.headline", "done"]}]})
    t0 = time.time()
    st, r = live.req("POST", "/api/autofill/linkedin/apply", {})
    assert st == 200, r
    live.wait_idle(r["chat"]["id"], timeout=60)
    last = calls(fake_claude)[-1]
    approved = json.loads(last["stdin"][last["stdin"].index("## The approved changes"):].split("```")[1])
    assert approved == [{"id": "basics.headline", "op": "update", "section": "basics", "now_on_site": "headline",
                         "set": {"headline": "ML engineer in training"}}]
    deadline = time.time() + 30
    while time.time() < deadline and not (live.req("GET", "/api/autofill/linkedin")[1].get("last_check") or {}).get("at", 0) > t0:
        time.sleep(0.3)
    v = live.req("GET", "/api/autofill/linkedin")[1]
    assert v["applying"] == [] and v["pending"] == []
    # fake Claude didn't really type it, so the check after the push shows the site still has the old one:
    # that is a site-side change against what was reported, not a silent loss of the user's text
    assert v["local"]["basics"]["headline"] == "Student at HCU"

    # a redesign: the route stops reading, and the window offers to let Claude record it anew
    live.inst.browser._eval_page("localStorage.setItem('exp-heading', 'Work'); 1")
    st, r = live.req("POST", "/api/autofill/linkedin/check", {"wait": True})
    assert st == 200 and not r["ok"] and ("experience came back empty" in r["error"] or "the script stopped" in r["error"]), r
    v = live.req("GET", "/api/autofill/linkedin")[1]
    assert v["route"]["broken"] and v["local"]["items"][0]["section"] == "experience"   # the copy is untouched
    st, r = live.req("POST", "/api/autofill/linkedin/learn", {})
    assert st == 200 and r["mode"] == "claude"
    live.wait_idle(r["chat"]["id"], timeout=60)
    assert "stopped working" in calls(fake_claude)[-1]["stdin"] and json.dumps(FAKE_ROUTE_SCRIPT)[1:40] in calls(fake_claude)[-1]["stdin"]
    st, lst = live.req("GET", "/api/chats")
    assert len([c for c in lst["chats"] if c.get("site") == "linkedin"]) == 3


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
    edu["title"], edu["subtitle"] = edu["subtitle"], edu["title"]   # "Master of Computer Science" on top, the school under it
    want = profile.desired(doc, next(v for v in doc["subsets"] if v["id"] == "ALL"), "en", LI)
    by_key = {i["key"]: i for i in want["items"]}
    assert by_key["EXP-001"]["fields"]["org"] == "Harbour City University"
    assert by_key["EXP-001"]["fields"]["degree"] == "Master of Computer Science"
    # LinkedIn's projects have no role field: it leads the description instead of disappearing
    p2e = by_key["EXP-002"]["fields"]
    assert "role" not in p2e and p2e["description"].startswith("Solo developer\n• Built")
    assert by_key["EXP-002"]["label"] == "QuizLoom"
    boss = profile.desired(doc, next(v for v in doc["subsets"] if v["id"] == "ALL"), "zh", PRESETS["boss"])
    assert next(i for i in boss["items"] if i["key"] == "EXP-002")["fields"]["role"] == "独立开发"
    _, errs = profile.validate_current({"items": [{"section": "projects", "fields": {"role": "x"}}]}, LI)
    assert errs and "not known for projects on LinkedIn" in errs[0]


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


def test_a_copy_read_by_an_older_version_counts_as_read(server, data_file):
    # older versions kept the time of Claude's first read as read_at: the table and the page must agree it was read
    server.req("POST", "/api/autofill/linkedin/current", SITE_NOW)
    f = data_file.parent / "resume-studio-data" / "autofill" / "linkedin.json"
    st = json.loads(f.read_text(encoding="utf-8"))
    st["read_at"] = st.pop("synced_at")
    st.pop("last_check", None)
    f.write_text(json.dumps(st), encoding="utf-8")
    row = next(s for s in server.req("GET", "/api/autofill")[1]["sites"] if s["id"] == "linkedin")
    v = server.req("GET", "/api/autofill/linkedin")[1]
    assert v["synced"] and v["synced_at"] == st["read_at"] and row["synced_at"] == st["read_at"]