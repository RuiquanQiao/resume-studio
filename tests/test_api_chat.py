"""HTTP API and the Claude Code chat bridge (with a fake `claude`)."""
from __future__ import annotations

import base64
import json
import shutil
import time
import urllib.parse

import pytest

from conftest import calls

needs_tex = pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")


def test_state_and_item_update(server, data_file):
    code, s = server.req("GET", "/api/state")
    assert code == 200 and s["doc"]["entries"] and s["claude"]["available"]
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-003")
    e["location"] = "Canberra"
    code, s2 = server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-003", "data": e, "base": s["hashes"]["entry"]["EXP-003"]})
    assert code == 200
    assert "location: Canberra" in data_file.read_text(encoding="utf-8")
    # the same stale base now conflicts
    code, err = server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-003", "data": e, "base": s["hashes"]["entry"]["EXP-003"]})
    assert code == 409 and err["data"]["current"]["location"] == "Canberra"


def test_entries_and_versions_lifecycle(server):
    code, r = server.req("POST", "/api/entries", {"section": "projects"})
    assert code == 200 and r["id"] == "EXP-006"
    code, r = server.req("POST", "/api/versions", {"id": "RES-X", "copy_from": "RES-TECH-EN"})
    assert r["id"] == "RES-X"
    v = next(x for x in r["doc"]["versions"] if x["id"] == "RES-X")
    assert v["entries"] and v["template"] == "classic"
    code, r = server.req("DELETE", "/api/entries/EXP-002")
    assert all("EXP-002" not in x.get("entries", []) for x in r["doc"]["versions"])
    code, r = server.req("DELETE", "/api/versions/RES-X")
    assert all(x["id"] != "RES-X" for x in r["doc"]["versions"])
    code, r = server.req("DELETE", "/api/versions/ALL")
    assert code == 400                                                  # ALL can't be deleted
    code, r = server.req("POST", "/api/entries/EXP-003/move", {"dir": 1})
    assert code == 200
    code, r = server.req("POST", "/api/versions/ALL/render")
    assert code == 200 and r["version"] == "ALL"


@needs_tex
def test_render_page_images_and_export(server, data_file):
    code, r = server.req("POST", "/api/versions/RES-TECH-EN/render")
    assert code == 200 and r["ok"] and r["pages"] == 1
    if r["images"]:
        code, png = server.req("GET", "/api/versions/RES-TECH-EN/page/1")
        assert code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
    code, r = server.req("POST", "/api/versions/RES-TECH-EN/export")
    assert r["ok"] and (data_file.parent / "exports" / "RES-TECH-EN.pdf").exists()


def test_reveal_refuses_paths_outside_data_folder(server):
    code, r = server.req("POST", "/api/reveal", {"path": "C:/Windows"})
    assert r["ok"] is False


def test_instance_identity(server, data_file):
    code, r = server.req("GET", "/api/instance")
    assert r["app"] == "resume-studio" and r["data"] == str(data_file.resolve())


# ---------------------------------------------------------------- chat
def _send(server, chat_id, text, **extra):
    return server.req("POST", f"/api/chats/{chat_id}/send", {"text": text, **extra})


def test_chat_is_a_real_resumed_session(server, data_file, fake_claude):
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    code, r = _send(server, cid, "你好，帮我看看简历", version_id="RES-TECH-EN", entry_id="EXP-002")
    assert code == 200
    server.wait_idle(cid)
    code, r = server.req("GET", f"/api/chats/{cid}")
    msgs = r["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    blocks = msgs[1]["blocks"]
    assert [b["type"] for b in blocks] == ["tool", "text"]           # tool step, then the reply
    assert blocks[0]["name"] == "Read" and blocks[0]["status"] == "done"
    assert blocks[1]["text"].count("好的，我看过了") == 1              # streamed text not duplicated
    sid = r["chat"]["session_id"]
    assert sid and r["chat"]["title"].startswith("你好")
    ctx_path = calls(fake_claude)[0]["argv"][calls(fake_claude)[0]["argv"].index("--append-system-prompt-file") + 1]
    ctx = open(ctx_path, encoding="utf-8").read()
    assert str(data_file.resolve()) in ctx and "RES-TECH-EN" in ctx and "EXP-002" in ctx

    _send(server, cid, "继续")
    server.wait_idle(cid)
    first, second = calls(fake_claude)
    assert "--resume" not in first["argv"]
    assert second["argv"][second["argv"].index("--resume") + 1] == sid
    for c in (first, second):
        argv = c["argv"]
        assert "--bare" not in argv and "--system-prompt" not in argv   # Claude Code's own brain stays
        assert "--append-system-prompt-file" in argv
        assert c["cwd"] == str(data_file.parent)                        # CLAUDE.md / skills of the data folder load
    assert first["stdin"] == "你好，帮我看看简历"                        # user's words untouched, no language forcing


def _info(server):
    for _ in range(100):
        code, r = server.req("GET", "/api/claude")
        if r["info"]:
            return r
        time.sleep(0.1)
    raise AssertionError(r)


def test_claude_info_lists_models_efforts_and_commands(server):
    r = _info(server)
    info = r["info"]
    names = [m["name"] for m in info["models"]]
    assert names == ["Opus 5.5", "Sonnet 5.5", "Fable 5.1", "Haiku 4.5"]          # the menu Claude Code shows
    fable = next(m for m in info["models"] if m["name"] == "Fable 5.1")
    assert fable["value"] == "claude-fable-5-1[1m]" and fable["tag"]               # the CLI's own id is used
    assert next(m for m in info["models"] if m["name"] == "Haiku 4.5")["efforts"] == []
    assert {m["name"] for m in info["more_models"]} == {"Sonnet 5", "Opus 5"}      # the CLI's aliases stay reachable
    assert info["default_model"]["name"] == "Sonnet 5"
    assert info["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    cmds = {c["name"]: c for c in info["commands"]}
    assert "compact" in cmds and cmds["resume-studio"]["source"] == "user"
    assert info["account"]["plan"] == "Claude Pro" and r["modes"] == ["default", "acceptEdits", "plan", "auto"]


def test_chat_model_effort_and_mode(server, fake_claude):
    code, chat = server.req("POST", "/api/chats", {"model": "claude-opus-5-5", "effort": "high", "mode": "plan"})
    cid = chat["id"]
    assert (chat["model_choice"], chat["effort"], chat["mode"]) == ("claude-opus-5-5", "high", "plan")
    _send(server, cid, "hi")
    server.wait_idle(cid)
    argv = calls(fake_claude)[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--permission-prompt-tool" in argv and "--permission-prompts" not in argv   # prompts reach the UI
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert "Edit" not in allowed and "Write" not in allowed                    # the mode decides edits, not a blanket allow

    code, meta = server.req("PUT", f"/api/chats/{cid}", {"model": None, "effort": None, "mode": None})   # follow the CLI
    assert code == 200 and meta["model_choice"] is None and meta["effort"] is None and meta["mode"] is None
    _send(server, cid, "again")
    server.wait_idle(cid)
    argv = calls(fake_claude)[-1]["argv"]
    assert "--model" not in argv and "--effort" not in argv and "--permission-mode" not in argv and "--resume" in argv

    for bad in ({"model": "--dangerous"}, {"model": "a&b"}, {"effort": "huge"}, {"mode": "bypassPermissions"}, {"title": "  "}):
        assert server.req("PUT", f"/api/chats/{cid}", bad)[0] == 400, bad
    assert server.req("PUT", f"/api/chats/{cid}", {"title": "改名了"})[1]["title"] == "改名了"
    assert server.req("PUT", "/api/chats/nope", {"model": "opus"})[0] == 404


def _wait(cond, timeout=10, msg="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(msg)


def _pending(server, cid):
    def find():
        live = server.req("GET", f"/api/chats/{cid}")[1]["live"] or {}
        return next((b for b in live.get("blocks", []) if b["type"] == "ask" and b["status"] == "pending"), None)
    return _wait(find, msg="a pending card")


def _ctl(fake_claude):
    path = str(fake_claude) + ".ctl"
    try:
        return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    except OSError:
        return []


def test_permission_card_allow_always_and_deny(server, fake_claude):
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    _send(server, cid, "PERM write notes")
    ask = _pending(server, cid)
    assert ask["kind"] == "permission" and ask["tool"] == "Write" and ask["input"]["content"] == "hello\nworld"
    assert cid in server.req("GET", "/api/chats")[1]["waiting"]
    code, r = server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": True, "suggestion": 0})
    assert code == 200 and r["ask"]["status"] == "allowed"
    server.wait_idle(cid)
    answer = [c for c in _ctl(fake_claude) if "answer" in c][-1]["answer"]
    assert answer["behavior"] == "allow" and answer["updatedPermissions"][0]["mode"] == "acceptEdits"
    msgs = server.req("GET", f"/api/chats/{cid}")[1]["messages"]
    assert "已写入" in msgs[-1]["blocks"][-1]["text"]
    assert any(b["type"] == "ask" and b["status"] == "allowed" for b in msgs[-1]["blocks"])   # the card stays in the transcript
    assert server.req("GET", f"/api/chats/{cid}")[1]["chat"]["mode"] == "acceptEdits"        # "always" changed the mode

    _send(server, cid, "PERM again")
    ask = _pending(server, cid)
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": False, "message": "别写这个文件"})
    server.wait_idle(cid)
    answer = [c for c in _ctl(fake_claude) if "answer" in c][-1]["answer"]
    assert answer == {"behavior": "deny", "message": "别写这个文件"}
    assert server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": True})[0] == 409   # already over


def test_question_and_plan_cards(server, fake_claude):
    code, chat = server.req("POST", "/api/chats", {"mode": "plan"})
    cid = chat["id"]
    _send(server, cid, "QUESTION me")
    ask = _pending(server, cid)
    assert ask["kind"] == "question" and ask["input"]["questions"][0]["options"][1]["label"] == "Blue"
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "answers": {"你喜欢哪个颜色？": "Blue"}})
    server.wait_idle(cid)
    assert "你选了 Blue" in server.req("GET", f"/api/chats/{cid}")[1]["messages"][-1]["blocks"][-1]["text"]

    _send(server, cid, "PLAN it")
    ask = _pending(server, cid)
    assert ask["kind"] == "plan" and "改第一条要点" in ask["input"]["plan"]
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "approve": False, "feedback": "先只改措辞"})
    server.wait_idle(cid)
    assert "先只改措辞" in server.req("GET", f"/api/chats/{cid}")[1]["messages"][-1]["blocks"][-1]["text"]
    assert server.req("GET", f"/api/chats/{cid}")[1]["chat"]["mode"] == "plan"        # still planning

    _send(server, cid, "PLAN again")
    ask = _pending(server, cid)
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "approve": True, "mode": "acceptEdits"})
    server.wait_idle(cid)
    answer = [c for c in _ctl(fake_claude) if "answer" in c][-1]["answer"]
    assert answer["behavior"] == "allow" and answer["updatedPermissions"] == [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    assert server.req("GET", f"/api/chats/{cid}")[1]["chat"]["mode"] == "acceptEdits"   # the next reply does not re-plan


def test_queue_while_running_and_interrupt(server, fake_claude):
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    _send(server, cid, "PERM first")                      # waits on a card, so the next one queues
    ask = _pending(server, cid)
    code, r = _send(server, cid, "第二条")
    assert code == 200 and r["queued"] is True
    code, r2 = _send(server, cid, "第三条")
    assert server.req("DELETE", f"/api/chats/{cid}/queue/{r2['id']}")[1]["ok"] is True   # changed my mind
    assert [q["msg"]["text"] for q in server.req("GET", f"/api/chats/{cid}")[1]["live"]["queue"]] == ["第二条"]
    assert _send(server, cid, "x", action="polish", entry_id="EXP-002")[0] == 409        # actions never queue
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": True})
    server.wait_idle(cid)
    msgs = server.req("GET", f"/api/chats/{cid}")[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[2]["text"] == "第二条"
    texts = [c["stdin"] for c in calls(fake_claude)]
    assert texts == ["PERM first", "第二条"] and len({json.dumps(c["argv"]) for c in calls(fake_claude)}) == 1   # same process

    _send(server, cid, "SLOW please")
    time.sleep(0.5)
    server.req("POST", f"/api/chats/{cid}/cancel")
    server.wait_idle(cid, timeout=8)
    assert any(c.get("subtype") == "interrupt" for c in _ctl(fake_claude))               # a real interrupt, not a kill
    last = server.req("GET", f"/api/chats/{cid}")[1]["messages"][-1]
    assert last["cancelled"] is True and last["error"] is None


def test_attachments_images_inline_files_by_path(server, fake_claude, tmp_path):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    doc_file = tmp_path / "旧简历.pdf"
    doc_file.write_bytes(b"%PDF-1.4 fake")
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    code, r = _send(server, cid, "看看这些", attachments=[{"name": "截图.png", "data": "data:image/png;base64," + png},
                                                          {"name": "旧简历.pdf", "path": str(doc_file)}])
    assert code == 200
    server.wait_idle(cid)
    c = calls(fake_claude)[-1]
    assert c["content"] == ["text", "image"]                                   # the picture goes in the message
    assert str(doc_file).replace("\\", "/") in c["stdin"] and "Read" in c["stdin"]   # the PDF is read by path
    assert "--add-dir" in c["argv"]
    user = server.req("GET", f"/api/chats/{cid}")[1]["messages"][0]
    assert user["text"] == "看看这些" and [f["image"] for f in user["files"]] == [True, False]
    code, img = server.req("GET", f"/api/chats/{cid}/file?path=" + urllib.parse.quote(user["files"][0]["path"]))
    assert code == 200 and img.startswith(b"\x89PNG")
    assert server.req("GET", f"/api/chats/{cid}/file?path=" + urllib.parse.quote("C:/Windows/win.ini"))[0] == 404
    code, r = _send(server, cid, "", attachments=[{"name": "a.png", "data": "data:image/png;base64," + png}])
    assert code == 200                                                         # a picture alone is a message


def test_fork_tasks_context_and_slash_command(server, fake_claude):
    code, chat = server.req("POST", "/api/chats", {"effort": "low"})
    cid = chat["id"]
    _send(server, cid, "TASKS first")
    server.wait_idle(cid)
    r = server.req("GET", f"/api/chats/{cid}")[1]
    assert r["chat"]["tasks"] == [{"id": "1", "subject": "读取数据", "active": "", "status": "completed"},
                                  {"id": "2", "subject": "改写要点", "active": "", "status": "pending"}]
    assert r["chat"]["context"] == {"used": 40130, "model": "fake-model", "window": 200000}
    assert server.req("GET", "/api/claude")[1]["limits"]["windows"]["seven_day"]["utilization"] == 0.34
    _send(server, cid, "second")
    server.wait_idle(cid)
    msgs = server.req("GET", f"/api/chats/{cid}")[1]["messages"]
    sid = server.req("GET", f"/api/chats/{cid}")[1]["chat"]["session_id"]

    code, f = server.req("POST", f"/api/chats/{cid}/fork", {"index": 2})
    assert code == 200 and f["text"] == "second" and f["chat"]["effort"] == "low"
    new = f["chat"]["id"]
    assert len(server.req("GET", f"/api/chats/{new}")[1]["messages"]) == 2
    _send(server, new, "second, rewritten")
    server.wait_idle(new)
    argv = calls(fake_claude)[-1]["argv"]
    assert argv[argv.index("--resume") + 1] == sid and "--fork-session" in argv
    assert argv[argv.index("--resume-session-at") + 1] == msgs[1]["uuid"]
    meta = server.req("GET", f"/api/chats/{new}")[1]["chat"]
    assert meta["session_id"] != sid and not meta.get("fork")                # its own session from now on
    assert server.req("POST", f"/api/chats/{cid}/fork", {"index": 1})[0] == 400   # not from Claude's message

    _send(server, cid, "/context")
    server.wait_idle(cid)
    last = server.req("GET", f"/api/chats/{cid}")[1]["messages"][-1]
    assert [b["text"] for b in last["blocks"] if b["type"] == "text"] == ["## Context Usage\n\n**Tokens:** 40k / 200k (20%)"]


def test_chat_error_cancel_and_busy(server, fake_claude):
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    _send(server, cid, "FAIL please")
    server.wait_idle(cid)
    code, r = server.req("GET", f"/api/chats/{cid}")
    assert "模拟的错误" in (r["messages"][-1]["error"] or "")

    _send(server, cid, "SLOW please")
    time.sleep(0.4)
    code, busy = _send(server, cid, "another")
    assert code == 200 and busy["queued"] is True                     # typed while busy: waits its turn
    server.req("POST", f"/api/chats/{cid}/cancel")                    # stopping drops the queue too
    server.wait_idle(cid, timeout=10)
    code, r = server.req("GET", f"/api/chats/{cid}")
    assert r["messages"][-1]["cancelled"] is True
    assert [m.get("text") for m in r["messages"] if m["role"] == "user"][-1] == "SLOW please"


def test_polish_request_and_data_edit(server, data_file, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", json.dumps({"file": str(data_file), "from": "Solo developer", "to": "Solo developer (polished)"}))
    code, chat = server.req("POST", "/api/chats")
    code, r = _send(server, chat["id"], "更简洁", polish=True, entry_id="EXP-002")
    assert "Paper2Exam" in r["text"] and "更简洁" in r["text"] and "EXP-002" not in r["text"]
    server.wait_idle(chat["id"])
    assert "Solo developer (polished)" in data_file.read_text(encoding="utf-8")
    code, s = server.req("GET", "/api/state")
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    assert e["subtitle"]["en"] == "Solo developer (polished)"


def test_bilingual_and_one_line_actions(server, data_file, fake_claude):
    s = server.req("GET", "/api/state")[1]
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    e["bullets"].append({"en": "Designed an evaluation harness that scores every generated question against the source paper and flags hallucinated facts before export"})
    server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    send = lambda **b: server.req("POST", f"/api/chats/{cid}/send", {"version_id": "RES-TECH-EN", **b})  # noqa: E731

    code, r = send(action="translate", entry_id="EXP-002", target="zh")
    assert code == 200
    t = r["text"]
    assert "中文版本" in t and "不是逐句翻译" in t and "--lang zh" in t and "RES-TECH-EN" in t
    assert "''" in t                                                   # how to drop a bullet in one language
    server.wait_idle(cid)

    code, r = send(action="fit", entry_id="EXP-002", lang="en")
    assert code == 200 and "第 3 条" in r["text"] and "evaluation harness" in r["text"]
    server.wait_idle(cid)
    code, r = send(action="fit", entry_id="EXP-003", lang="en")
    assert code == 409                                                 # already one line each

    code, r = send(action="batch_translate", target="zh")
    assert code == 200 and "Paper2Exam" in r["text"]                   # the entry lacking Chinese is listed
    server.wait_idle(cid)
    code, r = send(action="batch_fit")
    assert code == 200 and "Paper2Exam" in r["text"]
    server.wait_idle(cid)
    msgs = server.req("GET", f"/api/chats/{cid}")[1]["messages"]
    assert [m["meta"].get("action") for m in msgs if m["role"] == "user"] == ["translate", "fit", "batch_translate", "batch_fit"]
    code, r = send(action="nope")
    assert code == 400
