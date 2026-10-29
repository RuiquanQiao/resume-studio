import json

import pytest

from core.session_lifecycle import operate


def _methods(log):
    return [json.loads(line)["in"].get("method") for line in log.read_text().splitlines() if '"in"' in line]


def test_codex_archive_restore_delete(server, tmp_path, monkeypatch):
    agent_log = tmp_path / "agents.jsonl"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(agent_log))
    cid = server.req("POST", "/api/chats", {"agent": "codex"})[1]["id"]
    server.inst.chats.store.update_meta(cid, session_id="t-example")
    for expected in (True, False):
        code, result = server.req("POST", f"/api/chats/{cid}/archive")
        assert code == 200 and result["chat"]["archived"] is expected
    assert server.req("DELETE", f"/api/chats/{cid}")[0] == 200
    assert all(m in _methods(agent_log) for m in ("thread/archive", "thread/unarchive", "thread/delete"))


def test_codex_failure_preserves_local_chat(server):
    cid = server.req("POST", "/api/chats", {"agent": "codex"})[1]["id"]
    server.inst.chats.store.update_meta(cid, session_id="missing")
    assert server.req("DELETE", f"/api/chats/{cid}")[0] == 409
    assert server.req("POST", f"/api/chats/{cid}/archive")[0] == 409
    assert server.req("GET", f"/api/chats/{cid}")[0] == 200


def test_acp_without_delete_keeps_its_copy_and_says_so(server, tmp_path, monkeypatch):
    agent_log = tmp_path / "agents.jsonl"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(agent_log))
    cid = server.req("POST", "/api/chats", {"agent": "qwen"})[1]["id"]
    server.inst.chats.store.update_meta(cid, session_id="s-example")
    assert server.req("POST", f"/api/chats/{cid}/archive")[1]["chat"]["archived"] is True   # this window only
    code, result = server.req("DELETE", f"/api/chats/{cid}")
    assert code == 200 and "Qwen Code" in result["note"]
    assert "session/delete" not in _methods(agent_log)
    assert server.req("GET", f"/api/chats/{cid}")[0] == 404


@pytest.mark.parametrize("agent,env,folder,filename", [
    ("claude", "CLAUDE_CONFIG_DIR", "projects", "abc-123.jsonl"),
    ("pi", "PI_CODING_AGENT_DIR", "sessions", "2026-01-01_abc-123.jsonl"),
])
def test_local_session_archive_leaves_files_delete_removes_them(tmp_path, monkeypatch, agent, env, folder, filename):
    monkeypatch.setenv(env, str(tmp_path))
    monkeypatch.delenv("PI_CODING_AGENT_SESSION_DIR", raising=False)
    session = tmp_path / folder / "project" / filename
    session.parent.mkdir(parents=True)
    session.write_text('{"type":"session"}\n', encoding="utf-8")
    extra = session.with_suffix("")
    if agent == "claude":
        (extra / "tool-results").mkdir(parents=True)
    chat = {"agent": agent, "session_id": "abc-123"}
    operate(chat, "archive", tmp_path)
    operate(chat, "unarchive", tmp_path)
    assert session.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [folder]   # no folder of our own next to the agent's
    operate(chat, "delete", tmp_path)
    assert not session.exists() and not extra.exists()
