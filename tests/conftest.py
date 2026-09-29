"""Shared fixtures. Temp files live under <repo>/.cache (see pytest.ini), never in the system temp."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT / "examples" / "resume.sample.yaml"
FAKE = Path(__file__).resolve().parent / "fake_claude.py"

# keep every temp file of the test run (and of tools it starts) inside the repo
_TMP = ROOT / ".cache" / "tmp"
_TMP.mkdir(parents=True, exist_ok=True)
for _k in ("TMP", "TEMP", "TMPDIR"):
    os.environ[_k] = str(_TMP)


@pytest.fixture
def data_file(tmp_path: Path) -> Path:
    target = tmp_path / "resume.yaml"
    shutil.copyfile(SAMPLE, target)
    return target


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch) -> Path:
    """Route Claude Code calls to tests/fake_claude.py and return its call log."""
    log = tmp_path / "claude-calls.jsonl"
    monkeypatch.setenv("STUDIO_CLAUDE_CMD", json.dumps([sys.executable, str(FAKE)]))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("STUDIO_NO_SHELL", "1")
    return log


def calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


class Server:
    def __init__(self, inst, httpd, url):
        self.inst, self.httpd, self.url = inst, httpd, url

    def req(self, method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(self.url + path.lstrip("/"), data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_idle(self, chat_id: str, timeout: float = 15) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if not self.inst.chats.busy(chat_id):
                return
            time.sleep(0.05)
        raise TimeoutError("chat still running")


@pytest.fixture
def server(data_file: Path, fake_claude: Path):
    from resume.app import create_app

    inst = create_app(data_file, watch_interval=0.15)
    httpd, url = inst.app.start_background(port=18765)
    inst.url["url"] = url
    yield Server(inst, httpd, url)
    inst.chats.cancel_all()
    inst.watcher.stop()
    httpd.stop()
