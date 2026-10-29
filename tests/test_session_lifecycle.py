import json

import pytest

from core.session_lifecycle import operate


def test_codex_archive_restore_delete(server, tmp_path, monkeypatch):
    agent_log = tmp_path / "agents.jsonl"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(agent_log))
    cid = server.req("POST", "/api/chats", {"agent": "codex"})[1]["id"]
    server.inst.chats.store.update_meta(cid, session_id="t-example")
    for expected in (True, False):
        code, result = server.req("POST", f"/api/chats/{cid}/archive")
        assert code == 200 and result["chat"]["archived"] is expected
    assert server.req("DELETE", f"/api/chats/{cid}")[0] == 200
    methods = [json.loads(line)["in"].get("method") for line in agent_log.read_text().splitlines() if '"in"' in line]
    assert all(m in methods for m in ("thread/archive", "thread/unarchive", "thread/delete"))


def test_failure_preserves_local_chat(server):
    cid = server.req("POST", "/api/chats", {"agent": "codex"})[1]["id"]
    server.inst.chats.store.update_meta(cid, session_id="missing")
    assert server.req("DELETE", f"/api/chats/{cid}")[0] == 409
    assert server.req("GET", f"/api/chats/{cid}")[0] == 200


def test_acp_without_capability_does_not_delete(server):
    cid = server.req("POST", "/api/chats", {"agent": "qwen"})[1]["id"]
    server.inst.chats.store.update_meta(cid, session_id="s-example")
    assert server.req("DELETE", f"/api/chats/{cid}")[0] == 409
    assert server.req("POST", f"/api/chats/{cid}/archive")[0] == 409
    assert server.req("GET", f"/api/chats/{cid}")[0] == 200


@pytest.mark.parametrize("agent,env,folder,filename", [
    ("claude", "CLAUDE_CONFIG_DIR", "projects", "abc-123.jsonl"),
    ("pi", "PI_CODING_AGENT_DIR", "sessions", "2026-01-01_abc-123.jsonl"),
])
def test_local_session_archive_restore_delete(tmp_path, monkeypatch, agent, env, folder, filename):
    monkeypatch.setenv(env, str(tmp_path))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    session = tmp_path / folder / "project" / filename
    session.parent.mkdir(parents=True)
    session.write_text('{"type":"session"}\n', encoding="utf-8")
    chat = {"agent": agent, "session_id": "abc-123"}
    operate(chat, "archive", tmp_path)
    assert not session.exists()
    operate(chat, "unarchive", tmp_path)
    assert session.exists()
    operate(chat, "delete", tmp_path)
    assert not session.exists()
