"""The window's agents never change the app's own code (core/codeguard.py; user, 2026-11-14)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import calls
from core.codeguard import ENV, CodeGuard

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "codeguard_hook.py"


@pytest.fixture
def guard(tmp_path):
    app = tmp_path / "app"
    for d in ("web", "scripts", ".local", ".cache", "resume-studio-data"):
        (app / d).mkdir(parents=True)
    return CodeGuard(app, lambda: [app / "resume-studio-data", app / "resume.yaml", app / ".local", app / ".cache"])


def test_paths_inside_the_app_are_code_unless_they_are_data(guard, tmp_path):
    app = guard.root
    assert guard.blocked_path(app / "web" / "app.js")
    assert guard.blocked_path(app / "SKILL.md")
    assert guard.blocked_path("web/app.js", cwd=app)                       # relative to where the chat runs
    assert guard.blocked_path(str(app / "web" / ".." / "jobs" / "x.py"))
    assert not guard.blocked_path(app / "resume.yaml")
    assert not guard.blocked_path(app / "resume-studio-data" / "hunt" / "companies.json")
    assert not guard.blocked_path(app / ".local" / "providers" / "career-ops" / "cv.md")
    assert not guard.blocked_path(tmp_path / "elsewhere" / "notes.md")      # outside the app: not the guard's business
    assert not guard.blocked_path("")
    assert guard.contains_code(app) and guard.contains_code(app.parent) and not guard.contains_code(app / ".local")
    assert guard.safe_cwd() == app / "resume-studio-data"


@pytest.mark.parametrize("cmd", [
    "echo x > web/app.js",
    "Set-Content -Path web\\app.js -Value x",
    "Get-Content a.txt | Out-File {app}/web/app.js",
    "sed -i 's/a/b/' scripts/hunt.py",
    "Remove-Item {app}\\scripts\\hunt.py",
    "Copy-Item resume-studio-data/x.py scripts/x.py",
    "git checkout -- .",
    "git -C {app} commit -am x",
    "python -c \"open('web/app.js','w').write('x')\"",
    "python - <<'EOF'\nfrom pathlib import Path\nPath('scripts/hunt.py').write_text('x')\nEOF",
])
def test_commands_that_write_the_code_are_refused(guard, cmd):
    assert guard.blocked_command(cmd.format(app=guard.root), cwd=guard.root)


@pytest.mark.parametrize("cmd", [
    "python scripts/hunt.py add --company 腾讯 --note \"copy the posting\"",
    "python {app}/scripts/hunt.py list > resume-studio-data/out.txt",
    "Get-Content web/app.js",
    "git status && git diff web/app.js",
    "echo hi > $null",
    "Set-Content resume.yaml x",
    "python -c \"print(open('web/app.js').read())\"",
    "rm {elsewhere}/tmp.txt",
])
def test_commands_that_only_read_or_write_data_are_allowed(guard, tmp_path, cmd):
    assert guard.blocked_command(cmd.format(app=guard.root, elsewhere=tmp_path / "x"), cwd=guard.root) is None


def run_hook(guard, call, env=True):
    e = {**os.environ, **(guard.env() if env else {})}
    e.pop(ENV, None) if not env else None
    return subprocess.run([sys.executable, "-S", "-I", str(HOOK)], input=json.dumps(call).encode("utf-8"), capture_output=True, env=e, timeout=30)


def test_claude_codes_hook_refuses_edits_to_the_code_in_any_mode(guard):
    app = guard.root
    r = run_hook(guard, {"tool_name": "Edit", "tool_input": {"file_path": str(app / "web" / "app.js")}, "cwd": str(app),
                         "permission_mode": "bypassPermissions"})
    assert r.returncode == 2 and "read-only" in r.stderr.decode("utf-8")
    assert run_hook(guard, {"tool_name": "Bash", "tool_input": {"command": "git checkout -- ."}, "cwd": str(app)}).returncode == 2
    assert run_hook(guard, {"tool_name": "Write", "tool_input": {"file_path": str(app / "resume.yaml")}, "cwd": str(app)}).returncode == 0
    assert run_hook(guard, {"tool_name": "Read", "tool_input": {"file_path": str(app / "web" / "app.js")}, "cwd": str(app)}).returncode == 0
    # a terminal session (no settings from the window) is not the window's to hold
    assert run_hook(guard, {"tool_name": "Edit", "tool_input": {"file_path": str(app / "web" / "app.js")}}, env=False).returncode == 0


def test_every_claude_chat_runs_with_the_hook(server, fake_claude):
    cid = server.req("POST", "/api/chats", {})[1]["id"]
    server.req("POST", f"/api/chats/{cid}/send", {"text": "hi"})
    server.wait_idle(cid)
    argv = next(c["argv"] for c in calls(fake_claude) if "--input-format" in (c.get("argv") or []))
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]
    assert "Edit" in hook["matcher"] and "Bash" in hook["matcher"] and "codeguard_hook.py" in hook["hooks"][0]["command"]
