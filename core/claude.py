"""Run Claude Code headless (`claude -p`) and stream its progress as events.

The UI never talks to a model directly. It hands a prompt to the Claude Code the
user already has installed and logged in, so skills, settings and the user's
subscription all apply. Claude edits the data file itself; the file watcher then
refreshes the UI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .events import EventBus

DEFAULT_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Skill"]


def find_claude() -> str | None:
    return shutil.which("claude")


class Job:
    def __init__(self, job_id: str, meta: dict):
        self.id = job_id
        self.meta = meta
        self.proc: subprocess.Popen | None = None
        self.status = "running"
        self.started = time.time()
        self.result: str | None = None


class ClaudeRunner:
    def __init__(self, bus: EventBus, cwd: Path, tools: list[str] | None = None):
        self.bus = bus
        self.cwd = cwd
        self.tools = tools or DEFAULT_TOOLS
        self.jobs: dict[str, Job] = {}

    def available(self) -> bool:
        return find_claude() is not None

    def running(self) -> list[dict]:
        return [{"id": j.id, "meta": j.meta, "status": j.status} for j in self.jobs.values() if j.status == "running"]

    def start(self, prompt: str, meta: dict | None = None, add_dirs: list[str] | None = None) -> str:
        exe = find_claude()
        if not exe:
            raise RuntimeError("找不到 claude 命令。请先安装并登录 Claude Code。")
        job = Job(uuid.uuid4().hex[:8], meta or {})
        self.jobs[job.id] = job
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits", "--allowedTools", ",".join(self.tools)]
        for d in add_dirs or []:
            if d and Path(d).exists():
                cmd += ["--add-dir", str(Path(d))]
        threading.Thread(target=self._run, args=(job, cmd, prompt), daemon=True).start()
        return job.id

    def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or not job.proc or job.status != "running":
            return
        if os.name == "nt":  # claude is a .cmd shim on Windows; kill the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(job.proc.pid)],
                           capture_output=True)
        else:
            job.proc.terminate()
        job.status = "cancelled"

    # ---- internals -----------------------------------------------------
    def _emit(self, job: Job, kind: str, **data: Any) -> None:
        self.bus.publish("claude", {"job": job.id, "kind": kind, "meta": job.meta, **data})

    def _run(self, job: Job, cmd: list[str], prompt: str) -> None:
        self._emit(job, "start")
        try:
            job.proc = subprocess.Popen(
                cmd, cwd=str(self.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            job.status = "error"
            self._emit(job, "error", text=str(e))
            return
        assert job.proc.stdin and job.proc.stdout and job.proc.stderr
        job.proc.stdin.write(prompt.encode("utf-8"))
        job.proc.stdin.close()
        stderr_chunks: list[bytes] = []
        threading.Thread(target=lambda: stderr_chunks.append(job.proc.stderr.read()), daemon=True).start()
        for raw in job.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._emit(job, "text", text=line)
                continue
            self._handle(job, msg)
        code = job.proc.wait()
        if job.status == "cancelled":
            self._emit(job, "done", ok=False, text="已取消")
            return
        if code != 0 and job.status == "running":
            job.status = "error"
            err = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            self._emit(job, "error", text=err or f"claude 退出码 {code}")
            return
        job.status = "done" if job.status == "running" else job.status
        self._emit(job, "done", ok=job.status == "done", text=job.result or "",
                   seconds=round(time.time() - job.started, 1))

    def _handle(self, job: Job, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "assistant":
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    self._emit(job, "text", text=block["text"])
                elif block.get("type") == "tool_use":
                    inp = block.get("input") or {}
                    hint = inp.get("file_path") or inp.get("pattern") or inp.get("skill") or ""
                    self._emit(job, "tool", name=block.get("name"), hint=str(hint))
        elif mtype == "result":
            job.result = msg.get("result") or ""
            if msg.get("is_error"):
                job.status = "error"
                self._emit(job, "error", text=job.result or "Claude 报告了错误")
