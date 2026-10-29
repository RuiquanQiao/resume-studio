"""Chats on the other agents: Codex (app-server), Pi (rpc) and the ACP ones (Qwen Code, DeepSeek Harness),
each against tests/fake_agents.py. The same chat API and cards as Claude Code; the agent is picked per chat."""
from __future__ import annotations

import json
import os
import time

import pytest

from core import agents


def test_codex_uses_provider_turn_start_time():
    from types import SimpleNamespace
    from core.chat import Turn
    from core.drivers import CodexDriver

    events = []
    runner = SimpleNamespace(_emit=lambda *a, **kw: events.append((a, kw)))
    turn = Turn("clock")
    turn.state = {"rpc": SimpleNamespace(dispatch=lambda msg: False), "thread": "main"}
    driver = CodexDriver(agents.get("codex"))
    local_start = turn.started_at
    for value in (None, 0, True, "invalid"):
        driver.handle(runner, turn, {"method": "turn/started", "params": {"threadId": "main", "turn": {"startedAt": value}}})
        assert turn.started_at == local_start
    driver.handle(runner, turn, {"method": "turn/started", "params": {"threadId": "child", "turn": {"startedAt": 123}}})
    assert turn.started_at == local_start
    driver.handle(runner, turn, {"method": "turn/started", "params": {"threadId": "main", "turn": {"startedAt": 1700000000}}})
    assert turn.started_at == 1700000000
    assert events[-1][1]["started_at"] == 1700000000


@pytest.fixture
def agent_log(tmp_path, monkeypatch):
    log = tmp_path / "agents.jsonl"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(log))
    return log


def lines(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []


def sent(log, method=None, kind=None):
    """What the app wrote to the agent (JSON-RPC methods, or Pi's command types)."""
    out = [x["in"] for x in lines(log) if "in" in x]
    if method:
        out = [m for m in out if m.get("method") == method]
    if kind:
        out = [m for m in out if m.get("type") == kind]
    return out


def _wait(cond, timeout=10, msg="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(msg)


def new_chat(server, agent, **extra):
    code, chat = server.req("POST", "/api/chats", {"agent": agent, **extra})
    assert code == 200 and chat["agent"] == agent
    return chat["id"]


def say(server, cid, text, **extra):
    code, r = server.req("POST", f"/api/chats/{cid}/send", {"text": text, **extra})
    assert code == 200, r
    return r


def reply(server, cid):
    server.wait_idle(cid)
    msgs = server.req("GET", f"/api/chats/{cid}")[1]["messages"]
    return msgs[-1]


def text(msg):
    return "".join(b["text"] for b in msg["blocks"] if b["type"] == "text")


def pending(server, cid):
    def find():
        live = server.req("GET", f"/api/chats/{cid}")[1]["live"] or {}
        return next((b for b in live.get("blocks", []) if b["type"] == "ask" and b["status"] == "pending"), None)
    return _wait(find, msg="a pending card")


def test_every_agent_is_listed_with_how_to_get_it(server):
    code, r = server.req("GET", "/api/claude?agent=codex")
    assert code == 200 and r["agent"] == "codex" and r["available"]
    ids = [a["id"] for a in r["agents"]]
    assert ids == ["claude", "codex", "qwen", "pi", "dsh"]
    for a in r["agents"]:
        assert a["installed"] and a["install"].startswith("npm install -g ") and a["login"]
    assert r["modes"] and {m["id"] for m in r["modes"]} == {"plan", "default", "acceptEdits", "auto"}


def test_codex_info_models_efforts_account(server):
    code, r = server.req("GET", "/api/claude?agent=codex&refresh=1")
    info = r["info"]
    assert [m["value"] for m in info["models"]] == ["gpt-fake-1", "gpt-fake-2"]
    assert info["models"][0]["efforts"] == ["low", "medium", "high"] and info["models"][1]["efforts"] == []
    assert info["default_model"]["resolved"] == "gpt-fake-1"
    assert info["account"]["email"] == "me@example.com" and info["signed_in"] is True
    assert r["limits"]["windows"]["seven_day"]["utilization"] == 0.05
    assert (server.inst.studio.store.cache_dir / "agent-codex.json").exists()


def test_a_missing_agent_says_how_to_install_it(server, monkeypatch):
    monkeypatch.setenv("STUDIO_PI_CMD", "")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("core.agents._WHICH", {})   # forget what earlier tests found on PATH
    r = server.req("GET", "/api/claude?agent=pi")[1]
    assert r["available"] is False
    pi = next(a for a in r["agents"] if a["id"] == "pi")
    assert not pi["installed"] and "pi-coding-agent" in pi["install"]
    cid = new_chat(server, "pi")
    code, err = server.req("POST", f"/api/chats/{cid}/send", {"text": "hi"})
    assert code == 409 and "pi" in err["error"]


def test_codex_chat_resumes_its_thread(server, agent_log, data_file):
    cid = new_chat(server, "codex", model="gpt-fake-2", effort="high", mode="acceptEdits")
    say(server, cid, "你好 codex")
    m = reply(server, cid)
    assert text(m) == "codex heard: 你好 codex" and m["uuid"].startswith("turn-")
    meta = server.req("GET", f"/api/chats/{cid}")[1]["chat"]
    assert meta["session_id"].startswith("t-") and meta["context"] == {"used": 1234, "window": 258400, "model": "gpt-fake-2"}
    start = sent(agent_log, "thread/start")[0]["params"]
    assert start["cwd"] == str(data_file.parent) and start["model"] == "gpt-fake-2"
    assert start["approvalPolicy"] == "on-request" and start["sandbox"] == "workspace-write"
    assert "resume.yaml" in start["developerInstructions"]          # the window's note goes in Codex's instructions slot
    turn = sent(agent_log, "turn/start")[0]["params"]
    assert turn["effort"] == "high" and turn["input"][0] == {"type": "text", "text": "你好 codex"}
    assert lines(agent_log)[0]["argv"][-1] == "app-server"

    say(server, cid, "again")
    assert text(reply(server, cid)) == "codex heard: again"
    resume = sent(agent_log, "thread/resume")[0]["params"]
    assert resume["threadId"] == meta["session_id"]
    assert server.req("GET", "/api/claude?agent=codex")[1]["limits"]["windows"]["five_hour"]["utilization"] == 0.12


def test_reply_start_time_resets_after_queue_wait(server):
    cid = new_chat(server, "codex")
    say(server, cid, "QUESTION")
    ask = pending(server, cid)
    initial = server.req("GET", f"/api/chats/{cid}")[1]["live"]["started_at"]
    say(server, cid, "SLOW")
    turn = server.inst.chats.turns[cid]
    turn.queue[0]["msg"]["at"] = initial - 600
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "answers": {"Which colour?": "Blue"}})
    _wait(lambda: not turn.queue, msg="queued reply started")
    live = server.req("GET", f"/api/chats/{cid}")[1]["live"]
    assert initial <= live["started_at"] <= time.time()
    assert time.time() - live["started_at"] < 5
    server.req("POST", f"/api/chats/{cid}/cancel")
    server.wait_idle(cid)


def test_codex_approval_card_and_question(server, agent_log):
    cid = new_chat(server, "codex")
    say(server, cid, "PERM please")
    ask = pending(server, cid)
    assert ask["kind"] == "permission" and ask["tool"] == "Bash" and ask["input"]["command"] == "echo hi"
    assert ask["suggestions"] == [{"type": "session"}]
    code, r = server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": True, "suggestion": 0})
    assert code == 200 and r["ask"]["status"] == "allowed"
    m = reply(server, cid)
    assert text(m).startswith('decision="acceptForSession"')
    tool = next(b for b in m["blocks"] if b["type"] == "tool")
    assert tool["name"] == "Bash" and tool["status"] == "done" and tool["hint"] == "echo hi"

    say(server, cid, "PERM again")
    ask = pending(server, cid)
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": False, "message": "use dir instead"})
    m = reply(server, cid)
    assert text(m).startswith('decision="decline"')
    steer = sent(agent_log, "turn/steer")[0]["params"]
    assert steer["input"][0]["text"] == "use dir instead"           # what to do instead reaches the running turn

    say(server, cid, "QUESTION")
    ask = pending(server, cid)
    assert ask["kind"] == "question" and ask["input"]["questions"][0]["options"][1]["label"] == "Blue"
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "answers": {"Which colour?": "Blue"}})
    assert '"colour": {"answers": ["Blue"]}' in text(reply(server, cid))


def test_codex_stop_interrupts_and_errors_show(server, agent_log):
    cid = new_chat(server, "codex")
    say(server, cid, "SLOW")
    _wait(lambda: sent(agent_log, "turn/start"), msg="turn started")
    time.sleep(0.3)
    server.req("POST", f"/api/chats/{cid}/cancel")
    m = reply(server, cid)
    assert m["cancelled"] and sent(agent_log, "turn/interrupt")

    say(server, cid, "FAIL")
    assert reply(server, cid)["error"] == "fake codex failed"

    say(server, cid, "PLANNED")
    reply(server, cid)
    tasks = server.req("GET", f"/api/chats/{cid}")[1]["chat"]["tasks"]
    assert [(x["subject"], x["status"]) for x in tasks] == [("Read", "completed"), ("Write", "in_progress")]


def test_codex_fork_at_a_message(server, agent_log):
    cid = new_chat(server, "codex")
    say(server, cid, "one")
    first = reply(server, cid)
    say(server, cid, "two")
    reply(server, cid)
    code, r = server.req("POST", f"/api/chats/{cid}/fork", {"index": 2})
    assert code == 200 and r["chat"]["agent"] == "codex" and r["text"] == "two"
    say(server, r["chat"]["id"], "two, rewritten")
    assert text(reply(server, r["chat"]["id"])) == "codex heard: two, rewritten"
    fork = sent(agent_log, "thread/fork")[0]["params"]
    assert fork["lastTurnId"] == first["uuid"]


def test_acp_agent_session_load_permission_and_learned_modes(server, agent_log):
    cid = new_chat(server, "qwen")
    say(server, cid, "hello qwen")
    m = reply(server, cid)
    assert text(m) == "acp heard: hello qwen"
    assert next(b for b in m["blocks"] if b["type"] == "thinking")["text"] == "thinking it over"
    meta = server.req("GET", f"/api/chats/{cid}")[1]["chat"]
    assert meta["session_id"].startswith("s-") and meta["model"] == "fake-max" and meta["mode_actual"] == "default"
    assert [x["status"] for x in meta["tasks"]] == ["completed", "in_progress"]
    assert lines(agent_log)[0]["argv"][-1] == "--acp"
    first = sent(agent_log, "session/prompt")[0]["params"]["prompt"][0]["text"]
    assert first.startswith("<context>") and first.endswith("hello qwen")   # the window's note rides along: ACP has no system prompt
    # what the first session said about the agent: its modes, models and commands, for the pickers
    r = server.req("GET", "/api/claude?agent=qwen")[1]
    assert [x["id"] for x in r["modes"]] == ["plan", "default", "yolo"] and r["modes"][0]["label"] == "Plan"
    assert [x["value"] for x in r["info"]["models"]] == ["fake-max", "fake-mini"]
    assert r["info"]["commands"][0]["name"] == "review"

    server.req("PUT", f"/api/chats/{cid}", {"model": "fake-mini", "mode": "yolo"})
    say(server, cid, "PERM")
    ask = pending(server, cid)
    assert sent(agent_log, "session/load")[0]["params"]["sessionId"] == meta["session_id"]
    assert sent(agent_log, "session/set_model")[0]["params"]["modelId"] == "fake-mini"
    assert sent(agent_log, "session/set_mode")[0]["params"]["modeId"] == "yolo"
    assert ask["tool"] == "Bash" and ask["input"]["command"] == "echo hi"
    assert ask["suggestions"] == [{"type": "option", "label": "Always allow echo", "optionId": "always"}]
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": True, "suggestion": 0})
    m = reply(server, cid)
    assert text(m).startswith("outcome=always") and "OLD HISTORY" not in text(m)   # the replayed history isn't shown again

    say(server, cid, "PERM no")
    ask = pending(server, cid)
    server.req("POST", f"/api/chats/{cid}/answer", {"request_id": ask["id"], "allow": False})
    assert text(reply(server, cid)).startswith("outcome=no")


def test_acp_stop_and_error(server, agent_log):
    cid = new_chat(server, "dsh")
    say(server, cid, "SLOW")
    _wait(lambda: sent(agent_log, "session/prompt"), msg="prompt sent")
    time.sleep(0.3)
    server.req("POST", f"/api/chats/{cid}/cancel")
    assert reply(server, cid)["cancelled"] and sent(agent_log, "session/cancel")
    assert lines(agent_log)[0]["argv"][-2:] == ["--profile", "acp"]
    say(server, cid, "FAIL")
    assert reply(server, cid)["error"] == "fake acp failed"
    # an ACP agent can't go back to the middle of a chat
    code, err = server.req("POST", f"/api/chats/{cid}/fork", {"index": 2})
    assert code == 409


def test_pi_chat_session_tools_and_usage(server, agent_log):
    cid = new_chat(server, "pi", model="anthropic/claude-fake", effort="minimal")
    say(server, cid, "hello pi")
    m = reply(server, cid)
    assert text(m) == "pi heard: hello pi"
    tool = next(b for b in m["blocks"] if b["type"] == "tool")
    assert tool["name"] == "Read" and tool["hint"] == "resume.yaml" and tool["status"] == "done"
    argv = lines(agent_log)[0]["argv"]
    assert argv[argv.index("--model") + 1] == "anthropic/claude-fake" and argv[argv.index("--thinking") + 1] == "minimal"
    assert "--append-system-prompt" in argv and "--session-id" in argv
    meta = server.req("GET", f"/api/chats/{cid}")[1]["chat"]
    assert meta["session_id"] == argv[argv.index("--session-id") + 1]
    assert meta["context"]["used"] == 1020 and meta["context"]["window"] == 200000

    say(server, cid, "again")
    reply(server, cid)
    argv = [x for x in lines(agent_log) if "argv" in x][-1]["argv"]
    assert argv[argv.index("--session") + 1] == meta["session_id"]

    say(server, cid, "FAIL")
    assert reply(server, cid)["error"] == "fake pi failed"
    r = server.req("GET", "/api/claude?agent=pi&refresh=1")[1]
    assert [x["value"] for x in r["info"]["models"]] == ["anthropic/claude-fake", "openai/gpt-fake"] and r["modes"] == []


def test_a_chat_keeps_its_agent(server, agent_log):
    """New chats start on the last agent picked; a chat that has talked to one agent stays with it."""
    server.req("PUT", "/api/chat-defaults", {"agent": "codex"})
    code, chat = server.req("POST", "/api/chats")
    cid = chat["id"]
    say(server, cid, "who")
    assert text(reply(server, cid)) == "codex heard: who"
    assert server.req("GET", f"/api/chats/{cid}")[1]["chat"]["agent"] == "codex"
    server.req("PUT", "/api/chat-defaults", {"agent": "claude"})
    say(server, cid, "still codex?")
    assert text(reply(server, cid)) == "codex heard: still codex?"
    code, err = server.req("PUT", f"/api/chats/{cid}", {"agent": "pi"})
    assert code == 409
    # an empty chat can switch, and drops the other agent's model / mode
    code, chat = server.req("POST", "/api/chats", {"agent": "codex", "model": "gpt-fake-1", "mode": "plan"})
    code, meta = server.req("PUT", f"/api/chats/{chat['id']}", {"agent": "qwen"})
    assert code == 200 and meta["agent"] == "qwen" and meta["model_choice"] is None and meta["mode"] is None
    assert server.req("POST", "/api/chats", {"agent": "nope"})[0] == 400


def test_signed_in_comes_from_the_tools_own_files(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_CODEX_CMD", raising=False)
    monkeypatch.delenv("STUDIO_QWEN_CMD", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(agents, "HOME", tmp_path)
    assert agents.BY_ID["codex"].signed_in() is False
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "auth.json").write_text("{}", encoding="utf-8")
    assert agents.BY_ID["codex"].signed_in() is True
    assert agents.BY_ID["qwen"].signed_in() is False
    (tmp_path / ".qwen").mkdir()
    (tmp_path / ".qwen" / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": "qwen-oauth"}}}), encoding="utf-8")
    assert agents.BY_ID["qwen"].signed_in() is True
    assert os.environ.get("STUDIO_CLAUDE_CMD") is None or agents.BY_ID["claude"].signed_in() is True
