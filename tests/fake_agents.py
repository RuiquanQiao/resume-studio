"""Stand-ins for the other agents' machine modes, used by the tests (see fake_claude.py for Claude Code).

    fake_agents.py codex app-server     Codex's JSON-RPC app-server (threads, turns, items, approvals)
    fake_agents.py pi --mode rpc ...     Pi's RPC mode (prompt in, agent events out)
    fake_agents.py acp <args...>         an Agent Client Protocol agent (Qwen Code, DeepSeek Harness…)

Each one answers a message with "<agent> heard: <text>", streamed in two pieces. Words in the message:
  PERM      ask permission to run a command first (Codex / ACP), report what the user decided
  QUESTION  ask the user to pick a colour (Codex), echo the answer
  SLOW      wait until interrupted (or 30 s)
  FAIL      fail the reply
  PLANNED   report a two-step plan
Env:
  FAKE_AGENT_LOG  append one JSON line per process (argv, cwd) and per message received: {"in": ...}
"""
import json
import os
import sys
import threading
import time
import uuid

args = sys.argv[1:]
proto = args[0]
log = os.environ.get("FAKE_AGENT_LOG")
lock = threading.Lock()


def note(obj):
    if log:
        with lock, open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def out(obj):
    with lock:
        sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.flush()


note({"proto": proto, "argv": args, "cwd": os.getcwd()})
inbox: dict = {}          # answers to our own requests, by id
interrupted = threading.Event()


def wait_answer(rid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if rid in inbox:
            return inbox.pop(rid)
        if interrupted.is_set():
            return None
        time.sleep(0.02)
    return None


def text_of(blocks):
    return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")


# ============================================================ Codex app-server
def codex():
    threads: dict[str, list[str]] = {}

    def turn(tid, turn_id, text):
        n = lambda m, p: out({"method": m, "params": {"threadId": tid, "turnId": turn_id, **p}})   # noqa: E731
        out({"method": "turn/started", "params": {"threadId": tid, "turn": {"id": turn_id, "status": "inProgress"}}})
        answer = ""
        if "PERM" in text:
            item = {"type": "commandExecution", "id": "exec-1", "command": "echo hi", "commandActions": [{"type": "unknown", "command": "echo hi"}], "status": "inProgress"}
            n("item/started", {"item": item})
            out({"method": "item/commandExecution/requestApproval", "id": 900, "params": {"threadId": tid, "turnId": turn_id, "itemId": "exec-1",
                                                                                         "command": "echo hi", "reason": "to say hi", "startedAtMs": 0}})
            got = wait_answer(900) or {}
            decision = (got.get("result") or {}).get("decision")
            answer = f"decision={json.dumps(decision)} "
            n("item/completed", {"item": {**item, "status": "completed" if decision != "decline" else "declined", "aggregatedOutput": "hi\n", "exitCode": 0}})
        if "QUESTION" in text:
            out({"method": "item/tool/requestUserInput", "id": 901, "params": {"threadId": tid, "turnId": turn_id, "itemId": "q-1", "isBlocking": True,
                 "questions": [{"id": "colour", "header": "Colour", "question": "Which colour?", "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]}})
            got = wait_answer(901) or {}
            answer = f"answers={json.dumps((got.get('result') or {}).get('answers'), ensure_ascii=False)} "
        if "PLANNED" in text:
            n("turn/plan/updated", {"plan": [{"step": "Read", "status": "completed"}, {"step": "Write", "status": "inProgress"}]})
        if "SLOW" in text:
            interrupted.wait(30)
            n("turn/completed", {"turn": {"id": turn_id, "status": "interrupted"}})
            return
        if "FAIL" in text:
            out({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "status": "failed", "error": {"message": "fake codex failed"}}}})
            return
        msg = f"codex heard: {text}"
        item = {"type": "agentMessage", "id": "msg-" + turn_id, "text": ""}
        n("item/started", {"item": item})
        for part in (answer + msg[:6], msg[6:]):
            n("item/agentMessage/delta", {"itemId": item["id"], "delta": part})
        n("item/completed", {"item": {**item, "text": answer + msg}})
        n("thread/tokenUsage/updated", {"tokenUsage": {"last": {"inputTokens": 1200, "outputTokens": 34}, "modelContextWindow": 258400}})
        out({"method": "account/rateLimits/updated", "params": {"rateLimits": {"primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1900000000}}}})
        threads[tid].append(turn_id)
        out({"method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "status": "completed"}}})

    for raw in sys.stdin.buffer:
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        note({"in": m})
        if "method" not in m:
            inbox[m.get("id")] = m
            continue
        method, p, rid = m["method"], m.get("params") or {}, m.get("id")
        if method == "initialize":
            out({"id": rid, "result": {"userAgent": "fake-codex"}})
        elif method in ("thread/archive", "thread/unarchive", "thread/delete"):
            if p.get("threadId") == "missing":
                out({"id": rid, "error": {"code": -32600, "message": "no such thread"}})
            else:
                out({"id": rid, "result": {}})
        elif method == "account/read":
            out({"id": rid, "result": {"account": {"type": "chatgpt", "email": "me@example.com", "planType": "plus"}, "requiresOpenaiAuth": True}})
        elif method == "model/list":
            out({"id": rid, "result": {"data": [
                {"id": "gpt-fake-1", "displayName": "GPT-Fake-1", "description": "Fast", "isDefault": True,
                 "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}]},
                {"id": "gpt-fake-2", "displayName": "GPT-Fake-2", "description": "Careful", "isDefault": False, "supportedReasoningEfforts": []}]}})
        elif method == "account/rateLimits/read":
            out({"id": rid, "result": {"rateLimits": {"primary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 1900000000}}}})
        elif method in ("thread/start", "thread/resume", "thread/fork"):
            if method == "thread/resume" and p.get("threadId") not in threads and not p["threadId"].startswith("t-"):
                out({"id": rid, "error": {"code": -32600, "message": "no such thread"}})
                continue
            tid = p["threadId"] if method == "thread/resume" else "t-" + uuid.uuid4().hex[:8]
            threads.setdefault(tid, [])
            out({"id": rid, "result": {"thread": {"id": tid}, "model": p.get("model") or "gpt-fake-1"}})
            out({"method": "thread/started", "params": {"thread": {"id": tid}}})
        elif method == "turn/start":
            turn_id = "turn-" + uuid.uuid4().hex[:6]
            out({"id": rid, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
            interrupted.clear()
            threading.Thread(target=turn, args=(p["threadId"], turn_id, text_of(p.get("input") or [])), daemon=True).start()
        elif method == "turn/interrupt":
            interrupted.set()
            out({"id": rid, "result": {}})
        elif rid is not None:
            out({"id": rid, "result": {}})


# ============================================================ Pi rpc
def pi():
    sid = args[args.index("--session") + 1] if "--session" in args else args[args.index("--session-id") + 1] if "--session-id" in args else None
    for raw in sys.stdin.buffer:
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        note({"in": m})
        kind = m.get("type")
        if kind == "get_state":
            out({"id": m.get("id"), "type": "response", "command": "get_state", "success": True,
                 "data": {"model": {"provider": "anthropic", "id": "claude-fake", "name": "Claude Fake", "contextWindow": 200000}, "sessionId": sid}})
        elif kind == "get_available_models":
            out({"id": m.get("id"), "type": "response", "command": "get_available_models", "success": True,
                 "data": {"models": [{"provider": "anthropic", "id": "claude-fake", "name": "Claude Fake", "reasoning": True},
                                     {"provider": "openai", "id": "gpt-fake", "name": "GPT Fake", "reasoning": False}]}})
        elif kind == "abort":
            interrupted.set()
            out({"type": "response", "command": "abort", "success": True})
        elif kind == "prompt":
            out({"id": m.get("id"), "type": "response", "command": "prompt", "success": True})
            text = m.get("message") or ""
            threading.Thread(target=pi_reply, args=(text,), daemon=True).start()


def pi_reply(text):
    out({"type": "agent_start"})
    out({"type": "tool_execution_start", "toolCallId": "c1", "toolName": "read", "args": {"path": "resume.yaml"}})
    out({"type": "tool_execution_end", "toolCallId": "c1", "toolName": "read", "result": {"content": [{"type": "text", "text": "profile: ..."}]}, "isError": False})
    if "SLOW" in text:
        interrupted.wait(30)
        out({"type": "agent_end", "messages": []})
        return
    msg = f"pi heard: {text}"
    for part in (msg[:4], msg[4:]):
        out({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": part}})
    out({"type": "message_end", "message": {"role": "assistant", "usage": {"input": 900, "output": 20, "cacheRead": 100, "cacheWrite": 0},
                                            **({"stopReason": "error", "errorMessage": "fake pi failed"} if "FAIL" in text else {})}})
    out({"type": "agent_end", "messages": []})


# ============================================================ ACP
def acp():
    sessions: set[str] = set()
    for raw in sys.stdin.buffer:
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        note({"in": m})
        if "method" not in m:
            inbox[m.get("id")] = m
            continue
        method, p, rid = m["method"], m.get("params") or {}, m.get("id")
        if method == "initialize":
            out({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True}},
                                                         "authMethods": [{"id": "key", "name": "API key"}], "agentInfo": {"name": "fake-acp", "title": "Fake ACP", "version": "0.1"}}})
        elif method in ("session/new", "session/load"):
            sid = p.get("sessionId") or "s-" + uuid.uuid4().hex[:8]
            if method == "session/load":   # the history is replayed before the answer
                out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD HISTORY"}}}})
            sessions.add(sid)
            out({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid,
                 "modes": {"currentModeId": "default", "availableModes": [{"id": "plan", "name": "Plan", "description": "Read only"}, {"id": "default", "name": "Default", "description": "Asks first"}, {"id": "yolo", "name": "YOLO"}]},
                 "models": {"currentModelId": "fake-max", "availableModels": [{"modelId": "fake-max", "name": "Fake Max"}, {"modelId": "fake-mini", "name": "Fake Mini"}]}}})
        elif method in ("session/set_mode", "session/set_model"):
            out({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/cancel":
            interrupted.set()
        elif method == "session/prompt":
            interrupted.clear()
            threading.Thread(target=acp_reply, args=(rid, p["sessionId"], p.get("prompt") or []), daemon=True).start()
        elif rid is not None:
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})


def acp_reply(rid, sid, prompt):
    up = lambda u: out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})   # noqa: E731
    text = text_of(prompt)
    shown = text.split("</context>")[-1].strip()
    up({"sessionUpdate": "available_commands_update", "availableCommands": [{"name": "review", "description": "Review the changes"}]})
    answer = ""
    if "PERM" in text:
        up({"sessionUpdate": "tool_call", "toolCallId": "tc1", "title": "Run echo hi", "kind": "execute", "status": "pending", "rawInput": {"command": "echo hi"}})
        out({"jsonrpc": "2.0", "id": 700, "method": "session/request_permission", "params": {"sessionId": sid, "toolCall": {"toolCallId": "tc1", "title": "Run echo hi", "kind": "execute", "rawInput": {"command": "echo hi"}},
             "options": [{"optionId": "once", "name": "Allow once", "kind": "allow_once"}, {"optionId": "always", "name": "Always allow echo", "kind": "allow_always"},
                         {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
        got = wait_answer(700) or {}
        outcome = (got.get("result") or {}).get("outcome") or {}
        answer = f"outcome={outcome.get('optionId') or outcome.get('outcome')} "
        up({"sessionUpdate": "tool_call_update", "toolCallId": "tc1", "status": "completed" if outcome.get("optionId") in ("once", "always") else "failed",
            "content": [{"type": "content", "content": {"type": "text", "text": "hi"}}]})
    if "SLOW" in text:
        interrupted.wait(30)
        out({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
        return
    if "FAIL" in text:
        out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "fake acp failed"}})
        return
    up({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "thinking it over"}})
    up({"sessionUpdate": "plan", "entries": [{"content": "Look", "status": "completed"}, {"content": "Answer", "status": "in_progress"}]})
    msg = f"acp heard: {shown}"
    for part in (answer + msg[:4], msg[4:]):
        up({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": part}})
    out({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


{"codex": codex, "pi": pi, "acp": acp}[proto]()
