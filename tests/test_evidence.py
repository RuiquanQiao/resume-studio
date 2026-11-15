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
    tr = EvidenceTracker(tmp_path / "resume-studio-data")
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
    # the prompt names the range and leaves the commits to the agent: it reads them from git anyway (2026-11-15)
    text = describe(row)
    assert f"{row['changes']['since']}..HEAD" in text
    assert "evaluation harness" not in text and "app.py" not in text

    tr.mark_synced("E1", [p])
    for i in range(30):                                                # long commit messages don't make it longer
        (repo / f"m{i}.py").write_text("x\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", f"fix: {'a long user-facing description of what changed ' * 8}{i}")
    row = state(tr, "E1", p)["paths"][0]
    assert row["changes"]["commits"] == 30 and len(describe(row)) < 300

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
def test_a_second_look_reuses_the_first_until_a_commit(tmp_path, repo, monkeypatch):
    """The page asks on load and again on focus; with 10 repos each look ran ~45 git commands for 4-14 s
    (2026-11-11). A look within half a minute is reused, unless the repo got a commit in between."""
    import threading

    from resume import evidence
    looks = []
    real = evidence.signature
    monkeypatch.setattr(evidence, "signature", lambda p: (looks.append(p), real(p))[1])
    tr = EvidenceTracker(tmp_path / "resume-studio-data")
    p = str(repo).replace("\\", "/")
    tr.mark_synced("E1", [p])
    looks.clear()
    entries = [{"id": "E1", "evidence": [p]}]
    assert tr.status(entries)["E1"]["state"] == "same"
    assert tr.status(entries)["E1"]["state"] == "same"
    assert looks == []                                                   # both answered from the first look

    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: new")
    assert tr.status(entries)["E1"]["state"] == "changed"               # a commit shows at once, unforced
    assert len(looks) == 1

    looks.clear()
    threads = [threading.Thread(target=tr.status, args=(entries,), kwargs={"force": True}) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(looks) <= 2 and len(looks) >= 1                          # forced: looked, at most once each


@needs_git
def test_reading_never_writes_into_the_users_repo(tmp_path, repo):
    index = repo / ".git" / "index"
    os.utime(index, (1_000_000_000, 1_000_000_000))
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    tr = EvidenceTracker(tmp_path / "resume-studio-data")
    tr.status([{"id": "E1", "evidence": [str(repo)]}], force=True)
    assert index.stat().st_mtime == 1_000_000_000                     # git ran with --no-optional-locks


def test_plain_folder_and_missing_paths(tmp_path):
    proj = tmp_path / "plain"
    (proj / "src").mkdir(parents=True)
    (proj / "node_modules").mkdir()
    (proj / "src" / "a.txt").write_text("a", encoding="utf-8")
    tr = EvidenceTracker(tmp_path / "resume-studio-data")
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
    code, r = server.req("POST", f"/api/chats/{chat['id']}/send", {"action": "sync", "entry_id": "EXP-002", "subset_id": "RES-TECH-EN"})
    assert code == 409                                                  # nothing changed: nothing to do

    (repo / "eval.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: evaluation harness")
    st = server.req("GET", "/api/evidence?force=1")[1]["status"]["EXP-002"]
    assert st["state"] == "changed"
    code, r = server.req("POST", f"/api/chats/{chat['id']}/send", {"action": "sync", "entry_id": "EXP-002", "subset_id": "RES-TECH-EN"})
    assert code == 200 and f"{st['paths'][0]['changes']['since']}..HEAD" in r["text"]
    assert "feat: evaluation harness" not in r["text"]                 # the agent reads the commits itself
    assert "resume-writing.md" in r["text"]                           # and rewrites the entry by the writing rules
    assert "--lang zh" in r["text"] and "--lang en" in r["text"]       # both languages get checked
    server.wait_idle(chat["id"])
    assert server.req("GET", "/api/evidence?force=1")[1]["status"]["EXP-002"]["state"] == "same"   # Claude read it
    # the user undid the AI's rewrite: the progress is new again, and "Update from progress" is back (2026-11-15:
    # undone, the app still took the project as read and the button never came back)
    r = server.req("POST", "/api/evidence/EXP-002/unsync")[1]
    assert r["status"]["EXP-002"]["state"] == "changed"
    code, r = server.req("POST", f"/api/chats/{chat['id']}/send", {"action": "sync", "entry_id": "EXP-002", "subset_id": "RES-TECH-EN"})
    assert code == 200
    # the languages are written each on its own, every bullet filling 70-100% of a line (2026-11-15: the Chinese
    # bullets, translated from full English lines, came out at half a line)
    assert "不逐条对应" in r["text"] and "70%–100%" in r["text"]              # the suite's window speaks Chinese


@needs_git
def test_unsync_puts_back_the_baseline_before_the_last_sync(tmp_path, repo):
    tr = EvidenceTracker(tmp_path / "resume-studio-data")
    p = str(repo).replace("\\", "/")
    assert tr.unsync("E1") is False                                    # never synced: nothing to go back to
    tr.mark_synced("E1", [p])                                          # first read
    assert tr.unsync("E1") is True and state(tr, "E1", p)["state"] == "new"   # undone: not read yet again
    tr.mark_synced("E1", [p])
    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: x")
    tr.mark_synced("E1", [p])                                          # the AI took the commit in
    assert state(tr, "E1", p)["state"] == "same"
    assert tr.unsync("E1") is True                                     # the user undid it
    row = state(tr, "E1", p)["paths"][0]
    assert row["state"] == "changed" and row["changes"]["commits"] == 1
    assert tr.unsync("E1") is False                                    # one step back, not further


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
