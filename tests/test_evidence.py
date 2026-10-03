"""Evidence folders: change detection, the picker endpoint, and the 'update from project' flow."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import calls
from resume.evidence import EvidenceTracker, describe

needs_git = pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, reason="git missing")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "README.md").write_text("# Proj\n", encoding="utf-8")
    git(r, "add", ".")
    git(r, "commit", "-qm", "init")
    return r


def state(tr: EvidenceTracker, eid: str, path: str) -> dict:
    return tr.status([{"id": eid, "evidence": [path]}], force=True)[eid]


@needs_git
def test_git_project_lifecycle(tmp_path, repo):
    tr = EvidenceTracker(tmp_path / ".studio")
    p = str(repo).replace("\\", "/")
    assert state(tr, "E1", p)["state"] == "new"                     # Claude has not read it yet
    tr.mark_synced("E1", [p])
    assert state(tr, "E1", p)["state"] == "same"

    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: add evaluation harness")
    row = state(tr, "E1", p)["paths"][0]
    assert row["state"] == "changed" and row["changes"]["commits"] == 1
    assert "feat: add evaluation harness" in row["changes"]["log"][0]
    assert "新提交" in describe(row) and "app.py" in describe(row)

    tr.mark_synced("E1", [p])
    (repo / "app.py").write_text("print(2)\n", encoding="utf-8")      # uncommitted work counts too
    row = state(tr, "E1", p)["paths"][0]
    assert row["state"] == "changed" and row["changes"]["uncommitted"] == 1
    tr.mark_synced("E1", [p])
    time.sleep(0.02)
    (repo / "app.py").write_text("print(3)\n", encoding="utf-8")      # editing an already-dirty file again
    os.utime(repo / "app.py", None)
    assert state(tr, "E1", p)["state"] == "changed"


@needs_git
def test_reading_never_writes_into_the_users_repo(tmp_path, repo):
    index = repo / ".git" / "index"
    os.utime(index, (1_000_000_000, 1_000_000_000))
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    tr = EvidenceTracker(tmp_path / ".studio")
    tr.status([{"id": "E1", "evidence": [str(repo)]}], force=True)
    assert index.stat().st_mtime == 1_000_000_000                     # git ran with --no-optional-locks


def test_plain_folder_and_missing_paths(tmp_path):
    proj = tmp_path / "plain"
    (proj / "src").mkdir(parents=True)
    (proj / "node_modules").mkdir()
    (proj / "src" / "a.txt").write_text("a", encoding="utf-8")
    tr = EvidenceTracker(tmp_path / ".studio")
    tr.mark_synced("E1", [str(proj)])
    (proj / "node_modules" / "junk.js").write_text("x", encoding="utf-8")   # vendor dirs are ignored
    assert state(tr, "E1", str(proj))["state"] == "same"
    (proj / "src" / "b.txt").write_text("b", encoding="utf-8")
    row = state(tr, "E1", str(proj))["paths"][0]
    assert row["state"] == "changed" and row["changes"]["added"] == ["src/b.txt"]
    assert state(tr, "E2", str(tmp_path / "nope"))["state"] == "missing"


# ---------------------------------------------------------------- API
def test_pick_folder_uses_the_native_dialog(server, monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps(["E:\\Forge\\BlockMD"]))
    code, r = server.req("POST", "/api/pick", {"kind": "folder"})
    assert code == 200 and r["paths"] == ["E:/Forge/BlockMD"]          # forward slashes for the YAML
    monkeypatch.setenv("STUDIO_FAKE_PICK", "null")                     # cancelled
    assert server.req("POST", "/api/pick", {"kind": "folder"})[1]["paths"] == []


def test_reveal_only_opens_paths_the_user_picked(server):
    code, r = server.req("POST", "/api/reveal-any", {"path": "C:/Windows"})
    assert r["ok"] is False


@needs_git
def test_update_from_project_flow(server, data_file, fake_claude, repo):
    p = str(repo).replace("\\", "/")
    s = server.req("GET", "/api/state")[1]
    e = next(x for x in s["doc"]["entries"] if x["id"] == "EXP-002")
    e["evidence"] = [p]
    server.req("PUT", "/api/item", {"kind": "entry", "id": "EXP-002", "data": e, "base": s["hashes"]["entry"]["EXP-002"]})
    assert server.req("GET", "/api/evidence?force=1")[1]["status"]["EXP-002"]["state"] == "new"

    code, r = server.req("POST", "/api/evidence/EXP-002/synced")      # "标记为已同步"
    assert r["status"]["EXP-002"]["state"] == "same"
    code, chat = server.req("POST", "/api/chats")
    code, r = server.req("POST", f"/api/chats/{chat['id']}/send", {"action": "sync", "entry_id": "EXP-002", "version_id": "RES-TECH-EN"})
    assert code == 409                                                  # nothing changed: nothing to do

    (repo / "eval.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: evaluation harness")
    st = server.req("GET", "/api/evidence?force=1")[1]["status"]["EXP-002"]
    assert st["state"] == "changed"
    code, r = server.req("POST", f"/api/chats/{chat['id']}/send", {"action": "sync", "entry_id": "EXP-002", "version_id": "RES-TECH-EN"})
    assert code == 200 and "feat: evaluation harness" in r["text"] and "eval.py" in r["text"]
    assert "--lang zh" in r["text"] and "--lang en" in r["text"]       # both languages get checked
    server.wait_idle(chat["id"])
    assert server.req("GET", "/api/evidence?force=1")[1]["status"]["EXP-002"]["state"] == "same"   # Claude read it


def test_project_roots_reach_claude(server, data_file, fake_claude):
    s = server.req("GET", "/api/state")[1]
    st = dict(s["doc"]["settings"], project_roots=["E:/Forge"])
    server.req("PUT", "/api/item", {"kind": "settings", "data": st, "base": s["hashes"]["settings"]})
    code, chat = server.req("POST", "/api/chats")
    server.req("POST", f"/api/chats/{chat['id']}/send", {"text": "看看我的项目"})
    server.wait_idle(chat["id"])
    argv = calls(fake_claude)[-1]["argv"]
    ctx = Path(argv[argv.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    assert "`E:/Forge`" in ctx
