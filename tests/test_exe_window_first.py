"""Resume Studio.exe always opens its own window (2026-11-14).

A preview server left running on the user's data file in browser mode made the exe "open in the browser": the exe
found an instance on that file and asked it to come to front, and a browser instance does that by opening a tab.
Now an instance served to a browser stops when the exe asks (/api/yield) and the exe opens its window; a window
never yields. Everything here runs on a data file in tmp_path, never the user's.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_browser_instance_gives_way_to_the_exe(tmp_path):
    import studio
    from core.server import running_instance

    data = tmp_path / "resume.yaml"
    env = {**os.environ, "RESUME_STUDIO_PREFS": str(tmp_path / "prefs"), "STUDIO_NO_SHELL": "1"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "studio.py"), "--data", str(data), "--no-browser",
                             "--port", str(free_port())], env=env, cwd=str(ROOT),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        state = tmp_path / "resume-studio-data" / ".cache" / "server.json"
        end = time.monotonic() + 60
        while not state.exists() and time.monotonic() < end and proc.poll() is None:
            time.sleep(0.2)
        expect = {"app": "resume-studio", "data": str(data.resolve())}
        url = running_instance(state, {"app": "resume-studio"})
        assert url, "the browser instance never came up"
        assert running_instance(state, {**expect, "window": True}) is None     # it is not a window
        assert studio.give_way(url, lambda: running_instance(state, {"app": "resume-studio"}))
        proc.wait(timeout=30)                                                  # the process ends, not just its port
        assert not state.exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_a_window_never_gives_way(tmp_path, monkeypatch):
    monkeypatch.setenv("RESUME_STUDIO_PREFS", str(tmp_path / "prefs"))
    from resume.app import create_app

    inst = create_app(tmp_path / "resume.yaml", window=True, prefs_dir=tmp_path / "prefs")
    httpd, url = inst.app.start_background(port=free_port())
    try:
        req = urllib.request.Request(url + "api/yield", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["ok"] is False
        assert urllib.request.urlopen(url + "api/instance", timeout=5).status == 200   # still serving
    finally:
        httpd.stop()
        inst.shutdown()
