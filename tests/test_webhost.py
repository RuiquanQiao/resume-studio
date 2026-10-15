"""core/webhost.py: a skill's own web interface run as a child process and shown through a guarded proxy.

tests/fake_web.py plays the interface: it refuses any browser request that isn't same-origin (like
career-ops' origin guard), serves HTML pages and echoes the headers it receives.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import FAKE_WEB
from core.webhost import WebHost, WebSpec, _head_end, allow_request, bridge_script

WINDOW = 18999   # the window's port in these tests


class H(dict):
    def get(self, k, default=None):   # header lookup is case-insensitive in http.server
        return next((v for kk, v in self.items() if kk.lower() == k.lower()), default)


def get(url: str, headers: dict | None = None, method: str = "GET", body: bytes | None = None):
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=body)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def wait(host: WebHost, states=("running", "error"), timeout: float = 30) -> None:
    end = time.time() + timeout
    while host.state not in states:
        if time.time() > end:
            raise TimeoutError(host.status())
        time.sleep(0.05)


@pytest.fixture
def host(tmp_path: Path):
    events = []
    h = WebHost("fake", tmp_path / "web.log", window_ports=lambda: {WINDOW}, on_change=lambda: events.append(h.state),
                proxy_port=18870)
    h.events = events
    yield h
    h.shutdown()


def spec(**kw) -> WebSpec:
    return WebSpec(argv=[sys.executable, str(FAKE_WEB), "{port}"], cwd=FAKE_WEB.parent, ready_timeout=20, **kw)


# ---------------------------------------------------------------- the guard
def test_the_guard_lets_in_the_window_and_the_app_itself_only():
    ports = {WINDOW}
    assert allow_request(H(), ports, 18870) == (True, False)                                   # not a browser
    assert allow_request(H({"Sec-Fetch-Site": "same-origin"}), ports, 18870) == (True, False)  # the framed app
    assert allow_request(H({"Sec-Fetch-Site": "none"}), ports, 18870) == (True, False)         # typed address
    frame = {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Dest": "iframe"}
    assert allow_request(H({**frame, "Referer": f"http://127.0.0.1:{WINDOW}/"}), ports, 18870) == (True, True)
    assert allow_request(H({**frame, "Referer": f"http://localhost:{WINDOW}/x"}), ports, 18870) == (True, True)
    assert allow_request(H({**frame, "Origin": f"http://127.0.0.1:{WINDOW}"}), ports, 18870) == (True, True)
    assert allow_request(H({**frame, "Referer": "http://127.0.0.1:4444/"}), ports, 18870)[0] is False   # another local app
    assert allow_request(H({**frame}), ports, 18870)[0] is False                                         # no source at all
    assert allow_request(H({"Sec-Fetch-Site": "cross-site", "Referer": f"http://127.0.0.1:{WINDOW}/"}), ports, 18870)[0] is False
    assert allow_request(H({**frame, "Referer": f"https://127.0.0.1:{WINDOW}/"}), ports, 18870)[0] is False


def test_the_bridge_goes_right_after_the_head_tag():
    html = b"<!doctype html><html><HEAD lang=x><title>t</title></head><body></body></html>"
    at = _head_end(html)
    assert html[:at].endswith(b"<HEAD lang=x>")
    assert _head_end(b"<header>no head</header>") == -1
    js = bridge_script(["http://127.0.0.1:1"]).decode()
    assert js.startswith("<script>") and "resume-studio-webhost" in js and "http://127.0.0.1:1" in js and "'*'" not in js


# ---------------------------------------------------------------- the process and the proxy
def test_start_proxy_and_stop(host, tmp_path):
    steps = []
    host.start(lambda progress: (progress("build", "running"), steps.append("prepared"), progress("build", "done"), spec())[-1])
    wait(host)
    assert host.state == "running", host.status()
    assert steps == ["prepared"] and host.steps == {"build": "done"}
    assert {"preparing", "starting", "running"} <= set(host.events)
    url = host.status()["url"]
    assert url.startswith("http://127.0.0.1:")

    # the window's frame: let in, handed on as same-origin, with the bridge script in the page
    status, body, _ = get(url + "pipeline/1", {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Dest": "iframe", "Referer": f"http://localhost:{WINDOW}/"})
    assert status == 200 and "/pipeline/1" in body and "resume-studio-webhost" in body
    assert body.index("resume-studio-webhost") < body.index("<title>")
    status, body, _ = get(url + "api/echo", {"Sec-Fetch-Site": "same-site", "Referer": f"http://127.0.0.1:{WINDOW}/"})
    seen = {k.lower(): v for k, v in json.loads(body).items()}
    assert seen["sec-fetch-site"] == "same-origin" and "origin" not in seen

    # the app's own requests pass untouched; JSON is not rewritten
    status, body, _ = get(url + "api/echo", {"Sec-Fetch-Site": "same-origin", "Origin": url.rstrip("/")})
    seen = {k.lower(): v for k, v in json.loads(body).items()}
    assert status == 200 and seen["origin"] == url.rstrip("/") and "resume-studio-webhost" not in body
    status, body, _ = get(url + "api/echo", {"Sec-Fetch-Site": "same-origin"}, method="POST", body=b'{"a":1}')
    assert status == 200 and json.loads(body) == {"got": '{"a":1}'}

    # any other site: refused by the proxy before the app sees it
    assert get(url, {"Sec-Fetch-Site": "cross-site", "Referer": "https://evil.example/"})[0] == 403
    assert get(url, {"Sec-Fetch-Site": "same-site", "Referer": "http://127.0.0.1:4444/"})[0] == 403

    pid = host.proc.pid
    host.stop()
    assert host.state == "off" and host.proc is None
    assert get(url)[0] == 503                                             # the proxy says so instead of hanging
    assert not _alive(pid)


def test_a_server_that_exits_is_an_error_with_its_log(host):
    host.start(lambda progress: WebSpec(argv=[sys.executable, "-c", "print('boom: no port'); raise SystemExit(3)"],
                                        cwd=FAKE_WEB.parent, ready_timeout=10))
    wait(host)
    assert host.state == "error" and "code 3" in host.error and "boom: no port" in host.error


def test_a_failing_prepare_is_an_error(host):
    from core.webhost import StartError

    def prepare(progress):
        progress("deps", "running")
        raise StartError("npm said no")
    host.start(prepare)
    wait(host)
    assert host.status()["state"] == "error" and host.error == "npm said no" and host.steps == {"deps": "running"}


def test_restart_replaces_the_process_and_keeps_the_address(host):
    host.start(lambda p: spec())
    wait(host)
    url, first = host.url, host.proc.pid
    assert host.start(lambda p: spec())["state"] == "running"            # already running: nothing happens
    assert host.proc.pid == first
    host.start(lambda p: spec(), restart=True)
    wait(host)
    assert host.url == url and host.proc.pid != first and not _alive(first)


@pytest.mark.skipif(os.name != "nt", reason="Windows job object")
def test_the_server_dies_with_the_app(tmp_path):
    """Even when Resume Studio is killed (no clean shutdown), its child servers go with it."""
    script = tmp_path / "parent.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
        "from core.webhost import WebHost, WebSpec\n"
        f"h = WebHost('x', {str(tmp_path / 'w.log')!r}, window_ports=lambda: set(), proxy_port=18880)\n"
        f"h.start(lambda p: WebSpec(argv=[sys.executable, {str(FAKE_WEB)!r}, '{{port}}'], cwd={str(tmp_path)!r}))\n"
        "while h.state != 'running': time.sleep(0.05)\n"
        "print(h.proc.pid, flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    assert _alive(child)
    parent.kill()
    parent.wait()
    end = time.time() + 10
    while _alive(child) and time.time() < end:
        time.sleep(0.1)
    assert not _alive(child)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
