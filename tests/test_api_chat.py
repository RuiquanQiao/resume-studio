"""HTTP API and the Claude Code chat bridge (with a fake `claude`)."""
from __future__ import annotations

import json
import shutil
import time

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
    assert code == 409
    server.req("POST", f"/api/chats/{cid}/cancel")
    server.wait_idle(cid, timeout=10)
    code, r = server.req("GET", f"/api/chats/{cid}")
    assert r["messages"][-1]["cancelled"] is True


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
