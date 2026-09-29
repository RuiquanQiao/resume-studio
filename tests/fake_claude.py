"""Stand-in for `claude -p ... --output-format stream-json` used by the tests.

Speaks the same stream-json dialect as Claude Code: system/init, stream_event
text deltas, assistant tool_use, user tool_result, the full assistant message
(which duplicates the streamed text, like the real CLI) and a final result.

Behaviour switches, read from the prompt text:
  FAIL  -> report an error result
  SLOW  -> sleep so the test can cancel
Env:
  FAKE_CLAUDE_LOG   append one JSON line per call: argv, stdin, cwd
  FAKE_CLAUDE_EDIT  JSON {"file","from","to"}: apply this edit to the data file
"""
import json
import os
import sys
import time
import uuid

args = sys.argv[1:]
prompt = sys.stdin.buffer.read().decode("utf-8")
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": args, "stdin": prompt, "cwd": os.getcwd()}, ensure_ascii=False) + "\n")

sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())


def out(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.flush()


out({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-model"})
if "FAIL" in prompt:
    out({"type": "result", "is_error": True, "result": "模拟的错误", "session_id": sid})
    sys.exit(1)
if "SLOW" in prompt:
    time.sleep(30)

out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
     "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Read",
                              "input": {"file_path": "E:/data/resume.yaml"}}]}})
out({"type": "user", "session_id": sid,
     "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "file contents here", "is_error": False}]}})

edit = os.environ.get("FAKE_CLAUDE_EDIT")
if edit:
    e = json.loads(edit)
    with open(e["file"], encoding="utf-8") as f:
        text = f.read()
    with open(e["file"], "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace(e["from"], e["to"]))
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
         "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Edit", "input": {"file_path": e["file"]}}]}})
    out({"type": "user", "session_id": sid,
         "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "ok", "is_error": False}]}})

reply = "好的，我看过了。**已经处理**：\n\n- 第一点\n- 第二点"
for i in range(0, len(reply), 5):
    out({"type": "stream_event", "parent_tool_use_id": None, "session_id": sid,
         "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply[i:i + 5]}}})
    time.sleep(0.01)
out({"type": "assistant", "parent_tool_use_id": None, "session_id": sid,
     "message": {"content": [{"type": "text", "text": reply}]}})
out({"type": "result", "is_error": False, "result": reply, "session_id": sid, "total_cost_usd": 0.001})
