"""Stand-in for `claude -p --input-format stream-json --output-format stream-json` used by the tests.

Speaks Claude Code's SDK channel both ways: control requests (initialize, interrupt,
set_model, set_permission_mode, and can_use_tool going the other way), user messages
in, system/init, stream_event text deltas, assistant tool_use, user tool_result, the full
assistant message (duplicating the streamed text, like the real CLI), rate limits and a
final result per message. Several messages can arrive on one stdin (the queue).

Behaviour switches, read from the message text:
  FAIL   -> report an error result and exit
  SLOW   -> wait until interrupted (or 30 s)
  PERM   -> ask permission to Write a file, report what the user decided
  QUESTION -> AskUserQuestion (colour: Red / Blue), echo the answer
  PLAN   -> ExitPlanMode with a two-step plan, echo approval or feedback
  TASKS  -> TaskCreate x2, TaskUpdate the first to completed
  /context -> answer like a local slash command (synthetic message, no streaming)
Env:
  FAKE_CLAUDE_LOG   append one JSON line per user message: argv, stdin (text), content types, cwd
                    control requests from the app go to <log>.ctl
  FAKE_CLAUDE_EDIT  JSON {"file","from","to"}: apply this edit to the data file
"""
import json
import os
import queue
import sys
import threading
import time
import uuid

args = sys.argv[1:]
log = os.environ.get("FAKE_CLAUDE_LOG")
sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
if "--fork-session" in args:
    sid = str(uuid.uuid4())
model = args[args.index("--model") + 1] if "--model" in args else "fake-model"
mode = args[args.index("--permission-mode") + 1] if "--permission-mode" in args else "acceptEdits"
lines: "queue.Queue[dict | None]" = queue.Queue()
interrupted = threading.Event()


def out(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.flush()


def note(path_suffix, obj):
    if log:
        with open(log + path_suffix, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


INIT = {
    "commands": [
        {"name": "compact", "description": "Clear conversation history but keep a summary in context", "argumentHint": "<optional custom summarization instructions>"},
        {"name": "context", "description": "Visualize current context usage as a colored grid", "argumentHint": ""},
        {"name": "doctor", "description": "Diagnose and verify your Claude Code installation and settings", "argumentHint": ""},
        {"name": "resume-studio", "description": "带图形界面的简历工具。 (user)", "argumentHint": ""},
        {"name": "offer-toolkit-skill", "description": "求职工具包 (user)", "argumentHint": ""},
    ],
    "agents": [{"name": "Explore", "description": "Fast read-only search agent"}],
    "output_style": "default", "available_output_styles": ["default", "Explanatory"],
    "models": [
        {"value": "default", "resolvedModel": "claude-sonnet-5", "displayName": "Default (recommended)", "description": "Sonnet 5 · Efficient for routine tasks", "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsAutoMode": True},
        {"value": "sonnet", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet", "description": "Sonnet 5 · Efficient for routine tasks", "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsAutoMode": True},
        {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1", "displayName": "Fable", "description": "Fable 5.1 · Most capable for your hardest and longest-running tasks · Requires usage credits", "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsAutoMode": True},
        {"value": "opus", "resolvedModel": "claude-opus-5", "displayName": "Opus", "description": "Opus 5 · Best for everyday, complex tasks · ~2× usage vs Sonnet", "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsAutoMode": True},
        {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku", "description": "Haiku 4.5 · Fastest for quick answers"},
    ],
    "account": {"email": "test@example.com", "subscriptionType": "Claude Pro", "apiProvider": "firstParty"},
    "current_permission_mode": "acceptEdits",
}


def reader():
    for raw in sys.stdin.buffer:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("type") == "control_request":
            req = msg.get("request") or {}
            note(".ctl", req)
            if req.get("subtype") == "interrupt":
                interrupted.set()
            body = INIT if req.get("subtype") == "initialize" else {}
            out({"type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"), "response": body}})
            continue
        lines.put(msg)
    lines.put(None)


threading.Thread(target=reader, daemon=True).start()


def wait_response(request_id):
    """Block until the app answers our can_use_tool request."""
    while True:
        msg = lines.get()
        if msg is None or interrupted.is_set():
            return None
        if msg.get("type") == "control_response" and (msg.get("response") or {}).get("request_id") == request_id:
            note(".ctl", {"answer": msg["response"].get("response")})
            return msg["response"].get("response") or {}
        lines.put(msg)          # a queued user message: keep it for later
        time.sleep(0.02)


def ask(tool, inp, tool_use_id):
    rid = str(uuid.uuid4())
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid, "uuid": str(uuid.uuid4()),
         "message": {"model": model, "content": [{"type": "tool_use", "id": tool_use_id, "name": tool, "input": inp}]}})
    out({"type": "control_request", "request_id": rid, "request": {
        "subtype": "can_use_tool", "tool_name": tool, "display_name": tool, "input": inp, "tool_use_id": tool_use_id,
        "description": inp.get("file_path", ""),
        "permission_suggestions": [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}] if tool == "Write" else []}})
    return wait_response(rid)


def tool_result(tool_use_id, content, is_error=False, extra=None):
    out({"type": "user", "session_id": sid, **(extra or {}),
         "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}]}})


def reply(text):
    for i in range(0, len(text), 5):
        out({"type": "stream_event", "parent_tool_use_id": None, "session_id": sid,
             "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + 5]}}})
        time.sleep(0.01)
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid, "uuid": str(uuid.uuid4()),
         "message": {"model": model, "content": [{"type": "text", "text": text}],
                     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 38000, "output_tokens": 120}}})
    out({"type": "rate_limit_event", "session_id": sid, "rate_limit_info": {
        "status": "allowed", "rateLimitType": "five_hour", "resetsAt": int(time.time()) + 7200,
        "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": int(time.time()) + 7200},
                           "seven_day": {"utilization": 0.34, "resetsAt": int(time.time()) + 86400 * 3}}}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid, "total_cost_usd": 0.001,
         "modelUsage": {model: {"contextWindow": 200000}}})


def handle(prompt, content):
    if prompt.strip() == "/context":
        text = "## Context Usage\n\n**Tokens:** 40k / 200k (20%)"
        out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid, "uuid": str(uuid.uuid4()),
             "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 0, "output_tokens": 0}}})
        out({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid})
        return True
    if "FAIL" in prompt:
        out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "模拟的错误", "session_id": sid})
        return False
    if "TASKS" in prompt:
        for i, name in enumerate(["读取数据", "改写要点"], 1):
            out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
                 "message": {"model": model, "content": [{"type": "tool_use", "id": f"toolu_t{i}", "name": "TaskCreate", "input": {"subject": name, "description": name}}]}})
            tool_result(f"toolu_t{i}", f"Task #{i} created successfully: {name}", extra={"tool_use_result": {"task": {"id": str(i), "subject": name}}})
        out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
             "message": {"model": model, "content": [{"type": "tool_use", "id": "toolu_t9", "name": "TaskUpdate", "input": {"taskId": "1", "status": "completed"}}]}})
        tool_result("toolu_t9", "Updated task #1 status")

    if "SLOW" in prompt:
        out({"type": "stream_event", "parent_tool_use_id": None, "session_id": sid,
             "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "开始处理…"}}})
        end = time.time() + 30
        while time.time() < end and not interrupted.is_set():
            time.sleep(0.05)
        if interrupted.is_set():
            out({"type": "user", "session_id": sid, "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}})
            return False
    if "PERM" in prompt:
        ans = ask("Write", {"file_path": "E:/data/notes.md", "content": "hello\nworld"}, "toolu_w")
        if ans is None:
            return False
        ok = ans.get("behavior") == "allow"
        tool_result("toolu_w", "File created" if ok else ans.get("message", "denied"), is_error=not ok)
        reply("已写入。" if ok else "好的，不写了。")
        return True
    if "QUESTION" in prompt:
        q = {"questions": [{"question": "你喜欢哪个颜色？", "header": "颜色", "multiSelect": False,
                            "options": [{"label": "Red", "description": "红色"}, {"label": "Blue", "description": "蓝色"}]}]}
        ans = ask("AskUserQuestion", q, "toolu_q")
        if ans is None:
            return False
        answers = (ans.get("updatedInput") or {}).get("answers") or {}
        tool_result("toolu_q", f"answers: {answers}", is_error=ans.get("behavior") != "allow")
        reply(f"你选了 {answers.get('你喜欢哪个颜色？', '（没回答）')}。")
        return True
    if "PLAN" in prompt:
        ans = ask("ExitPlanMode", {"plan": "## 计划\n\n1. 读 resume.yaml\n2. 改第一条要点"}, "toolu_p")
        if ans is None:
            return False
        ok = ans.get("behavior") == "allow"
        tool_result("toolu_p", "approved" if ok else ans.get("message", ""), is_error=not ok)
        reply("开始执行计划。" if ok else "好的，我再改改计划：" + ans.get("message", ""))
        return True
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
         "message": {"model": model, "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read",
                                                  "input": {"file_path": "E:/data/resume.yaml"}}]}})
    tool_result("toolu_1", "file contents here")
    edit = os.environ.get("FAKE_CLAUDE_EDIT")
    if edit:
        e = json.loads(edit)
        with open(e["file"], encoding="utf-8") as f:
            text = f.read()
        with open(e["file"], "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace(e["from"], e["to"]))
        out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
             "message": {"model": model, "content": [{"type": "tool_use", "id": "toolu_2", "name": "Edit", "input": {"file_path": e["file"]}}]}})
        tool_result("toolu_2", "ok")
    reply("好的，我看过了。**已经处理**：\n\n- 第一点\n- 第二点")
    return True


out({"type": "system", "subtype": "init", "session_id": sid, "model": model, "permissionMode": mode})
while True:
    msg = lines.get()
    if msg is None:
        break
    if msg.get("type") != "user":
        continue
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, list):
        prompt = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        types = [b.get("type") for b in content]
    else:
        prompt, types = str(content or ""), ["text"]
    note("", {"argv": args, "stdin": prompt, "content": types, "cwd": os.getcwd()})
    interrupted.clear()
    if not handle(prompt, content):
        sys.exit(1)
