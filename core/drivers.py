"""How each agent's command line runs one reply (see core/agents.py for the agents themselves).

ChatRunner (core/chat.py) owns the transcript, the queue, the cards and the events; a driver
starts the agent's process, speaks its protocol, and turns what comes back into the same
blocks the UI already draws: text, thinking, tool steps, permission / question cards, the
task list, context use. One process per reply (plus whatever was queued behind it), and the
agent's own session id is kept on the chat so the next reply resumes it: the history lives in
the agent, exactly as with Claude Code.

  ClaudeDriver  `claude -p` stream-json in and out (Claude Code's SDK channel)
  CodexDriver   `codex app-server`: JSON-RPC (threads, turns, items, approvals)
  PiDriver      `pi --mode rpc`: JSON commands in, agent events out
  AcpDriver     the Agent Client Protocol: JSON-RPC (initialize, session/new|load, session/prompt)

Every driver leaves the user's own setup alone: their config, instructions files, skills and
login load exactly as in their terminal. The only addition is the short note saying what the
window shows, in whatever slot the protocol has for it.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .agents import Agent
from .chat import MODES, Turn, _result_preview, _small_input, _tool_hint, user_content
from .claude_info import fetch as claude_fetch
from .i18n import t

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now() -> float:
    return round(time.time(), 3)


class Driver:
    """One agent protocol. `run` is the body of the reply's thread and always ends in runner._finish."""

    def __init__(self, agent: Agent):
        self.agent = agent

    # ---- what ChatRunner calls -------------------------------------------
    def run(self, runner, turn: Turn, spec: dict) -> None:
        cost = None
        try:
            turn.proc = subprocess.Popen(self.command(runner, turn, spec), cwd=str(spec["cwd"]), env=spec.get("env"),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=NO_WINDOW)
            assert turn.proc.stdout and turn.proc.stderr
            stderr_chunks: list[bytes] = []
            threading.Thread(target=lambda: stderr_chunks.append(turn.proc.stderr.read()), daemon=True).start()
            self.begin(runner, turn, spec)
            for raw in turn.proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                res = self.handle(runner, turn, msg)
                if res is not None:
                    cost = res.get("cost", cost)
                    self.reply_done(runner, turn, res.get("error"), cost)
            code = turn.proc.wait()
            time.sleep(0.05)
            err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            if code != 0 and not turn.error and not turn.cancelled and not turn.flushed_any:
                turn.error = err_text[-2000:] or t("{cmd} exited with code {code}", cmd=self.agent.exe, code=code)
        except OSError as e:
            turn.error = t("Couldn't start {cmd}: {error}", cmd=self.agent.exe, error=e)
        finally:
            runner._finish(turn, cost)

    def reply_done(self, runner, turn: Turn, error: str | None, cost: Any = None) -> None:
        """One reply is complete: save it, then send what was queued behind it or let the process end."""
        runner._flush(turn, error, cost)
        if not runner._next(turn):
            self.close(turn)

    def close(self, turn: Turn) -> None:
        """No more messages: end the process (closing stdin ends most; the rest are stopped)."""
        turn.close()
        threading.Timer(3.0, runner_kill, args=(turn,)).start()

    def send(self, runner, turn: Turn, prompt: str, images: list[dict]) -> bool:
        raise NotImplementedError

    def answer(self, runner, turn: Turn, ask: dict, decision: dict) -> str | None:
        """Write the user's answer to a card; set ask["status"]. Returns a new permission mode, if any."""
        raise NotImplementedError

    def interrupt(self, turn: Turn) -> bool:
        return False

    def set_live(self, turn: Turn, model: str | None, mode: str | None) -> None:
        pass

    # ---- what each protocol fills in --------------------------------------
    def command(self, runner, turn: Turn, spec: dict) -> list[str]:
        raise NotImplementedError

    def begin(self, runner, turn: Turn, spec: dict) -> None:
        raise NotImplementedError

    def handle(self, runner, turn: Turn, msg: dict) -> dict | None:
        """One line of output. A dict ({"error", "cost"}) when a reply is over."""
        raise NotImplementedError


def runner_kill(turn: Turn) -> None:
    if not turn.proc or turn.proc.poll() is not None:
        return
    if os.name == "nt":   # the command may be a .cmd shim on Windows; end the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(turn.proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        turn.proc.terminate()


def _context_file(runner, turn: Turn, context: str) -> str | None:
    if not context.strip():
        return None
    path = runner.store.root / f"{turn.chat_id}.context.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(context, encoding="utf-8")
    return str(path)


def _tool(runner, turn: Turn, tid: str, name: str, inp: dict, hint: str = "") -> dict:
    tool = {"type": "tool", "id": tid, "name": name, "hint": (hint or _tool_hint(name, inp))[:160], "status": "running",
            "result": "", "input": _small_input(name, inp)}
    turn.tools[tid] = tool
    turn.blocks.append(tool)
    runner._emit("chat.tool", turn.chat_id, tool=tool)
    return tool


def _tool_end(runner, turn: Turn, tid: str, ok: bool, result: Any = "") -> None:
    tool = turn.tools.get(tid)
    if not tool:
        return
    tool["status"] = "done" if ok else "error"
    tool["result"] = _result_preview(result)
    runner._emit("chat.tool", turn.chat_id, tool=tool)


def _new_ask(runner, turn: Turn, rid: Any, kind: str, tool: str, inp: dict, **extra: Any) -> dict:
    ask = {"type": "ask", "id": str(rid), "kind": kind, "tool": tool, "input": inp, "hint": _tool_hint(tool, inp),
           "desc": "", "suggestions": [], "tool_use_id": None, "status": "pending", **extra}
    turn.asks[ask["id"]] = ask
    turn.blocks.append(ask)
    runner._emit("chat.ask", turn.chat_id, ask=ask)
    return ask


def _set_tasks(runner, turn: Turn, tasks: list[dict]) -> None:
    runner.store.update_meta(turn.chat_id, touch=False, tasks=tasks)
    runner._emit("chat.tasks", turn.chat_id, tasks=tasks)


# =========================================================================== Claude Code
class ClaudeDriver(Driver):
    """`claude -p` with stream-json in AND out, the channel Claude Code's own SDK uses."""

    def command(self, runner, turn, spec):
        chat, ws = spec["chat"], spec.get("workspace")
        cmd = spec["prefix"] + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                                "--include-partial-messages", "--permission-prompt-tool", "stdio",
                                "--allowedTools", ",".join(dict.fromkeys(runner.tools + (ws.tools if ws else [])))]
        if chat.get("mode") in MODES:
            cmd += ["--permission-mode", chat["mode"]]
        if chat.get("model_choice"):
            cmd += ["--model", chat["model_choice"]]
        if chat.get("effort"):
            cmd += ["--effort", chat["effort"]]
        fork = chat.get("fork") or {}
        if chat.get("session_id"):
            cmd += ["--resume", chat["session_id"]]
        elif fork.get("session_id"):
            cmd += ["--resume", fork["session_id"], "--fork-session"]
            if fork.get("at"):
                cmd += ["--resume-session-at", fork["at"]]
        ctx = _context_file(runner, turn, spec["context"])
        if ctx:
            cmd += ["--append-system-prompt-file", ctx]
        for d in spec["add_dirs"]:
            cmd += ["--add-dir", d]
        return cmd

    def close(self, turn):
        turn.close()   # Claude Code ends by itself once its stdin closes

    def begin(self, runner, turn, spec):
        turn.write({"type": "user", "message": {"role": "user", "content": user_content(spec["prompt"], spec["images"])}})

    def send(self, runner, turn, prompt, images):
        return turn.write({"type": "user", "message": {"role": "user", "content": user_content(prompt, images)}})

    def interrupt(self, turn):
        return turn.write({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}})

    def set_live(self, turn, model, mode):
        if model is not None:
            turn.write({"type": "control_request", "request_id": uuid.uuid4().hex,
                        "request": {"subtype": "set_model", "model": model or "default"}})
        if mode is not None:
            turn.write({"type": "control_request", "request_id": uuid.uuid4().hex,
                        "request": {"subtype": "set_permission_mode", "mode": mode}})

    def answer(self, runner, turn, ask, decision):
        kind, inp, new_mode = ask["kind"], ask["input"], None
        if kind == "question":
            answers = {str(k): str(v) for k, v in (decision.get("answers") or {}).items()}
            if decision.get("skip"):
                resp = {"behavior": "deny", "message": "The user skipped this question without answering."}
                ask["status"] = "denied"
            else:
                resp = {"behavior": "allow", "updatedInput": {**inp, "answers": answers}}
                ask["status"], ask["answers"] = "answered", answers
        elif kind == "plan":
            if decision.get("approve"):
                new_mode = decision.get("mode") if decision.get("mode") in ("acceptEdits", "default", "auto") else "default"
                resp = {"behavior": "allow", "updatedInput": inp,
                        "updatedPermissions": [{"type": "setMode", "mode": new_mode, "destination": "session"}]}
                ask["status"], ask["mode"] = "approved", new_mode
            else:
                feedback = str(decision.get("feedback") or "").strip()
                resp = {"behavior": "deny", "message": "The user wants to keep refining the plan; don't carry it out yet." + (f" Their feedback: {feedback}" if feedback else "")}
                ask["status"], ask["feedback"] = "revise", feedback
        else:
            if decision.get("allow"):
                resp = {"behavior": "allow", "updatedInput": inp}
                sug = ask.get("suggestions") or []
                pick = decision.get("suggestion")
                if isinstance(pick, int) and 0 <= pick < len(sug):
                    resp["updatedPermissions"] = [sug[pick]]
                    if sug[pick].get("type") == "setMode":
                        new_mode = sug[pick].get("mode")
                    ask["always"] = pick
                ask["status"] = "allowed"
            else:
                msg = str(decision.get("message") or "").strip()
                resp = {"behavior": "deny", "message": msg or "The user declined this action."}
                ask["status"] = "denied"
        turn.write({"type": "control_response", "response": {"subtype": "success", "request_id": ask["id"], "response": resp}})
        return new_mode

    def _ask(self, runner, turn, msg):
        req = msg.get("request") or {}
        rid = msg.get("request_id")
        if req.get("subtype") != "can_use_tool":
            turn.write({"type": "control_response", "response": {"subtype": "error", "request_id": rid,
                                                                 "error": f"Resume Studio does not support {req.get('subtype')}"}})
            return
        name = req.get("tool_name") or ""
        kind = "question" if name == "AskUserQuestion" else "plan" if name == "ExitPlanMode" else "permission"
        ask = {"type": "ask", "id": rid, "kind": kind, "tool": name, "input": req.get("input") or {},
               "hint": _tool_hint(name, req.get("input") or {}), "desc": req.get("description") or "",
               "suggestions": req.get("permission_suggestions") or [], "tool_use_id": req.get("tool_use_id"),
               "status": "pending"}
        turn.asks[rid] = ask
        turn.blocks.append(ask)
        runner._emit("chat.ask", turn.chat_id, ask=ask)

    def _tasks(self, runner, turn, name, inp, result=None):
        """Keep the chat's task list (TaskCreate / TaskUpdate / TodoWrite) in its meta."""
        meta = runner.store.meta(turn.chat_id) or {}
        tasks: list[dict] = list(meta.get("tasks") or [])
        if name == "TodoWrite" and isinstance(inp.get("todos"), list):
            tasks = [{"id": str(i + 1), "subject": x.get("content") or "", "active": x.get("activeForm") or "",
                      "status": x.get("status") or "pending"} for i, x in enumerate(inp["todos"])]
        elif name == "TaskCreate" and isinstance(result, dict) and isinstance(result.get("task"), dict):
            tid = str(result["task"].get("id"))
            tasks = [x for x in tasks if x["id"] != tid]
            tasks.append({"id": tid, "subject": result["task"].get("subject") or inp.get("subject") or "",
                          "active": inp.get("activeForm") or "", "status": "pending"})
        elif name == "TaskUpdate" and inp.get("taskId") is not None:
            tid = str(inp["taskId"])
            for x in tasks:
                if x["id"] == tid:
                    if inp.get("status"):
                        x["status"] = inp["status"]
                    if inp.get("subject"):
                        x["subject"] = inp["subject"]
                    if inp.get("activeForm"):
                        x["active"] = inp["activeForm"]
            tasks = [x for x in tasks if x["status"] != "deleted"]
        else:
            return
        _set_tasks(runner, turn, tasks)

    def handle(self, runner, turn, msg):
        mtype = msg.get("type")
        if mtype == "system":
            sub = msg.get("subtype")
            if sub == "init":
                fields = {"model": msg.get("model"), "mode_actual": msg.get("permissionMode")}
                if msg.get("session_id"):
                    fields.update(session_id=msg["session_id"], fork=None)
                runner.store.update_meta(turn.chat_id, touch=False, **fields)
                runner._emit("chat.init", turn.chat_id, model=msg.get("model"), mode=msg.get("permissionMode"))
            elif sub == "compact_boundary":
                runner._block(turn, {"type": "note", "text": t("Context compacted")})
                # the stream only reports the size before compacting; the new size arrives with the next reply
                turn.usage = {**(turn.usage or {}), "used": None, "compacted": True}
            elif sub == "thinking_tokens":
                runner._emit("chat.thinking", turn.chat_id, tokens=msg.get("estimated_tokens"))
        elif mtype == "control_request":
            self._ask(runner, turn, msg)
        elif mtype == "control_cancel_request":
            ask = turn.asks.get(msg.get("request_id"))
            if ask and ask["status"] == "pending":
                ask["status"] = "cancelled"
                runner._emit("chat.ask", turn.chat_id, ask=ask)
        elif mtype == "rate_limit_event":
            info = msg.get("rate_limit_info") or {}
            runner.set_limits(self.agent.id, {"status": info.get("status"), "windows": info.get("unifiedWindows") or {},
                                              "type": info.get("rateLimitType"), "resets": info.get("resetsAt"), "at": _now()})
        elif mtype == "stream_event":
            ev = msg.get("event") or {}
            if msg.get("parent_tool_use_id"):
                return None  # subagent chatter stays out of the main transcript
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta":
                if delta.get("type") == "text_delta":
                    runner._text(turn, delta.get("text", ""))
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    runner._text(turn, delta["thinking"], "thinking")
        elif mtype == "assistant":
            if msg.get("parent_tool_use_id"):
                return None
            message = msg.get("message") or {}
            if msg.get("uuid"):
                turn.last_uuid = msg["uuid"]
            usage = message.get("usage") or {}
            if usage and message.get("model") != "<synthetic>":
                used = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                            "cache_read_input_tokens", "output_tokens"))
                turn.usage = {**(turn.usage or {}), "used": used, "model": message.get("model")}
                turn.usage.pop("compacted", None)
            for block in message.get("content") or []:
                if block.get("type") == "text" and message.get("model") == "<synthetic>" and block.get("text"):
                    runner._text(turn, block["text"])   # local commands (/context, /cost…) answer without streaming
                if block.get("type") != "tool_use":
                    continue
                inp = block.get("input") or {}
                _tool(runner, turn, block.get("id"), block.get("name"), inp)
                if block.get("name") in ("TodoWrite", "TaskUpdate"):
                    self._tasks(runner, turn, block["name"], inp)
        elif mtype == "user":
            for block in (msg.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool = turn.tools.get(block.get("tool_use_id"))
                if not tool:
                    continue
                _tool_end(runner, turn, tool["id"], not block.get("is_error"), block.get("content"))
                if tool["name"] == "TaskCreate":
                    self._tasks(runner, turn, "TaskCreate", tool.get("input") or {}, msg.get("tool_use_result"))
        elif mtype == "result":
            sid = msg.get("session_id")
            if sid:
                runner.store.update_meta(turn.chat_id, touch=False, session_id=sid)
            if not any(b["type"] == "text" for b in turn.blocks) and msg.get("result") and not msg.get("is_error"):
                runner._text(turn, msg["result"])   # nothing streamed: fall back to the final text
            usage = msg.get("modelUsage") or {}
            model = (turn.usage or {}).get("model")
            mu = usage.get(model) if model in usage else next(iter(usage.values()), None) if usage else None
            if mu and mu.get("contextWindow"):
                turn.usage = {**(turn.usage or {}), "window": mu["contextWindow"]}
            error = None
            if msg.get("is_error") or msg.get("subtype", "success") not in ("success", None):
                error = msg.get("result") or t("Claude reported an error")
                if turn.cancelled:
                    error = None
            return {"error": error, "cost": msg.get("total_cost_usd")}
        return None


# =========================================================================== JSON-RPC (Codex, ACP)
class Rpc:
    """JSON-RPC 2.0 over a turn's stdin: numbered requests, each answer routed to its callback."""

    def __init__(self, turn: Turn):
        self.turn = turn
        self.n = 0
        self.waiting: dict[int, Any] = {}
        self.lock = threading.Lock()

    def call(self, method: str, params: dict, then: Any = None) -> bool:
        with self.lock:
            self.n += 1
            n = self.n
            self.waiting[n] = then
        return self.turn.write({"jsonrpc": "2.0", "id": n, "method": method, "params": params})

    def notify(self, method: str, params: dict | None = None) -> bool:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        return self.turn.write(msg)

    def reply(self, rid: Any, result: Any = None, error: str | None = None) -> bool:
        if error is not None:
            return self.turn.write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": error}})
        return self.turn.write({"jsonrpc": "2.0", "id": rid, "result": result if result is not None else {}})

    def dispatch(self, msg: dict) -> bool:
        """An answer to one of our requests: run its callback. False for anything else."""
        if "method" in msg or "id" not in msg or not ("result" in msg or "error" in msg):
            return False
        with self.lock:
            then = self.waiting.pop(msg["id"], None)
        if then:
            err = msg.get("error")
            then(msg.get("result"), (err.get("message") or json.dumps(err)) if isinstance(err, dict) else err)
        return True


def _err(runner, turn: Turn, driver: Driver, message: str) -> None:
    """A request failed before the reply could start: show it and end the process."""
    turn.error = turn.error or message
    driver.close(turn)


# =========================================================================== Codex
# the permission modes the picker offers, as Codex's approval policy + sandbox
CODEX_MODES = {
    "plan": {"approvalPolicy": "on-request", "sandbox": "read-only"},
    "default": {"approvalPolicy": "untrusted", "sandbox": "workspace-write"},
    "acceptEdits": {"approvalPolicy": "on-request", "sandbox": "workspace-write"},
    "auto": {"approvalPolicy": "on-request", "sandbox": "workspace-write", "approvalsReviewer": "auto_review"},
}
CODEX_TOOLS = {"commandExecution": "Bash", "fileChange": "Edit", "webSearch": "WebSearch", "imageView": "Read",
               "collabAgentToolCall": "Agent", "subAgentActivity": "Agent"}


class CodexDriver(Driver):
    """`codex app-server`: a thread per chat (its id is the chat's session id), a turn per message."""

    def command(self, runner, turn, spec):
        return spec["prefix"] + self.agent.args

    def begin(self, runner, turn, spec):
        rpc = turn.state["rpc"] = Rpc(turn)
        turn.state.update(spec=spec, items={}, streamed=set())
        rpc.call("initialize", {"clientInfo": {"name": "resume-studio", "title": "Resume Studio", "version": "1"}},
                 lambda r, e: self._thread(runner, turn) if not e else _err(runner, turn, self, e))

    def _thread(self, runner, turn):
        rpc, spec = turn.state["rpc"], turn.state["spec"]
        chat = spec["chat"]
        rpc.notify("initialized")
        params: dict = {"cwd": str(spec["cwd"])}
        if spec["context"].strip():
            params["developerInstructions"] = spec["context"]
        if chat.get("model_choice"):
            params["model"] = chat["model_choice"]
        mode = CODEX_MODES.get(chat.get("mode") or "")
        if mode:
            params.update(mode)
        if spec["add_dirs"]:
            params["config"] = {"sandbox_workspace_write": {"writable_roots": spec["add_dirs"]}}
        fork = chat.get("fork") or {}

        def started(r, e):
            if e:
                return _err(runner, turn, self, e)
            thread = (r or {}).get("thread") or {}
            turn.state["thread"] = thread.get("id")
            runner.store.update_meta(turn.chat_id, touch=False, session_id=thread.get("id"), fork=None,
                                     model=(r or {}).get("model") or chat.get("model_choice"))
            self.send(runner, turn, spec["prompt"], spec["images"])

        if chat.get("session_id"):
            rpc.call("thread/resume", {**params, "threadId": chat["session_id"], "excludeTurns": True}, started)
        elif fork.get("session_id"):
            rpc.call("thread/fork", {**params, "threadId": fork["session_id"], "excludeTurns": True,
                                     **({"lastTurnId": fork["at"]} if fork.get("at") else {})}, started)
        else:
            rpc.call("thread/start", params, started)

    def send(self, runner, turn, prompt, images):
        spec, chat = turn.state["spec"], runner.store.meta(turn.chat_id) or {}
        items: list[dict] = [{"type": "text", "text": prompt or t("(see image)")}]
        items += [{"type": "localImage", "path": im["path"]} for im in images]
        params: dict = {"threadId": turn.state["thread"], "input": items}
        if chat.get("model_choice"):
            params["model"] = chat["model_choice"]
        if chat.get("effort"):
            params["effort"] = chat["effort"]
        mode = CODEX_MODES.get(chat.get("mode") or "")
        if mode:
            params["approvalPolicy"] = mode["approvalPolicy"]
            params["approvalsReviewer"] = mode.get("approvalsReviewer", "user")

        def started(r, e):
            if e:
                turn.error = e
                self.reply_done(runner, turn, e)
            else:
                turn.state["turn"] = ((r or {}).get("turn") or {}).get("id")
        return turn.state["rpc"].call("turn/start", params, started)

    def interrupt(self, turn):
        if not turn.state.get("turn"):
            return False
        return turn.state["rpc"].call("turn/interrupt", {"threadId": turn.state["thread"], "turnId": turn.state["turn"]})

    def answer(self, runner, turn, ask, decision):
        rpc: Rpc = turn.state["rpc"]
        kind = ask["kind"]
        if kind == "question":
            if decision.get("skip"):
                ask["status"] = "denied"
                rpc.reply(ask["rpc_id"], {"answers": {}})
                return None
            answers = {str(k): str(v) for k, v in (decision.get("answers") or {}).items()}
            ask["status"], ask["answers"] = "answered", answers
            by_text = {q["question"]: q.get("id") for q in ask["input"].get("questions") or []}
            rpc.reply(ask["rpc_id"], {"answers": {by_text.get(q, q): {"answers": [a]} for q, a in answers.items()}})
            return None
        if ask.get("method") == "item/permissions/requestApproval":
            if decision.get("allow"):
                ask["status"] = "allowed"
                rpc.reply(ask["rpc_id"], {"permissions": ask["raw"].get("permissions") or {}, "scope": "turn"})
            else:
                ask["status"] = "denied"
                rpc.reply(ask["rpc_id"], error="The user declined.")
            return None
        if decision.get("allow"):
            sug = ask.get("suggestions") or []
            pick = decision.get("suggestion")
            chosen = sug[pick] if isinstance(pick, int) and 0 <= pick < len(sug) else None
            if chosen and chosen.get("type") == "rule":
                verdict: Any = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": chosen["rules"]}}
            else:
                verdict = "acceptForSession" if chosen else "accept"
            if chosen:
                ask["always"] = pick
            ask["status"] = "allowed"
        else:
            verdict = "decline"
            ask["status"] = "denied"
            note = str(decision.get("message") or "").strip()
            if note and turn.state.get("turn"):   # what to do instead goes into the running turn
                turn.state["rpc"].call("turn/steer", {"threadId": turn.state["thread"], "expectedTurnId": turn.state["turn"],
                                                      "input": [{"type": "text", "text": note}]})
        rpc.reply(ask["rpc_id"], {"decision": verdict})
        return None

    # ---- what the app-server says ------------------------------------------
    def handle(self, runner, turn, msg):
        rpc: Rpc = turn.state["rpc"]
        if rpc.dispatch(msg):
            return None
        method, p = msg.get("method") or "", msg.get("params") or {}
        if "id" in msg:
            self._request(runner, turn, msg["id"], method, p)
            return None
        if p.get("threadId") and turn.state.get("thread") and p["threadId"] != turn.state["thread"]:
            return None   # a subagent's thread
        if method == "item/agentMessage/delta":
            turn.state["streamed"].add(p.get("itemId"))
            runner._text(turn, p.get("delta") or "")
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            runner._text(turn, p.get("delta") or "", "thinking")
        elif method == "item/reasoning/summaryPartAdded":
            runner._text(turn, "\n\n", "thinking")
        elif method == "item/started":
            self._item(runner, turn, p.get("item") or {}, done=False)
        elif method == "item/completed":
            self._item(runner, turn, p.get("item") or {}, done=True)
        elif method == "turn/plan/updated":
            steps = p.get("plan") or []
            status = {"inProgress": "in_progress", "completed": "completed"}
            _set_tasks(runner, turn, [{"id": str(i + 1), "subject": s.get("step") or "", "active": "",
                                       "status": status.get(s.get("status"), "pending")} for i, s in enumerate(steps)])
        elif method == "thread/tokenUsage/updated":
            u = p.get("tokenUsage") or {}
            last = u.get("last") or {}
            turn.usage = {"used": int(last.get("inputTokens") or 0) + int(last.get("outputTokens") or 0),
                          "window": u.get("modelContextWindow"), "model": (runner.store.meta(turn.chat_id) or {}).get("model")}
        elif method == "account/rateLimits/updated":
            runner.set_limits(self.agent.id, codex_limits(p.get("rateLimits") or {}))
        elif method == "thread/compacted":
            runner._block(turn, {"type": "note", "text": t("Context compacted")})
        elif method == "error":
            err = p.get("error") or {}
            if not p.get("willRetry"):
                turn.state["error"] = err.get("message") or json.dumps(err)
        elif method == "turn/completed":
            tr = p.get("turn") or {}
            turn.last_uuid = tr.get("id")
            turn.state["turn"] = None
            error = None
            if tr.get("status") == "failed":
                error = ((tr.get("error") or {}).get("message")) or turn.state.get("error") or t("{name} reported an error", name=self.agent.name)
            turn.state["error"] = None
            return {"error": None if turn.cancelled else error}
        return None

    def _item(self, runner, turn, item, done):
        kind, iid = item.get("type"), item.get("id")
        if kind == "agentMessage":
            if not done:
                last = turn.blocks[-1] if turn.blocks else None
                if last and last["type"] == "text" and last["text"].strip():
                    runner._text(turn, "\n\n")   # a new message after an earlier one in the same reply
            elif iid not in turn.state["streamed"] and item.get("text"):
                runner._text(turn, item["text"])
            return
        if kind == "contextCompaction":
            if done:
                runner._block(turn, {"type": "note", "text": t("Context compacted")})
            return
        if kind == "fileChange":
            turn.state["items"][iid] = item
        name = CODEX_TOOLS.get(kind) or (f"{item.get('server')}: {item.get('tool')}" if kind == "mcpToolCall"
                                         else item.get("tool") if kind == "dynamicToolCall" else None)
        if not name:
            return
        if not done:
            _tool(runner, turn, iid, name, *codex_tool_input(item))
            return
        status = item.get("status")
        ok = status not in ("failed", "declined") and item.get("success") is not False and not item.get("error")
        result = item.get("aggregatedOutput") or item.get("result") or item.get("error") or ""
        if kind == "fileChange":
            result = "\n".join(f"{c.get('kind', {}).get('type', '') if isinstance(c.get('kind'), dict) else c.get('kind', '')} {c.get('path', '')}".strip()
                               for c in item.get("changes") or [])
        if status == "declined":
            result = t("Declined")
        if iid not in turn.tools:
            _tool(runner, turn, iid, name, *codex_tool_input(item))
        _tool_end(runner, turn, iid, ok, result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))

    def _request(self, runner, turn, rid, method, p):
        rpc: Rpc = turn.state["rpc"]
        if method == "item/commandExecution/requestApproval":
            inp = {"command": p.get("command") or "", "description": p.get("reason") or ""}
            sug: list[dict] = [{"type": "session"}]
            if p.get("proposedExecpolicyAmendment"):
                sug.insert(0, {"type": "rule", "rules": p["proposedExecpolicyAmendment"]})
            _new_ask(runner, turn, f"codex-{rid}", "permission", "Bash", inp, suggestions=sug, rpc_id=rid, method=method)
        elif method == "item/fileChange/requestApproval":
            item = turn.state["items"].get(p.get("itemId")) or {}
            changes = item.get("changes") or []
            inp = {"path": ", ".join(str(c.get("path") or "") for c in changes) or p.get("grantRoot") or "",
                   "diff": "\n".join(str(c.get("diff") or "") for c in changes)[:6000], "description": p.get("reason") or ""}
            _new_ask(runner, turn, f"codex-{rid}", "permission", "Edit", inp, suggestions=[{"type": "session"}], rpc_id=rid, method=method)
        elif method == "item/tool/requestUserInput":
            qs = [{"id": q.get("id"), "question": q.get("question") or "", "header": q.get("header") or "",
                   "options": [{"label": o.get("label"), "description": o.get("description")} for o in q.get("options") or []],
                   "multiSelect": False} for q in p.get("questions") or []]
            _new_ask(runner, turn, f"codex-{rid}", "question", "AskUserQuestion", {"questions": qs}, rpc_id=rid, method=method)
        elif method == "item/permissions/requestApproval":
            _new_ask(runner, turn, f"codex-{rid}", "permission", "Permissions",
                     {"description": p.get("reason") or "", "permissions": p.get("permissions")}, rpc_id=rid, method=method, raw=p)
        elif method == "mcpServer/elicitation/request":
            rpc.reply(rid, {"action": "decline"})
        else:
            rpc.reply(rid, error=f"Resume Studio does not support {method}")


def codex_tool_input(item: dict) -> tuple[dict, str]:
    kind = item.get("type")
    if kind == "commandExecution":
        actions = item.get("commandActions") or []
        shown = actions[0].get("command") if len(actions) == 1 and actions[0].get("command") else item.get("command") or ""
        return {"command": shown}, shown
    if kind == "fileChange":
        paths = [str(c.get("path") or "") for c in item.get("changes") or []]
        return {"file_path": paths[0] if paths else ""}, ", ".join(paths)
    if kind == "webSearch":
        return {"query": item.get("query") or ""}, item.get("query") or ""
    if kind == "imageView":
        return {"file_path": item.get("path") or ""}, item.get("path") or ""
    args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
    return args, _tool_hint(None, args) or str(item.get("prompt") or "")[:160]


def codex_limits(rl: dict) -> dict:
    """Codex's usage windows in the shape the UI reads (Claude Code's `unifiedWindows`)."""
    windows = {}
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if not isinstance(w, dict) or w.get("usedPercent") is None:
            continue
        mins = int(w.get("windowDurationMins") or 0)
        key = "five_hour" if mins == 300 else "seven_day" if mins == 10080 else f"m{mins}"
        windows[key] = {"utilization": float(w["usedPercent"]) / 100, "resetsAt": w.get("resetsAt"), "minutes": mins}
    return {"status": "rejected" if rl.get("rateLimitReachedType") else "allowed", "windows": windows,
            "plan": rl.get("planType"), "at": _now()}


def codex_fetch(prefix: list[str], cwd: Path, timeout: float) -> dict:
    """Models, effort levels and account from `codex app-server` (no model call)."""
    answers = _rpc_probe(prefix + ["app-server"], cwd, timeout, [
        ("initialize", {"clientInfo": {"name": "resume-studio", "title": "Resume Studio", "version": "1"}}),
        ("initialized", None), ("account/read", {}), ("model/list", {}), ("account/rateLimits/read", None)])
    acct = (answers.get("account/read") or {})
    account = acct.get("account") or {}
    models = [m for m in (answers.get("model/list") or {}).get("data") or [] if not m.get("hidden")]
    default = next((m for m in models if m.get("isDefault")), models[0] if models else {})
    shown = [{"value": m.get("id") or m.get("model"), "name": m.get("displayName") or m.get("id"), "desc": m.get("description") or "",
              "efforts": [e.get("reasoningEffort") for e in m.get("supportedReasoningEfforts") or [] if e.get("reasoningEffort")],
              "auto_mode": True, "resolved": m.get("id") or m.get("model")} for m in models]
    efforts = list(dict.fromkeys(e for m in shown for e in m["efforts"]))
    limits = (answers.get("account/rateLimits/read") or {}).get("rateLimits")
    return {"models": shown, "more_models": [],
            "default_model": {"resolved": default.get("id") or "", "name": default.get("displayName") or "Codex default"},
            "efforts": efforts, "commands": [], "agents": [], "output_style": None, "output_styles": [],
            "permission_mode": None, "modes": list(CODEX_MODES),
            "account": {"email": account.get("email"), "plan": account.get("planType"), "provider": account.get("type")},
            "signed_in": bool(account) or not acct.get("requiresOpenaiAuth", True),
            "limits": codex_limits(limits) if isinstance(limits, dict) else None}


def _rpc_probe(cmd: list[str], cwd: Path, timeout: float, steps: list[tuple[str, Any]]) -> dict:
    """Send JSON-RPC requests one by one (params None = a notification) and collect the answers by method."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=NO_WINDOW)
    assert proc.stdin and proc.stdout
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    answers: dict[str, Any] = {}
    try:
        for n, (method, params) in enumerate(steps, 1):
            msg: dict = {"jsonrpc": "2.0", "method": method}
            if params is not None or method != "initialized":
                msg["id"] = n
                msg["params"] = params or {}
            proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            proc.stdin.flush()
            if "id" not in msg:
                continue
            for raw in proc.stdout:
                try:
                    got = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(got, dict) and got.get("id") == n and "method" not in got:
                    if "error" in got and method == steps[0][0]:
                        raise RuntimeError((got["error"] or {}).get("message") or "initialize failed")
                    answers[method] = got.get("result")
                    break
            else:
                raise RuntimeError(t("The agent didn't answer"))
        return answers
    finally:
        timer.cancel()
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            runner_kill(type("T", (), {"proc": proc})())


# =========================================================================== Pi
PI_TOOLS = {"bash": "Bash", "read": "Read", "edit": "Edit", "write": "Write", "grep": "Grep", "find": "Glob", "ls": "Glob"}
PI_THINKING = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class PiDriver(Driver):
    """`pi --mode rpc`: prompts in, agent events out. Pi never asks before acting (it has no
    permission prompts by design), so there are no cards; its extension dialogs become cards."""

    def command(self, runner, turn, spec):
        chat = spec["chat"]
        cmd = spec["prefix"] + self.agent.args
        sid = chat.get("session_id")
        if sid:
            cmd += ["--session", sid]
        else:
            turn.state["new_sid"] = uuid.uuid4().hex
            cmd += ["--session-id", turn.state["new_sid"]]
        if chat.get("model_choice"):
            cmd += ["--model", chat["model_choice"]]
        if chat.get("effort") in PI_THINKING:
            cmd += ["--thinking", chat["effort"]]
        ctx = _context_file(runner, turn, spec["context"])
        if ctx:
            cmd += ["--append-system-prompt", ctx]
        return cmd

    def begin(self, runner, turn, spec):
        turn.write({"id": "state", "type": "get_state"})
        self.send(runner, turn, spec["prompt"], spec["images"])

    def send(self, runner, turn, prompt, images):
        imgs = [{"type": "image", "data": base64.b64encode(Path(im["path"]).read_bytes()).decode(), "mimeType": im["media_type"]}
                for im in images]
        return turn.write({"id": "prompt", "type": "prompt", "message": prompt or t("(see image)"), **({"images": imgs} if imgs else {})})

    def interrupt(self, turn):
        return turn.write({"type": "abort"})

    def answer(self, runner, turn, ask, decision):
        if ask["kind"] == "question":
            answers = {str(k): str(v) for k, v in (decision.get("answers") or {}).items()}
            if decision.get("skip") or not answers:
                ask["status"] = "denied"
                turn.write({"type": "extension_ui_response", "id": ask["ui_id"], "cancelled": True})
            else:
                ask["status"], ask["answers"] = "answered", answers
                turn.write({"type": "extension_ui_response", "id": ask["ui_id"], "value": next(iter(answers.values()))})
        else:
            ask["status"] = "allowed" if decision.get("allow") else "denied"
            turn.write({"type": "extension_ui_response", "id": ask["ui_id"], "confirmed": bool(decision.get("allow"))})
        return None

    def handle(self, runner, turn, msg):
        kind = msg.get("type")
        if kind == "response":
            if msg.get("command") == "get_state" and msg.get("success"):
                data = msg.get("data") or {}
                model = data.get("model") or {}
                turn.state["window"] = model.get("contextWindow")
                runner.store.update_meta(turn.chat_id, touch=False, model=f"{model.get('provider')}/{model.get('id')}" if model.get("id") else None)
            elif msg.get("command") == "prompt" and msg.get("success") is False:
                return {"error": msg.get("error") or t("{name} reported an error", name=self.agent.name)}
        elif kind == "message_update":
            ev = msg.get("assistantMessageEvent") or {}
            if ev.get("type") == "text_delta":
                runner._text(turn, ev.get("delta") or "")
            elif ev.get("type") == "thinking_delta":
                runner._text(turn, ev.get("delta") or "", "thinking")
        elif kind == "message_end":
            m = msg.get("message") or {}
            if m.get("role") == "assistant":
                u = m.get("usage") or {}
                used = sum(int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite", "output"))
                if used:
                    turn.usage = {"used": used, "window": turn.state.get("window"), "model": m.get("model")}
                if m.get("stopReason") == "error" and m.get("errorMessage"):
                    turn.state["error"] = m["errorMessage"]
        elif kind == "tool_execution_start":
            args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
            _tool(runner, turn, msg.get("toolCallId"), PI_TOOLS.get(msg.get("toolName"), msg.get("toolName") or "tool"), args)
        elif kind == "tool_execution_end":
            res = msg.get("result")
            text = res.get("content") if isinstance(res, dict) else res
            _tool_end(runner, turn, msg.get("toolCallId"), not msg.get("isError"), text)
        elif kind == "compaction_end" and not msg.get("aborted"):
            runner._block(turn, {"type": "note", "text": t("Context compacted")})
        elif kind == "auto_retry_end" and not msg.get("success"):
            turn.state["error"] = msg.get("finalError") or turn.state.get("error")
        elif kind == "extension_ui_request":
            self._dialog(runner, turn, msg)
        elif kind == "agent_end" and not msg.get("willRetry"):
            if turn.state.get("new_sid"):
                runner.store.update_meta(turn.chat_id, touch=False, session_id=turn.state.pop("new_sid"))
            error, turn.state["error"] = turn.state.get("error"), None
            return {"error": None if turn.cancelled else error}
        return None

    def _dialog(self, runner, turn, msg):
        method = msg.get("method")
        if method == "confirm":
            _new_ask(runner, turn, f"pi-{msg.get('id')}", "permission", str(msg.get("title") or "Pi"),
                     {"description": msg.get("message") or ""}, ui_id=msg.get("id"))
        elif method == "select":
            opts = [o if isinstance(o, str) else (o or {}).get("label", "") for o in msg.get("options") or []]
            _new_ask(runner, turn, f"pi-{msg.get('id')}", "question", "AskUserQuestion",
                     {"questions": [{"question": msg.get("title") or "", "header": "", "multiSelect": False,
                                     "options": [{"label": o, "description": ""} for o in opts]}]}, ui_id=msg.get("id"))
        elif method in ("input", "editor"):
            turn.write({"type": "extension_ui_response", "id": msg.get("id"), "cancelled": True})


def pi_fetch(prefix: list[str], cwd: Path, timeout: float) -> dict:
    """Pi's models (only those it has a login or key for) and its current one, over RPC."""
    proc = subprocess.Popen(prefix + ["--mode", "rpc", "--no-session"], cwd=str(cwd), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    assert proc.stdin and proc.stdout
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    got: dict[str, Any] = {}
    try:
        for cmd in ({"id": "m", "type": "get_available_models"}, {"id": "s", "type": "get_state"}):
            proc.stdin.write((json.dumps(cmd) + "\n").encode("utf-8"))
        proc.stdin.flush()
        for raw in proc.stdout:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("type") == "response" and m.get("id") in ("m", "s"):
                got[m["id"]] = m.get("data") or {}
                if len(got) == 2:
                    break
    finally:
        timer.cancel()
        try:
            proc.stdin.close()
        except OSError:
            pass
        runner_kill(type("T", (), {"proc": proc})())
    if "m" not in got:
        raise RuntimeError(t("The agent didn't answer"))
    models = []
    for m in got["m"].get("models") or []:
        value = f"{m.get('provider')}/{m.get('id')}"
        models.append({"value": value, "name": m.get("name") or m.get("id"), "desc": m.get("provider") or "",
                       "efforts": PI_THINKING[1:] if m.get("reasoning") else [], "auto_mode": False, "resolved": value})
    cur = (got.get("s") or {}).get("model") or {}
    cur_id = f"{cur.get('provider')}/{cur.get('id')}" if cur.get("id") else ""
    return {"models": models, "more_models": [], "default_model": {"resolved": cur_id, "name": cur.get("name") or cur_id or "Pi default"},
            "efforts": PI_THINKING[1:], "commands": [], "agents": [], "output_style": None, "output_styles": [],
            "permission_mode": None, "modes": [], "account": {}, "signed_in": bool(models)}


# =========================================================================== ACP
ACP_TOOLS = {"execute": "Bash", "edit": "Edit", "delete": "Edit", "move": "Edit", "read": "Read", "search": "Grep",
             "fetch": "WebFetch"}
ACP_STATUS = {"completed": "done", "failed": "error"}


class AcpDriver(Driver):
    """The Agent Client Protocol: initialize, then session/load (or session/new), then session/prompt."""

    def command(self, runner, turn, spec):
        return spec["prefix"] + self.agent.args

    def begin(self, runner, turn, spec):
        rpc = turn.state["rpc"] = Rpc(turn)
        turn.state.update(spec=spec, loading=False)
        rpc.call("initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                                                             "terminal": False},
                                "clientInfo": {"name": "resume-studio", "title": "Resume Studio", "version": "1"}},
                 lambda r, e: self._session(runner, turn, r or {}) if not e else _err(runner, turn, self, e))

    def _session(self, runner, turn, init):
        rpc, spec = turn.state["rpc"], turn.state["spec"]
        caps = init.get("agentCapabilities") or {}
        turn.state["images"] = bool((caps.get("promptCapabilities") or {}).get("image"))
        sid = spec["chat"].get("session_id")
        base = {"cwd": str(spec["cwd"]), "mcpServers": []}

        def opened(r, e, resumed=False):
            turn.state["loading"] = False
            if e:
                return _err(runner, turn, self, e)
            r = r or {}
            turn.state["sid"] = r.get("sessionId") or sid
            runner.store.update_meta(turn.chat_id, touch=False, session_id=turn.state["sid"], fork=None)
            self._learn(runner, turn, r)
            if sid and not resumed:
                runner._block(turn, {"type": "note", "text": t("{name} can't reopen an earlier conversation, so this reply starts a new one", name=self.agent.name)})
            self._settings(runner, turn, r)

        if sid and caps.get("loadSession"):
            turn.state["loading"] = True   # the agent replays the history first: not shown again
            rpc.call("session/load", {**base, "sessionId": sid}, lambda r, e: opened(r, e, resumed=True))
        else:
            rpc.call("session/new", base, opened)

    def _learn(self, runner, turn, r):
        info = runner.agent_info(self.agent.id)
        modes, models = r.get("modes") or {}, r.get("models") or {}
        fields: dict = {}
        if modes.get("availableModes"):
            fields["modes"] = [{"id": m.get("id"), "label": m.get("name") or m.get("id"), "desc": m.get("description") or ""}
                               for m in modes["availableModes"]]
            fields["permission_mode"] = modes.get("currentModeId")
        if models.get("availableModels"):
            fields["models"] = [{"value": m.get("modelId"), "name": m.get("name") or m.get("modelId"), "desc": m.get("description") or "",
                                 "efforts": [], "auto_mode": False, "resolved": m.get("modelId")} for m in models["availableModels"]]
            cur = next((m for m in fields["models"] if m["value"] == models.get("currentModelId")), None)
            fields["default_model"] = {"resolved": models.get("currentModelId") or "", "name": cur["name"] if cur else models.get("currentModelId") or ""}
        if info and info.learn(signed_in=True, **fields):
            runner.bus.publish("claude.info", {"agent": self.agent.id})
        if modes.get("currentModeId"):
            runner.store.update_meta(turn.chat_id, touch=False, mode_actual=modes["currentModeId"])
        if models.get("currentModelId"):
            runner.store.update_meta(turn.chat_id, touch=False, model=models["currentModelId"])

    def _settings(self, runner, turn, r):
        """The chat's own model and mode, if it picked some, then the message."""
        rpc, spec = turn.state["rpc"], turn.state["spec"]
        chat = spec["chat"]
        steps = []
        if chat.get("model_choice") and chat["model_choice"] != (r.get("models") or {}).get("currentModelId"):
            steps.append(("session/set_model", {"sessionId": turn.state["sid"], "modelId": chat["model_choice"]}))
        if chat.get("mode") and chat["mode"] != (r.get("modes") or {}).get("currentModeId"):
            steps.append(("session/set_mode", {"sessionId": turn.state["sid"], "modeId": chat["mode"]}))

        def next_step(_r=None, _e=None):   # a setting the agent refuses is skipped, never fatal
            if steps:
                method, params = steps.pop(0)
                rpc.call(method, params, next_step)
            else:
                self.send(runner, turn, spec["prompt"], spec["images"])
        next_step()

    def send(self, runner, turn, prompt, images):
        spec = turn.state["spec"]
        text = prompt or t("(see image)")
        if spec["context"].strip():   # ACP has no system prompt slot: the window's note rides with the message
            text = f"<context>\n{spec['context'].strip()}\n</context>\n\n{text}"
        blocks: list[dict] = [{"type": "text", "text": text}]
        for im in images:
            if turn.state.get("images"):
                blocks.append({"type": "image", "mimeType": im["media_type"], "data": base64.b64encode(Path(im["path"]).read_bytes()).decode()})
            else:
                blocks[0]["text"] += f"\n\n(image: {im['path']})"

        def done(r, e):
            stop = (r or {}).get("stopReason")
            error = e or (t("{name} refused to answer", name=self.agent.name) if stop == "refusal" else None)
            self.reply_done(runner, turn, None if turn.cancelled else error)
        return turn.state["rpc"].call("session/prompt", {"sessionId": turn.state["sid"], "prompt": blocks}, done)

    def interrupt(self, turn):
        rpc: Rpc | None = turn.state.get("rpc")
        if not rpc or not turn.state.get("sid"):
            return False
        for ask in turn.asks.values():   # the protocol wants every open permission request answered "cancelled"
            if ask.get("rpc_id") is not None and ask["status"] in ("pending", "cancelled") and not ask.get("replied"):
                ask["replied"] = True
                rpc.reply(ask["rpc_id"], {"outcome": {"outcome": "cancelled"}})
        return rpc.notify("session/cancel", {"sessionId": turn.state["sid"]})

    def set_live(self, turn, model, mode):
        rpc: Rpc | None = turn.state.get("rpc")
        if not rpc or not turn.state.get("sid"):
            return
        if model:
            rpc.call("session/set_model", {"sessionId": turn.state["sid"], "modelId": model})
        if mode:
            rpc.call("session/set_mode", {"sessionId": turn.state["sid"], "modeId": mode})

    def answer(self, runner, turn, ask, decision):
        opts = ask.get("options") or []

        def first(*kinds):
            return next((o["optionId"] for k in kinds for o in opts if o.get("kind") == k), None)
        if decision.get("allow"):
            sug = ask.get("suggestions") or []
            pick = decision.get("suggestion")
            option = sug[pick]["optionId"] if isinstance(pick, int) and 0 <= pick < len(sug) else first("allow_once", "allow_always")
            ask["status"] = "allowed"
            if isinstance(pick, int):
                ask["always"] = pick
        else:
            option = first("reject_once", "reject_always")
            ask["status"] = "denied"
        ask["replied"] = True
        outcome = {"outcome": "selected", "optionId": option} if option else {"outcome": "cancelled"}
        turn.state["rpc"].reply(ask["rpc_id"], {"outcome": outcome})
        return None

    def handle(self, runner, turn, msg):
        rpc: Rpc = turn.state["rpc"]
        if rpc.dispatch(msg):
            return None
        method, p = msg.get("method") or "", msg.get("params") or {}
        if "id" in msg:
            if method == "session/request_permission":
                tc = p.get("toolCall") or {}
                raw = tc.get("rawInput") if isinstance(tc.get("rawInput"), dict) else {}
                name = ACP_TOOLS.get(tc.get("kind") or "", tc.get("title") or "tool")
                inp = {**raw, "description": tc.get("title") or ""}
                opts = p.get("options") or []
                _new_ask(runner, turn, f"acp-{msg['id']}", "permission", name, inp, rpc_id=msg["id"], options=opts,
                         suggestions=[{"type": "option", "label": o.get("name"), "optionId": o.get("optionId")}
                                      for o in opts if o.get("kind") == "allow_always"])
            else:
                rpc.reply(msg["id"], error=f"Resume Studio does not support {method}")
            return None
        if method != "session/update" or turn.state.get("loading"):
            return None
        u = p.get("update") or {}
        kind = u.get("sessionUpdate")
        content = u.get("content") if isinstance(u.get("content"), dict) else {}
        if kind == "agent_message_chunk" and content.get("type") == "text":
            runner._text(turn, content.get("text") or "")
        elif kind == "agent_thought_chunk" and content.get("type") == "text":
            runner._text(turn, content.get("text") or "", "thinking")
        elif kind == "tool_call":
            raw = u.get("rawInput") if isinstance(u.get("rawInput"), dict) else {}
            name = ACP_TOOLS.get(u.get("kind") or "", u.get("title") or "tool")
            if u.get("toolCallId") not in turn.tools:
                _tool(runner, turn, u.get("toolCallId"), name, raw, u.get("title") or "")
            self._tool_update(runner, turn, u)
        elif kind == "tool_call_update":
            self._tool_update(runner, turn, u)
        elif kind == "plan":
            status = {"in_progress": "in_progress", "completed": "completed"}
            _set_tasks(runner, turn, [{"id": str(i + 1), "subject": e.get("content") or "", "active": "",
                                       "status": status.get(e.get("status"), "pending")} for i, e in enumerate(u.get("entries") or [])])
        elif kind == "available_commands_update":
            cmds = [{"name": c.get("name"), "desc": c.get("description") or "", "hint": ((c.get("input") or {}).get("hint") or ""),
                     "aliases": [], "source": ""} for c in u.get("availableCommands") or []]
            info = runner.agent_info(self.agent.id)
            if info and info.learn(commands=cmds):
                runner.bus.publish("claude.info", {"agent": self.agent.id})
        elif kind == "current_mode_update":
            runner.store.update_meta(turn.chat_id, touch=False, mode_actual=u.get("currentModeId"))
            runner._emit("chat.meta", turn.chat_id, meta=runner.store.meta(turn.chat_id))
        elif kind == "usage_update" and u.get("size"):
            turn.usage = {"used": int(u.get("used") or 0), "window": u.get("size")}
        return None

    def _tool_update(self, runner, turn, u):
        tid = u.get("toolCallId")
        tool = turn.tools.get(tid)
        if not tool:
            return
        if u.get("title") and not tool["hint"]:
            tool["hint"] = str(u["title"])[:160]
        st = ACP_STATUS.get(u.get("status") or "")
        texts = []
        for c in u.get("content") or []:
            inner = c.get("content") if isinstance(c, dict) else None
            if isinstance(inner, dict) and inner.get("type") == "text":
                texts.append(inner.get("text") or "")
            elif isinstance(c, dict) and c.get("type") == "diff":
                texts.append(str(c.get("path") or ""))
        if st:
            _tool_end(runner, turn, tid, st == "done", "\n".join(texts))
        elif texts:
            tool["result"] = _result_preview("\n".join(texts))
            runner._emit("chat.tool", turn.chat_id, tool=tool)


def acp_fetch(agent: Agent):
    def fetch(prefix: list[str], cwd: Path, timeout: float) -> dict:
        """initialize only (no session, so nothing is left in the agent's history): who it is and how it signs in.
        Its models, modes and commands arrive with the first conversation (AcpDriver._learn)."""
        answers = _rpc_probe(prefix + agent.args, cwd, timeout, [
            ("initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                            "clientInfo": {"name": "resume-studio", "title": "Resume Studio", "version": "1"}})])
        init = answers.get("initialize") or {}
        about = init.get("agentInfo") or {}
        data = {"models": [], "more_models": [], "default_model": {"resolved": "", "name": t("{name}'s default", name=agent.name)},
                "efforts": [], "commands": [], "agents": [], "output_style": None, "output_styles": [], "permission_mode": None,
                "modes": [], "account": {"provider": about.get("title") or about.get("name"), "plan": about.get("version")},
                "auth_methods": [m.get("name") or m.get("id") for m in init.get("authMethods") or []],
                "signed_in": agent.signed_in()}
        return data
    return fetch


def fetcher(agent: Agent):
    if agent.protocol == "claude":
        return claude_fetch
    if agent.protocol == "codex":
        return codex_fetch
    if agent.protocol == "pi":
        return pi_fetch
    return acp_fetch(agent)


DRIVERS = {"claude": ClaudeDriver, "codex": CodexDriver, "pi": PiDriver, "acp": AcpDriver}


def driver_for(agent: Agent) -> Driver:
    return DRIVERS[agent.protocol](agent)
