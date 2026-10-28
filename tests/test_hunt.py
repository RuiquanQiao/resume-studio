"""The job-hunt tracker (hunt/, scripts/hunt.py): companies, applications, the events ledger, sending."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from core.i18n import Prefs, t
from jobhunt.store import Hunt, SameCompany, event_title, state_of

HUNT_PY = ROOT / "scripts" / "hunt.py"
FIXTURE = Path(__file__).parent / "fixtures" / "career-ops"


@pytest.fixture
def hunt(tmp_path: Path) -> Hunt:
    return Hunt(tmp_path / "resume-studio-data")


# ---------------------------------------------------------------- the store
def test_companies_and_roles_by_name_alias_or_id(hunt):
    c = hunt.add_company({"name": "星河科技", "aliases": "Xinghe, Nebula", "site": "careers.xinghe.example/campus"})
    assert c["id"] == "C1" and c["site"] == "https://careers.xinghe.example/campus" and c["aliases"] == ["Xinghe", "Nebula"]
    assert hunt.company("xinghe")["id"] == hunt.company("星河科技")["id"] == hunt.company("C1")["id"] == "C1"
    with pytest.raises(ValueError):
        hunt.add_company({"name": " 星河科技 "})                        # already on the list
    hunt.add_company({"name": "字节跳动"})
    a1 = hunt.add_application("Xinghe", {"role": "服务端研发工程师"})
    a2 = hunt.add_application("Xinghe", {"role": "算法工程师"})
    a3 = hunt.add_application("Xinghe", {"role": "数据分析", "order": 1})
    assert [a["order"] for a in (a1, a2, a3)] == [1, 2, 1]
    order = {a["role"]: a["order"] for a in hunt.applications("Xinghe")}
    assert order == {"数据分析": 1, "服务端研发工程师": 2, "算法工程师": 3}   # the 志愿 order, as asked
    assert hunt.application("xinghe/服务端")["id"] == a1["id"]                # a unique part of the role is enough
    assert hunt.application("A2")["role"] == "算法工程师"
    with pytest.raises(KeyError, match=r"\(A1\).*\(A2\)"):
        hunt.application("Xinghe/工程师")                                     # two roles match: say which
    with pytest.raises(ValueError):
        hunt.add_application("Xinghe", {"role": "算法工程师"})                # already there
    hunt.delete_application(a3["id"])
    assert {a["role"]: a["order"] for a in hunt.applications("Xinghe")} == {"服务端研发工程师": 1, "算法工程师": 2}
    assert hunt.add_application("Xinghe", {"role": "测试"})["id"] == "A4"    # ids are never reused


def test_a_read_while_a_write_replaces_the_file_never_sees_an_empty_list(hunt):
    import threading
    hunt.add_company({"name": "星河科技"})
    stop = threading.Event()

    def write():
        while not stop.is_set():
            hunt.update_company("C1", {"note": "x"}, source="auto")
    w = threading.Thread(target=write)
    w.start()
    try:
        empty = sum(not hunt.companies() for _ in range(2000))   # on Windows a few of these came back empty
    finally:
        stop.set()
        w.join()
    assert empty == 0


def test_a_company_added_again_is_caught_by_its_name_an_alias_or_a_close_name(hunt):
    c = hunt.add_company({"name": "字节跳动", "aliases": ["ByteDance"]})
    for again in ("字节 跳动", "bytedance", "Byte-Dance"):                   # its name or an alias: refused
        with pytest.raises(SameCompany) as e:
            hunt.add_company({"name": again, "different": True})       # even when told it's another
        assert e.value.same["id"] == c["id"]
    for close in ("字节", "字节跳动有限公司", "ByteDance Ltd"):                # close: asks, unless told it's another
        with pytest.raises(SameCompany, match="--different") as e:
            hunt.add_company({"name": close})
        assert e.value.same is None and [x["id"] for x in e.value.close] == [c["id"]]
    assert hunt.add_company({"name": "字节跳动有限公司", "different": True})["id"] == "C2"
    hunt.add_company({"name": "麦当劳", "aliases": ["McDonald's", "マクドナルド"]})
    for region in ("麦当劳中国", "McDonald's China", "McDonald's Australia Pty Ltd", "McDonald's Deutschland",
                   "日本マクドナルド", "McDonald's España", "McDonald's APAC"):   # an arm in any country is a company of its own
        assert hunt.add_company({"name": region})["name"] == region
    hunt.add_company({"name": "ブリヂストン"})
    with pytest.raises(SameCompany):
        hunt.add_company({"name": "ブリヂストンタイヤ"})                    # タイ (Thailand) inside タイヤ is no region
    with pytest.raises(SameCompany) as e:
        hunt.add_company({"name": "麦当劳（中国）有限公司"})                   # close to the arm, not to 麦当劳
    assert [x["name"] for x in e.value.close] == ["麦当劳中国"]
    hunt.add_company({"name": "美团"})
    with pytest.raises(ValueError, match="C1"):
        hunt.update_company("美团", {"name": "ByteDance"})              # renamed to another company's name


def test_statuses_take_the_users_words_and_land_in_the_ledger(hunt):
    assert [state_of(w) for w in ("一面", "笔试", "挂", "OA", "已投递", "Interview")] == \
           ["interview", "assessment", "rejected", "assessment", "applied", "interview"]
    with pytest.raises(ValueError, match="saved"):
        state_of("maybe")
    hunt.add_company({"name": "Xinghe"})
    a = hunt.add_application("Xinghe", {"role": "后端"})
    assert a["status"] == "saved" and not a["applied"]
    hunt.set_status(a["id"], "已投递", on="2026-10-10")
    hunt.set_status(a["id"], "一面", note="一面 周三 14:00\n问了\tRedis")
    hunt.set_status(a["id"], "interview")                                # the same again, no note: no line
    hunt.add_note(a["id"], "二面约在下周二")
    a = hunt.application(a["id"])
    assert a["status"] == "interview" and a["applied"] == "2026-10-10"
    evs = hunt.events(a["id"])
    # by when it happened: the status dated back to 10-10 comes before today's "added"
    assert [(e["kind"], e["from"], e["to"]) for e in evs] == [("status", "saved", "applied"), ("created", "", "saved"),
                                                              ("status", "applied", "interview"), ("note", "", "")]
    assert evs[0]["at"].startswith("2026-10-10") and evs[2]["note"] == "一面 周三 14:00\n问了 Redis"   # lines survive; a tab never splits a cell
    with pytest.raises(ValueError, match="2026-10-25"):
        hunt.update_application(a["id"], {"apply_by": "next tuesday"})


def test_interviews_are_booked_moved_and_dropped(hunt):
    hunt.add_company({"name": "Xinghe"})
    a = hunt.add_application("Xinghe", {"role": "后端"})
    assert a["schedule"] == []                                                      # nothing known, nothing booked
    e2 = hunt.add_booked(a["id"], {"title": "二面", "at": "2026/10/28 9:30", "minutes": "45", "where": "腾讯会议 123"})
    e1 = hunt.add_booked(a["id"], {"title": "一面", "at": "2026-10-25"})
    assert (e1["id"], e2["id"]) == ("E2", "E1") and e2["at"] == "2026-10-28 09:30" and e2["minutes"] == 45
    # the usual rounds are built in: no title of their own, so they read in the interface's language
    assert [(e.get("kind"), e["title"]) for e in hunt.application(a["id"])["schedule"]] == [("round1", ""), ("round2", "")]   # by date
    assert event_title(e1) == t("1st interview")
    custom = hunt.add_booked(a["id"], {"title": "宣讲会", "at": "2026-10-29"})
    assert "kind" not in custom and custom["title"] == "宣讲会"                    # the user's own stays as written
    hunt.delete_booked(a["id"], custom["id"])
    with pytest.raises(ValueError):
        hunt.add_booked(a["id"], {"title": "三面", "at": "next week"})
    with pytest.raises(ValueError):
        hunt.add_booked(a["id"], {"title": "", "at": "2026-10-30"})
    hunt.update_booked(a["id"], "二面", {"at": "2026-10-24 15:00"})                  # by its name too, in any language
    assert [e["kind"] for e in hunt.application(a["id"])["schedule"]] == ["round2", "round1"]
    hunt.update_booked(a["id"], "E1", {"title": t("2nd interview")})                # its own name shown back: still built in
    assert hunt.application(a["id"])["schedule"][0]["title"] == ""
    hunt.update_booked(a["id"], "E1", {"title": "二面（加面）"})                     # renamed: the user's words now
    assert hunt.application(a["id"])["schedule"][0]["title"] == "二面（加面）"
    hunt.delete_booked(a["id"], "E2")
    assert [e["id"] for e in hunt.application(a["id"])["schedule"]] == ["E1"]
    with pytest.raises(KeyError):
        hunt.delete_booked(a["id"], "E9")


def test_a_next_step_from_before_becomes_booked_or_a_note(hunt):
    hunt.add_company({"name": "Xinghe"})
    a1 = hunt.add_application("Xinghe", {"role": "后端"})
    a2 = hunt.add_application("Xinghe", {"role": "前端", "note": "学长内推"})
    data = hunt._apps()
    for a in data["applications"]:
        a.update({"next": "等二面", "due": "2026-10-25"} if a["id"] == a1["id"] else {"next": "问 HR", "due": ""})
    hunt._write("applications.json", data)
    hunt.migrate()
    x1, x2 = hunt.application(a1["id"]), hunt.application(a2["id"])
    assert "next" not in x1 and "due" not in x2
    assert [(e["id"], e["title"], e["at"]) for e in x1["schedule"]] == [("E1", "等二面", "2026-10-25")]
    assert x2["note"] == "学长内推\n问 HR"


def test_when_to_apply_by_is_only_what_the_posting_says(hunt):
    hunt.add_company({"name": "Xinghe"})
    a = hunt.add_application("Xinghe", {"role": "后端"})
    # nothing said, nothing made up: no apply event, no date, no 招满即止 (docs/JOB_HUNT.md, 2026-10-26)
    assert a["schedule"] == []
    assert "deadline" not in a and "apply_by" not in a                            # an event, never a field
    hunt.add_booked(a["id"], {"title": "一面", "at": "2026-10-25"})
    a = hunt.update_application(a["id"], {"apply_by": "2026-10-31"})
    assert [(e.get("kind"), e["title"], e["at"], e.get("rolling")) for e in a["schedule"]] == \
        [("round1", "", "2026-10-25", None), ("apply", "", "2026-10-31", False)]
    # 招满即止 only when someone says so, in any of its words
    for word in ("rolling", "招满即止", "滚动招聘"):
        hunt.update_application(a["id"], {"apply_by": "2026-10-31"})
        due = next(e for e in hunt.update_application(a["id"], {"apply_by": word})["schedule"] if e.get("kind") == "apply")
        assert due["at"] == "" and due["rolling"] is True
    # "" is nothing known: the event goes
    assert not any(e.get("kind") == "apply" for e in hunt.update_application(a["id"], {"apply_by": ""})["schedule"])
    # the event itself: a date ends 招满即止; 招满即止 clears the date; a dateless one the user made is "not known"
    e = hunt.add_booked(a["id"], {"kind": "apply"})
    assert (e["at"], e["rolling"]) == ("", False) and event_title(e) == t("Apply by")
    hunt.update_booked(a["id"], e["id"], {"rolling": True})
    hunt.update_booked(a["id"], e["id"], {"at": "2026-11-05 18:00", "rolling": False})
    due = next(x for x in hunt.application(a["id"])["schedule"] if x.get("kind") == "apply")
    assert (due["at"], due["rolling"]) == ("2026-11-05 18:00", False)
    hunt.update_booked(a["id"], e["id"], {"at": "招满即止"})
    due = next(x for x in hunt.application(a["id"])["schedule"] if x.get("kind") == "apply")
    assert (due["at"], due["rolling"]) == ("", True)
    with pytest.raises(ValueError):
        hunt.add_booked(a["id"], {"kind": "apply"})                               # one per role
    with pytest.raises(ValueError):
        hunt.update_booked(a["id"], "一面", {"at": ""})                            # an interview still needs its date


def test_booking_a_round_moves_the_status_forward_only(hunt):
    from jobhunt.store import booked_stage
    assert [booked_stage(x) for x in ("一面", "HR 面", "Final interview", "笔试", "测评", "OA", "Board meeting", "宣讲会")] == \
        ["interview", "interview", "interview", "assessment", "assessment", "assessment", None, None]
    hunt.add_company({"name": "Xinghe"})
    a = hunt.add_application("Xinghe", {"role": "后端"})
    hunt.add_booked(a["id"], {"title": "宣讲会", "at": "2026-10-20"})            # says nothing: stays
    assert hunt.application(a["id"])["status"] == "saved"
    hunt.add_booked(a["id"], {"title": "笔试", "at": "2026-10-22"}, source="chat")
    a = hunt.application(a["id"])
    assert a["status"] == "assessment" and a["applied"]                          # it went in, then
    ev = hunt.events(a["id"])[-1]
    assert (ev["kind"], ev["from"], ev["to"], ev["source"]) == ("status", "saved", "assessment", "chat") and t("Written test") in ev["note"]
    hunt.update_booked(a["id"], "宣讲会", {"title": "一面"})                    # renamed to a later round
    assert hunt.application(a["id"])["status"] == "interview"
    hunt.add_booked(a["id"], {"title": "测评", "at": "2026-10-30"})              # never back
    assert hunt.application(a["id"])["status"] == "interview"
    hunt.set_status(a["id"], "rejected")
    hunt.add_booked(a["id"], {"title": "二面", "at": "2026-11-02"})              # never off an outcome
    assert hunt.application(a["id"])["status"] == "rejected"
    hunt.update_application(a["id"], {"apply_by": "2026-10-31"})                 # applying is no round
    assert hunt.application(a["id"])["status"] == "rejected"


def test_a_deadline_field_from_before_becomes_the_event(hunt):
    hunt.add_company({"name": "Xinghe"})
    a1 = hunt.add_application("Xinghe", {"role": "后端"})
    a2 = hunt.add_application("Xinghe", {"role": "前端"})
    data = hunt._apps()
    for a in data["applications"]:   # a file from before 2026-10-21: a field, no event
        a["schedule"] = [{"id": "E1", "title": "一面", "at": "2026-10-25", "minutes": 0, "where": "", "note": ""}] if a["id"] == a1["id"] else []
        a["deadline"] = "2026-10-31" if a["id"] == a1["id"] else ""
    hunt._write("applications.json", data)
    hunt.migrate()
    x1, x2 = hunt.application(a1["id"]), hunt.application(a2["id"])
    assert "deadline" not in x1 and "deadline" not in x2
    assert [(e["id"], e.get("kind"), e["at"]) for e in x1["schedule"]] == [("E1", None, "2026-10-25"), ("E2", "apply", "2026-10-31")]
    assert x2["schedule"] == []                                                     # no date given: none made up
    hunt.migrate()                                                                  # once only
    assert len(hunt.application(a1["id"])["schedule"]) == 2


def test_the_apply_events_made_for_every_role_go_once(hunt):
    """Until 2026-10-26 every role got a dateless 投递截止 (read as 招满即止), titled in the language of the day."""
    hunt.add_company({"name": "Xinghe"})
    ids = [hunt.add_application("Xinghe", {"role": r})["id"] for r in ("后端", "前端", "算法", "测试")]
    data = hunt._apps()
    data.pop("migrated")                                                            # a file from before
    old = {ids[0]: [{"id": "E1", "kind": "apply", "title": "投递截止", "at": "", "minutes": 0, "where": "", "note": ""},
                    {"id": "E2", "title": "一面", "at": "2026-10-25", "minutes": 0, "where": "", "note": ""}],
           ids[1]: [{"id": "E1", "kind": "apply", "title": "Apply by", "at": "2026-10-31", "minutes": 0, "where": "", "note": ""}],
           ids[2]: [{"id": "E1", "kind": "apply", "title": "投递截止", "at": "", "minutes": 0, "where": "", "note": "HR 说月底前"}],
           ids[3]: [{"id": "E1", "kind": "apply", "title": "网申截止（我自己记的）", "at": "", "minutes": 0, "where": "", "note": ""}]}
    for a in data["applications"]:
        a["schedule"] = old[a["id"]]
    hunt._write("applications.json", data)
    hunt.migrate()
    got = {i: [(e["id"], e.get("kind"), e["title"]) for e in hunt.application(i)["schedule"]] for i in ids}
    assert got[ids[0]] == [("E2", "round1", "")]                                    # the made-up one goes; 一面 is built in
    assert got[ids[1]] == [("E1", "apply", "")]                                     # a real date stays, its name follows the language
    assert got[ids[2]] == [("E1", "apply", "")]                                     # a note on it: the user's, kept
    assert got[ids[3]] == [("E1", "apply", "网申截止（我自己记的）")]                    # renamed by the user: kept as written
    hunt.migrate()                                                                  # once only
    assert got[ids[0]] == [(e["id"], e.get("kind"), e["title"]) for e in hunt.application(ids[0])["schedule"]]


def test_a_role_is_in_the_batch_the_user_names(hunt):
    hunt.add_company({"name": "Xinghe"})
    a = hunt.add_application("Xinghe", {"role": "后端", "batch": " 27秋招 "})
    assert a["batch"] == "27秋招"
    assert hunt.update_application(a["id"], {"batch": "2027 Graduate Program"})["batch"] == "2027 Graduate Program"
    assert hunt.update_application(a["id"], {"batch": ""})["batch"] == ""


def test_the_calendar_file_imports_anywhere(hunt):
    from jobhunt.ics import calendar
    hunt.add_company({"name": "星河科技"})
    a = hunt.add_application("星河科技", {"role": "服务端, 研发", "url": "https://x.example/1", "apply_by": "2026-10-31"})
    hunt.add_booked(a["id"], {"title": "一面", "at": "2026-10-25 14:00", "minutes": 45, "where": "A座; 3楼", "note": "带简历\n两份"})
    hunt.add_booked(a["id"], {"title": "笔试", "at": "2026-10-23"})
    text = calendar(hunt.applications(), {c["id"]: c["name"] for c in hunt.companies()})
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n") and "\n" not in text.replace("\r\n", "")
    unfolded = text.replace("\r\n ", "")
    assert "DTSTART:20261025T140000\r\nDTEND:20261025T144500" in unfolded                       # floating local time
    assert "DTSTART;VALUE=DATE:20261023\r\nDTEND;VALUE=DATE:20261024" in unfolded               # a day, no time
    assert "SUMMARY:" + t("1st interview") + " · 星河科技 · 服务端\\, 研发" in unfolded and "LOCATION:A座\\; 3楼" in unfolded
    assert "DESCRIPTION:带简历\\n两份\\nhttps://x.example/1" in unfolded
    # applying keeps the UID it had as a field: importing again updates it
    assert "UID:A1-E2@resume-studio" in unfolded and "UID:A1-deadline@resume-studio" in unfolded and "UID:A1-E1@" not in unfolded
    assert "DTSTART;VALUE=DATE:20261031\r\nDTEND;VALUE=DATE:20261101\r\nSUMMARY:" + t("Apply by") + " · 星河科技 · 服务端\\, 研发" in unfolded
    # with no date (招满即止) it isn't on the calendar
    hunt.update_application(a["id"], {"apply_by": "rolling"})
    assert "UID:A1-deadline@" not in calendar(hunt.applications(), {})
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))


def test_the_old_sign_ups_move_in_once(tmp_path, data_file):
    old = {"signups": [{"id": "S1", "company": "星河科技", "site": "https://careers.xinghe.example/campus", "subset": "RES-TECH-ZH",
                        "note": "手机号登录", "roles": [{"key": "r1", "role": "服务端", "url": "", "app": "4"},
                                                    {"key": "r2", "role": "算法", "url": "", "app": None}],
                        "sent": [{"at": "2026-10-12T10:00:00", "subset": "RES-TECH-ZH", "n": 1, "roles": ["r1"]}]}], "sent": {}}
    state = data_file.parent / "resume-studio-data"
    state.mkdir(exist_ok=True)
    (state / "applications.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    from resume.app import create_app
    inst = create_app(data_file, watch_interval=0.15, prefs_dir=data_file.parent / "local")
    try:
        h = Hunt(state)
        c = h.company("星河科技")
        assert c["site"].endswith("/campus") and c["subset"] == "RES-TECH-ZH" and c["note"] == "手机号登录"
        apps = {a["role"]: a for a in h.applications(c["id"])}
        assert apps["服务端"]["status"] == "applied" and apps["服务端"]["sent"][0]["n"] == 1 and apps["服务端"]["co_app"] == "4"
        assert apps["算法"]["status"] == "saved" and not apps["算法"]["sent"] and [a["order"] for a in apps.values()] == [1, 2]
    finally:
        inst.shutdown()
    inst = create_app(data_file, watch_interval=0.15, prefs_dir=data_file.parent / "local")   # not twice
    inst.shutdown()
    assert len(Hunt(state).applications()) == 2


# ---------------------------------------------------------------- over HTTP and from Claude's command
def cli(data_file, *args, stdin: str = "") -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HUNT_PY), "--data", str(data_file), *args], input=stdin.encode("utf-8"),
                       capture_output=True, timeout=60)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


@pytest.fixture
def live(server, data_file):
    (data_file.parent / "resume-studio-data" / ".cache").mkdir(parents=True, exist_ok=True)
    (data_file.parent / "resume-studio-data" / ".cache" / "server.json").write_text(json.dumps({"url": server.url}), encoding="utf-8")
    return server


def test_claude_works_the_tracker_from_its_command(live, data_file):
    code, out = cli(data_file, "add-company", "星河科技", "--site", "https://careers.xinghe.example/campus", "--alias", "Xinghe")
    assert code == 0 and "C1 星河科技 (also: Xinghe)" in out and "careers site: https://careers.xinghe.example/campus" in out
    assert cli(data_file, "add-role", "Xinghe", "服务端研发工程师")[0] == 0
    code, out = cli(data_file, "add-role", "Xinghe", "算法工程师", "--order", "1")
    assert "A2    星河科技 / 算法工程师  [saved]  (its own site, choice 1)" in out
    code, out = cli(data_file, "status", "Xinghe/服务端", "一面", "--note", "一面 周三 14:00")
    assert code == 0 and "[interview]" in out and "saved → interview: 一面 周三 14:00  [chat]" in out
    code, out = cli(data_file, "book", "Xinghe/服务端", "二面", "--at", "2030-10-25 14:00", "--where", "腾讯会议")
    assert code == 0 and "[when to apply by]" not in out and "E1   2030-10-25 14:00  2nd interview (二面) @ 腾讯会议" in out
    code, out = cli(data_file, "list", "--status", "interview")
    assert out.strip().splitlines() == ["A1    星河科技 / 服务端研发工程师  [interview]  (its own site, choice 2)  next: 2nd interview (二面) 2030-10-25 14:00"]
    code, out = cli(data_file, "status", "Xinghe/服务端", "maybe")
    assert code != 0 and out.startswith("error:")
    about = {"summary": "电商平台", "points": [{"topic": "业务", "text": "星河科技与 Nebula", "sources": ["https://example.com/r"]}]}
    code, out = cli(data_file, "about", "Xinghe", "--stdin", stdin=json.dumps(about, ensure_ascii=False))
    assert code == 0 and "about (read " in out and "- 业务: 星河科技与 Nebula  [https://example.com/r]" in out
    # the window sees the same, with its last event
    tab = live.req("GET", "/api/hunt")[1]
    assert tab["tracker"] == "studio" and [s["id"] for s in tab["states"]][:2] == ["saved", "applied"]
    row = next(a for a in tab["applications"] if a["id"] == "A1")
    assert row["company_name"] == "星河科技" and row["last"]["kind"] == "status"
    c = tab["companies"][0]
    assert (c["roles"], c["open"], c["best"]) == (2, 2, "interview")
    assert {p["id"] for p in tab["platforms"]} >= {"linkedin", "boss"}


def test_a_roles_jd_is_kept_apart_from_its_notes(live, data_file):
    cli(data_file, "add-company", "星河科技")
    cli(data_file, "add-role", "星河科技", "服务端", "--note", "学长内推")
    jd = "服务端研发工程师｜北京\n\n职位描述：\n· 负责交易系统\t设计\n\n任职要求：\n· 2027 届本硕"
    code, out = cli(data_file, "jd", "星河科技/服务端", "--stdin", stdin=jd)
    out = out.replace("\r\n", "\n")
    assert code == 0 and "  JD:\n    服务端研发工程师｜北京\n    \n    职位描述：" in out and "notes: 学长内推" in out
    a = live.req("GET", "/api/hunt/apps/A1")[1]["application"]
    assert a["jd"] == jd.replace("\t", " ") and a["note"] == "学长内推"      # lines and blank lines survive
    assert cli(data_file, "jd", "星河科技/服务端", "--stdin", stdin="  ")[0] != 0   # nothing to keep
    assert "JD: not kept yet" in cli(data_file, "add-role", "星河科技", "算法")[1]
    assert live.req("PUT", "/api/hunt/apps/A1", {"jd": ""})[1]["application"]["jd"] == ""   # the window can clear it


def test_a_roles_referral_code_has_its_own_field(live, data_file):
    cli(data_file, "add-company", "星河科技")
    code, out = cli(data_file, "add-role", "星河科技", "服务端", "--referral", "NTA8X2")
    assert code == 0 and "referral code NTA8X2" in out
    assert live.req("GET", "/api/hunt/apps/A1")[1]["application"]["referral"] == "NTA8X2"
    assert "referral code" not in cli(data_file, "add-role", "星河科技", "算法")[1]   # none got: no referral
    assert live.req("PUT", "/api/hunt/apps/A1", {"referral": ""})[1]["application"]["referral"] == ""


def test_a_role_is_in_several_places_and_remote_says_where(live, data_file):
    cli(data_file, "add-company", "星河科技")
    code, out = cli(data_file, "add-role", "星河科技", "服务端", "--place", "南京", "--place", "上海", "--place", "远程（中国大陆）",
                    "--place", "hybrid: 悉尼", "--place", "南京")
    # found in the list of places by any name: kept by id, under its English name (the window shows it in its language)
    assert code == 0 and "in Nanjing; Shanghai; remote: Mainland China; Sydney (hybrid)" in out
    a = live.req("GET", "/api/hunt/apps/A1")[1]["application"]
    assert a["places"] == [{"kind": "onsite", "area": "Nanjing", "id": "g1799962"}, {"kind": "onsite", "area": "Shanghai", "id": "g1796236"},
                           {"kind": "remote", "area": "Mainland China", "id": "r:mainland-china"},
                           {"kind": "hybrid", "area": "Sydney", "id": "g2147714"}]
    # remote must say the region it is open to
    status, r = live.req("PUT", "/api/hunt/apps/A1", {"places": [{"kind": "remote", "area": ""}]})
    assert status == 400 and r["error"]
    assert cli(data_file, "edit-role", "A1", "--place", "remote")[0] != 0
    v = live.req("PUT", "/api/hunt/apps/A1", {"places": [{"kind": "remote", "area": "不限"}]})[1]["application"]
    assert v["places"] == [{"kind": "remote", "area": "Anywhere", "id": "r:any"}]
    assert live.req("GET", "/api/hunt/apps/A1")[1]["application"]["places"] == v["places"]
    assert cli(data_file, "edit-role", "A1", "--no-places")[0] == 0
    assert live.req("GET", "/api/hunt/apps/A1")[1]["application"]["places"] == []


def test_places_are_picked_from_a_list_and_read_in_the_interfaces_language(live, data_file):
    from jobhunt import geo
    # typing finds them: the bigger first, told apart by state and country; regions first for a remote role
    found = live.req("GET", "/api/hunt/places?q=syd&kind=onsite")[1]["places"]   # in the window's language
    assert found[0]["id"] == "g2147714" and found[0]["name"] == geo.name("g2147714") and geo.name("t:AU") in found[0]["detail"]
    assert found[1]["id"] == "g6354908" and geo.name("t:CA") in found[1]["detail"]
    assert live.req("GET", "/api/hunt/places?q=%E4%B8%AD%E5%9B%BD%E5%A4%A7%E9%99%86&kind=remote")[1]["places"][0]["id"] == "r:mainland-china"
    assert live.req("GET", "/api/hunt/places?q=&kind=onsite")[1]["places"] == []
    # one name, every language: what makes a table read in one language
    assert [geo.name("g2147714", x) for x in ("en", "zh", "ja", "ko")] == ["Sydney", "悉尼", "シドニー", "시드니"]
    assert geo.name("g1835848", "zh") == "首尔" and geo.name("g1808926", "ja") == "杭州"     # no 特别市, no 市
    assert geo.name("t:US", "zh") == "美国" and geo.name("t:HK", "zh") == "香港" and geo.name("t:US", "en") == "United States"
    assert geo.name("r:mainland-china", "zh") == "中国大陆" and geo.name("r:any", "de") == "Überall"
    # picked in the window by id; an id the list doesn't have is the text as written
    cli(data_file, "add-company", "星河科技")
    cli(data_file, "add-role", "星河科技", "服务端")
    a = live.req("PUT", "/api/hunt/apps/A1", {"places": [{"kind": "onsite", "id": "g2147714", "area": "悉尼"},
                                                          {"kind": "onsite", "id": "g0", "area": "滨江园区"}]})[1]["application"]
    assert a["places"] == [{"kind": "onsite", "area": "Sydney", "id": "g2147714"}, {"kind": "onsite", "area": "滨江园区"}]
    tab = live.req("GET", "/api/hunt")[1]
    assert tab["places"]["g2147714"]["name"] == geo.name("g2147714") and tab["places"]["g2147714"]["lat"] == -33.868
    assert tab["geo"] == {"滨江园区": None}                                      # only the written ones are looked up by text
    # a company's headquarters are one city, the same way (sent as `location` before 2026-10-27, still understood)
    c = live.req("PUT", "/api/hunt/companies/C1", {"hq": {"kind": "onsite", "id": "g1796236", "area": "上海"}})[1]["company"]
    assert c["hq"] == {"area": "Shanghai", "id": "g1796236"} and "hq" in c["kept"]
    code, out = cli(data_file, "edit-company", "星河科技", "--location", "北京（海淀区）")
    assert code == 0 and "headquarters: Beijing" in out
    assert live.req("PUT", "/api/hunt/companies/C1", {"hq": ""})[1]["company"]["hq"] is None


def test_places_are_found_on_the_map_by_any_name():
    from jobhunt.geo import find
    hangzhou = find("杭州")
    assert hangzhou["name"] == "Hangzhou" and hangzhou["country"] == "CN"
    for same in ("杭州·余杭区", "杭州市", "Hangzhou"):
        assert find(same)["lat"] == hangzhou["lat"]
    assert find("北京市海淀区")["name"] == "Beijing"            # by its start
    assert find("悉尼")["country"] == "AU" and find("Sydney, NSW")["country"] == "AU"   # the bigger Sydney
    assert find("München")["name"] == "Munich" and find("東京")["name"] == "Tokyo"
    assert find("湾区") is None and find("") is None


def test_a_city_after_its_province_or_with_its_name_in_brackets_is_found():
    from jobhunt.geo import find
    assert find("湖南长沙")["name"] == "Changsha"                 # not 湖南, a city in Japan
    assert find("浙江杭州")["name"] == "Hangzhou" and find("广东 广州市")["name"] == "Guangzhou"
    assert find("印度 班加罗尔(卡纳塔克邦)")["name"] == "Bengaluru"
    assert find("美国加州卡尔弗城(Culver City);东南亚业务重心在新加坡")["name"] == "Culver City"
    assert find("纳拉布里") is None                               # not 布里, in Brazil: any end would be a guess


# what Nominatim answers (cut down): 雄安, and the guess it once made for 纳拉布里 (Narrabri)
XIONGAN = [{"osm_type": "relation", "osm_id": 7157395, "category": "boundary", "type": "administrative", "place_rank": 10,
            "lat": "39.0480479", "lon": "115.9075331", "name": "Xiong'an New Area",
            "namedetails": {"name": "雄安新区", "name:en": "Xiong'an New Area", "name:ko": "슝안신구", "name:zh": "雄安新区"},
            "address": {"country_code": "cn", "state": "Hebei"}}]
BRISBANE_US = [{"osm_type": "relation", "osm_id": 2834528, "category": "boundary", "place_rank": 16, "lat": "37.68",
                "lon": "-122.4", "name": "Brisbane", "namedetails": {"name": "Brisbane", "name:zh": "布里斯班"},
                "address": {"country_code": "us"}}]


def test_a_city_the_list_doesnt_know_is_looked_up_on_openstreetmap_once(hunt, monkeypatch):
    from jobhunt import geo
    asked: list[str] = []
    monkeypatch.setattr(geo, "_fetch", lambda q: asked.append(q) or {"雄安": XIONGAN, "纳拉布里": BRISBANE_US}.get(q, []))
    monkeypatch.setenv("STUDIO_PLACES", "on")
    hunt.add_company({"name": "拼多多"})
    hunt.update_company("C1", {"places": ["上海", "雄安", "纳拉布里"], "hq": "雄安"})
    assert [p.get("id") for p in hunt.company("C1")["places"]] == ["g1796236", None, None]   # written as it was, first
    assert hunt.link_places(geo.look_up) == 2                       # then found: 雄安, where it hires and its HQ
    c = hunt.company("C1")
    assert c["places"][1] == {"kind": "onsite", "area": "Xiong'an New Area", "id": "o:R7157395"}
    assert c["hq"] == {"area": "Xiong'an New Area", "id": "o:R7157395"}
    assert c["places"][2] == {"kind": "onsite", "area": "纳拉布里"}     # Brisbane, USA isn't 纳拉布里: left as written
    # a place like the others: its name in every language, its state, suggested while typing
    assert [geo.name("o:R7157395", x) for x in ("en", "zh", "ja", "ko")] == ["Xiong'an New Area", "雄安新区", "雄安新区", "슝안신구"]
    assert geo.label("o:R7157395", "zh")["detail"] == "河北, 中国" and geo.find("雄安")["id"] == "o:R7157395"
    assert geo.search("xiong'an", lang="en")[0]["id"] == "o:R7157395"
    # each name is asked once, and kept with the data
    assert sorted(asked) == ["纳拉布里", "雄安"]
    assert hunt.link_places(geo.look_up) == 0 and len(asked) == 2
    saved = json.loads((hunt.dir / "places.json").read_text(encoding="utf-8"))
    assert saved["places"]["o:R7157395"]["names"]["zh"] == "雄安新区" and saved["asked"]["纳拉布里"][1] == ""
    # nothing goes out while it is off (the tests' default)
    monkeypatch.setenv("STUDIO_PLACES", "off")
    assert geo.look_up("嘉兴南湖") is None and len(asked) == 2


def test_places_read_wrong_by_the_first_lookup_are_looked_up_again(tmp_path):
    """2026-10-27: the first migration read 湖南长沙 as Konan (Japan); roles and companies not changed since are
    looked up again from the files it kept."""
    d = tmp_path / "resume-studio-data" / "hunt"
    d.mkdir(parents=True)
    old_c = {"companies": [{"id": "C1", "name": "茶颜悦色", "location": "湖南长沙", "places": ["长沙"], "updated": "2026-10-20T10:00:00"},
                           {"id": "C2", "name": "零跑", "location": "浙江杭州", "updated": "2026-10-20T10:00:00"}]}
    old_a = {"applications": [{"id": "A1", "company": "C1", "role": "店长", "places": ["广东 广州市"], "updated": "2026-10-20T10:00:00"}]}
    (d / "companies.before-place-ids.json").write_text(json.dumps(old_c, ensure_ascii=False), encoding="utf-8")
    (d / "applications.before-place-ids.json").write_text(json.dumps(old_a, ensure_ascii=False), encoding="utf-8")
    now_c = {"companies": [{"id": "C1", "name": "茶颜悦色", "hq": {"area": "Konan", "id": "g11612571"},
                            "places": [{"kind": "onsite", "area": "Changsha", "id": "g1815577"}], "updated": "2026-10-20T10:00:00"},
                           {"id": "C2", "name": "零跑", "hq": {"area": "Shenzhen", "id": "g1795565"}, "kept": ["hq"],
                            "updated": "2099-01-01T00:00:00"}]}   # changed by the user since: theirs
    now_a = {"migrated": ["builtin-events", "place-ids"], "applications": [
        {"id": "A1", "company": "C1", "role": "店长", "places": [{"kind": "onsite", "area": "广东 广州市"}], "updated": "2026-10-20T10:00:00"}]}
    (d / "companies.json").write_text(json.dumps(now_c, ensure_ascii=False), encoding="utf-8")
    (d / "applications.json").write_text(json.dumps(now_a, ensure_ascii=False), encoding="utf-8")
    h = Hunt(tmp_path / "resume-studio-data")
    h.migrate()
    assert h.company("C1")["hq"] == {"area": "Changsha", "id": "g1815577"}
    assert h.company("C2")["hq"] == {"area": "Shenzhen", "id": "g1795565"}
    assert h.application("A1")["places"] == [{"kind": "onsite", "area": "Guangzhou", "id": "g1809858"}]
    assert "place-ids-2" in json.loads((d / "applications.json").read_text(encoding="utf-8"))["migrated"]


def test_a_company_is_described_in_the_language_of_where_the_user_goes_for_it():
    from jobhunt.writing import needs_rewrite, research_language, written_in
    sydney, shanghai, taipei, berlin = ({"id": x} for x in ("g2147714", "g1796236", "g1668341", "g2950159"))
    mine = ["en", "zh"]                                             # the resume's languages
    rsm, pdd = {"hq": {"area": "Sydney", **sydney}}, {"hq": {"area": "Shanghai", **shanghai}}
    assert research_language(rsm, [], mine) == {"lang": "en", "why": "hq", "place": "g2147714"}
    assert research_language(pdd, [], mine)["lang"] == "zh"
    # where the user applies comes first: a Chinese company's office in Sydney reads in English
    assert research_language(pdd, [{"places": [{"kind": "onsite", "area": "Sydney", **sydney}]}], mine) == \
        {"lang": "en", "why": "applying", "place": "g2147714"}
    # a German company: in German only when the resume is in German too, else English
    assert research_language({"hq": {"area": "Berlin", **berlin}}, [], mine) == {"lang": "en", "why": "fallback", "place": "g2950159"}
    assert research_language({"hq": {"area": "Berlin", **berlin}}, [], ["zh", "de"])["lang"] == "de"
    assert research_language({"hq": {"area": "Taipei", **taipei}}, [], mine)["lang"] == "zh"   # either Chinese will do
    # where it is isn't known yet: what is there stays as it is
    r = research_language({}, [], mine)
    assert r["why"] == "unknown" and needs_rewrite({"industry": "会计 / 审计"}, r) == ""
    # RSM Australia, described in Chinese before 2026-10-27, is to be written again
    old = {**rsm, "industry": "会计 / 审计", "about": {"summary": "RSM Australia 是澳大利亚排名前列的会计师事务所"}}
    assert needs_rewrite(old, research_language(rsm, [], mine)) == "zh"
    assert written_in("Accounting, audit and tax advisory for mid-sized firms in Australia", "en") == "en"
    assert written_in("Wirtschaftsprüfung und Steuerberatung für den Mittelstand in der Region", "en") == "de"
    assert written_in("シドニーに本社を置く会計事務所", "en") == "ja"


def test_fill_says_which_language_the_research_is_written_in(live, data_file):
    cli(data_file, "add-company", "RSM Australia")
    code, out = cli(data_file, "fill", "RSM Australia", "--stdin", stdin=json.dumps(
        {"hq": "Sydney", "industry": "会计 / 审计 / 税务", "about": {"summary": "RSM Australia 是澳大利亚排名前列的会计师事务所"}}))
    assert code == 0 and "language: what you wrote is in 简体中文, but this company's research is written in English (en)" in out
    assert "write industry, strength, size and about in: English (en; its headquarters is in Sydney)" in out
    assert "what is there now is in 简体中文: write it again in English" in out
    c = live.req("GET", "/api/hunt")[1]["companies"][0]
    assert c["research"]["lang"] == "en" and c["research"]["rewrite"] == "zh"
    code, out = cli(data_file, "fill", "RSM Australia", "--stdin", stdin=json.dumps(
        {"industry": "Accounting, audit and tax", "about": {"summary": "RSM Australia is one of the largest accounting firms in Australia."}}))
    assert "language:" not in out and "what is there now" not in out
    assert live.req("GET", "/api/hunt")[1]["companies"][0]["research"]["rewrite"] == ""


def test_companies_hire_in_places_and_the_map_knows_where_they_are(live, data_file):
    cli(data_file, "add-company", "星河科技")
    code, out = cli(data_file, "fill", "星河科技", "--stdin", stdin=json.dumps(
        {"location": "上海", "places": ["上海", "北京", "remote: 中国大陆", "滨江园区"], "sources": {"places": "https://xinghe.example/jobs"}}))
    assert code == 0 and ("hires in: Shanghai; Beijing; remote: Mainland China; 滨江园区 [not in the list of places]"
                          "  [https://xinghe.example/jobs]") in out and "headquarters: Shanghai" in out
    cli(data_file, "add-role", "星河科技", "服务端", "--place", "杭州·余杭区")
    tab = live.req("GET", "/api/hunt")[1]
    places, geo = tab["places"], tab["geo"]
    assert places["g1796236"]["lat"] == 31.222 and places["g1808926"]["lat"] == 30.294
    assert tab["companies"][0]["hq"] == {"area": "Shanghai", "id": "g1796236"}
    assert geo == {"滨江园区": None} and places["r:mainland-china"]["lat"] is None   # remote isn't a spot on the map
    # one the map can't find is pinned where the user (or Claude) says
    code, out = cli(data_file, "pin", "滨江园区", "30.21", "120.21")
    assert code == 0 and "30.21, 120.21 (pinned)" in out
    assert live.req("GET", "/api/hunt")[1]["geo"]["滨江园区"]["pinned"]
    assert "滨江园区 [not in the list of places]: 30.21, 120.21 (pinned)" in cli(data_file, "where")[1]
    # a picked one the map puts wrong is pinned by its id
    assert live.req("PUT", "/api/hunt/geo", {"area": "g1808926", "lat": 30.42, "lon": 120.3})[0] == 200
    assert live.req("GET", "/api/hunt")[1]["places"]["g1808926"]["lat"] == 30.42
    assert live.req("PUT", "/api/hunt/geo", {"area": "滨江园区", "lat": None})[0] == 200
    assert live.req("GET", "/api/hunt")[1]["geo"]["滨江园区"] is None
    assert live.req("PUT", "/api/hunt/geo", {"area": "x", "lat": 95, "lon": 0})[0] == 400
    # edited in the window, the places are the user's: a later fill leaves them
    live.req("PUT", "/api/hunt/companies/C1", {"places": ["杭州"]})
    code, out = cli(data_file, "fill", "星河科技", "--stdin", stdin=json.dumps({"places": ["广州"]}))
    assert "left as the user set them: places" in out and "hires in: Hangzhou" in out


def test_booked_from_the_window_and_from_claude_end_in_one_calendar_file(live, data_file):
    cli(data_file, "add-company", "星河科技")
    cli(data_file, "add-role", "星河科技", "服务端")
    code, out = cli(data_file, "book", "星河科技/服务端", "一面", "--at", "2026-10-25 14:00", "--minutes", "45")
    assert code == 0 and "E1   2026-10-25 14:00  1st interview (一面) (45 min)" in out
    v = live.req("POST", "/api/hunt/apps/A1/schedule", {"title": "二面", "at": "2026-10-28"})[1]
    assert [e.get("kind") or e["title"] for e in v["application"]["schedule"]] == ["round1", "round2"]
    v = live.req("PUT", "/api/hunt/apps/A1/schedule/E2", {"at": "2026-10-29 10:00"})[1]
    assert v["application"]["schedule"][1]["at"] == "2026-10-29 10:00"
    assert "E1   2026-10-25 14:00  1st interview (一面)" in cli(data_file, "rebook", "星河科技/服务端", "一面", "--where", "A座")[1]
    code, out = cli(data_file, "ics")
    path = Path(out.split("written: ")[1].split(" (")[0])
    assert code == 0 and path.name == "applying.ics" and path.read_text(encoding="utf-8").count("BEGIN:VEVENT") == 2
    r = live.req("POST", "/api/hunt/ics", {"app": "A1", "event": "E2"})[1]
    assert Path(r["path"]).name == "applying-A1-E2.ics" and t("2nd interview") in Path(r["path"]).read_text(encoding="utf-8")
    assert live.req("DELETE", "/api/hunt/apps/A1/schedule/E1")[1]["application"]["schedule"][0]["id"] == "E2"
    assert cli(data_file, "unbook", "星河科技/服务端", "E1")[0] != 0   # gone already


def test_the_sign_up_goes_out_with_a_kept_version(live, data_file):
    cli(data_file, "add-company", "星河科技")
    cli(data_file, "add-role", "星河科技", "服务端")
    cli(data_file, "add-role", "星河科技", "算法")
    code, out = cli(data_file, "sent", "星河科技")
    assert code != 0 and out.startswith("error:")                       # it isn't filled from a subset yet
    assert live.req("POST", "/api/hunt/companies/C1/sent")[0] == 409
    assert live.req("PUT", "/api/hunt/companies/C1", {"subset": "NOPE"})[0] == 404
    live.req("PUT", "/api/hunt/companies/C1", {"subset": "RES-TECH-ZH"})
    code, out = cli(data_file, "sent", "星河科技")
    assert code == 0 and "sent with RES-TECH-ZH v1: A1, A2" in out
    v = live.req("GET", "/api/hunt/companies/C1")[1]
    assert all(a["status"] == "applied" and a["sent"][0]["n"] == 1 for a in v["applications"])
    assert [e["kind"] for e in v["events"]].count("sent") == 2
    assert live.req("POST", "/api/hunt/companies/C1/sent")[0] == 409     # nothing new to send
    cli(data_file, "add-role", "星河科技", "数据")                          # a role added later goes out alone, as the same v1
    code, out = cli(data_file, "sent", "星河科技")
    assert "sent with RES-TECH-ZH v1: A3" in out
    st = live.req("GET", "/api/state")[1]
    assert st["subset_versions"]["RES-TECH-ZH"]["count"] == 1


def test_with_career_ops_as_the_tracker_sent_roles_get_rows_there(live, data_file, tmp_path):
    checkout = tmp_path / "career-ops"
    shutil.copytree(FIXTURE, checkout)
    (checkout / "node_modules" / "js-yaml").mkdir(parents=True)
    Prefs(data_file.parent / "local").save(career_ops={"path": str(checkout)})
    assert live.req("PUT", "/api/hunt/settings", {"tracker": "nope"})[0] == 400
    assert live.req("PUT", "/api/hunt/settings", {"tracker": "career-ops"})[1]["tracker"] == "career-ops"
    live.req("POST", "/api/hunt/companies", {"name": "星河科技", "subset": "RES-TECH-ZH"})
    for role in ("服务端研发工程师", "算法工程师"):
        live.req("POST", "/api/hunt/companies/C1/roles", {"role": role})
    code, r = live.req("POST", "/api/hunt/companies/C1/sent")
    assert code == 200 and r["copied"] == 2 and [a["co_app"] for a in r["applications"]] == ["4", "5"]
    md = (checkout / "data" / "applications.md").read_text(encoding="utf-8")
    assert "服务端研发工程师" in md and "算法工程师" in md
    # Resume Studio's own tracker leaves career-ops alone
    live.req("PUT", "/api/hunt/settings", {"tracker": "studio"})
    live.req("POST", "/api/hunt/companies/C1/roles", {"role": "数据分析"})
    assert live.req("POST", "/api/hunt/companies/C1/sent")[1]["copied"] == 0
    assert "数据分析" not in (checkout / "data" / "applications.md").read_text(encoding="utf-8")


def test_the_window_edits_and_deletes(live):
    assert live.req("POST", "/api/hunt/companies", {"name": ""})[0] == 400
    c = live.req("POST", "/api/hunt/companies", {"name": "美团"})[1]["company"]
    code, v = live.req("POST", f"/api/hunt/companies/{c['id']}/roles", {"role": "后端", "channel": "platform", "platform": "boss"})
    aid = v["application"]["id"]
    assert v["application"]["platform"] == "boss"
    assert live.req("POST", f"/api/hunt/apps/{aid}/status", {"status": "offer", "note": "口头 offer"})[1]["application"]["status"] == "offer"
    assert live.req("POST", f"/api/hunt/apps/{aid}/note", {"note": ""})[0] == 400
    assert live.req("PUT", f"/api/hunt/apps/{aid}", {"channel": "fax"})[0] == 400
    code, v = live.req("PUT", f"/api/hunt/apps/{aid}", {"channel": "referral"})
    assert v["application"]["channel"] == "referral" and v["application"]["platform"] == ""
    assert live.req("DELETE", f"/api/hunt/apps/{aid}")[0] == 200 and live.req("GET", f"/api/hunt/apps/{aid}")[0] == 404
    assert live.req("DELETE", f"/api/hunt/companies/{c['id']}")[0] == 200
    assert live.req("GET", "/api/hunt")[1]["companies"] == []
