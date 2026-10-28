"""Settings → Data file: open another resume.yaml or start a new one, in the same window."""
from __future__ import annotations

import json
import sys

from conftest import ROOT, SAMPLE


def pick(monkeypatch, path):
    monkeypatch.setenv("STUDIO_FAKE_PICK", json.dumps(None if path is None else [str(path)]))


def test_open_another_file_switches_everything(server, data_file, tmp_path, monkeypatch):
    other = tmp_path / "other" / "cv.yaml"
    other.parent.mkdir()
    other.write_text(SAMPLE.read_text(encoding="utf-8").replace("Alex Chen", "Sam Lee"), encoding="utf-8")
    before = data_file.read_text(encoding="utf-8")

    pick(monkeypatch, other)
    status, r = server.req("POST", "/api/data-file", {"mode": "open"})
    assert status == 200 and r == {"path": str(other.resolve()), "created": False}

    st = server.req("GET", "/api/state")[1]
    assert st["data_path"] == str(other.resolve())
    assert "Sam Lee" in json.dumps(st["doc"]["profile"], ensure_ascii=False)
    assert server.req("GET", "/api/instance")[1]["data"] == str(other.resolve())
    # edits now land in the new file; the old one is left alone
    prof = st["doc"]["profile"]
    prof["notes"] = "switched"
    assert server.req("PUT", "/api/item", {"kind": "profile", "data": prof, "base": st["hashes"]["profile"]})[0] == 200
    assert "switched" in other.read_text(encoding="utf-8")
    assert data_file.read_text(encoding="utf-8") == before
    # chats live next to the data file
    assert server.inst.chats.store.root == other.parent / "resume-studio-data" / "chats"
    # remembered for the next start, in the app's own preferences (not next to either data file)
    prefs = json.loads((data_file.parent / "local" / "prefs.json").read_text(encoding="utf-8"))
    assert prefs["data"] == str(other.resolve())


def test_new_file_is_created_and_never_overwrites(server, tmp_path, monkeypatch):
    target = tmp_path / "fresh" / "mine"            # no extension: .yaml is added
    target.parent.mkdir()
    pick(monkeypatch, target)
    status, r = server.req("POST", "/api/data-file", {"mode": "new"})
    assert status == 200 and r["created"] is True and r["path"].endswith("mine.yaml")
    made = target.with_suffix(".yaml")
    assert made.exists() and server.req("GET", "/api/state")[1]["doc"]["entries"] == []

    keep = tmp_path / "keep.yaml"
    keep.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    text = keep.read_text(encoding="utf-8")
    pick(monkeypatch, keep)
    status, r = server.req("POST", "/api/data-file", {"mode": "new"})
    assert status == 200 and r["created"] is False         # an existing file is opened, not replaced
    assert keep.read_text(encoding="utf-8") == text


def test_cancel_same_file_and_bad_files(server, data_file, tmp_path, monkeypatch):
    pick(monkeypatch, None)
    assert server.req("POST", "/api/data-file", {"mode": "open"})[1] == {"path": None}
    pick(monkeypatch, data_file)
    assert server.req("POST", "/api/data-file", {"mode": "open"})[1]["same"] is True
    pick(monkeypatch, tmp_path / "missing.yaml")
    assert server.req("POST", "/api/data-file", {"mode": "open"})[0] == 404
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile: [unclosed\n", encoding="utf-8")
    pick(monkeypatch, bad)
    status, r = server.req("POST", "/api/data-file", {"mode": "open"})
    assert status == 400 and server.req("GET", "/api/state")[1]["data_path"] == str(data_file)
    assert server.req("POST", "/api/data-file", {"mode": "delete"})[0] == 400
    assert not (data_file.parent / "local" / "prefs.json").exists()   # nothing remembered on failure


def test_launch_opens_the_remembered_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import _bootstrap

    saved = tmp_path / "cv.yaml"
    saved.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("RESUME_STUDIO_DATA", raising=False)
    monkeypatch.setattr(_bootstrap, "PREFS_DIR", tmp_path)
    assert _bootstrap.resolve_data(None) == _bootstrap.DEFAULT_DATA
    (tmp_path / "prefs.json").write_text(json.dumps({"data": str(saved)}), encoding="utf-8")
    assert _bootstrap.resolve_data(None) == saved
    assert _bootstrap.resolve_data(str(tmp_path / "x.yaml")) == (tmp_path / "x.yaml").resolve()   # --data wins
    saved.unlink()
    assert _bootstrap.resolve_data(None) == _bootstrap.DEFAULT_DATA                  # gone: back to the default


def test_old_studio_folder_becomes_the_data_folder(tmp_path):
    """An old hidden .studio turns into resume-studio-data on the next start; its caches go into .cache."""
    from resume.service import Studio

    data = tmp_path / "resume.yaml"
    data.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    old = tmp_path / ".studio"
    (old / "hunt").mkdir(parents=True)
    (old / "hunt" / "companies.json").write_text('{"next": 1, "companies": []}', encoding="utf-8")
    (old / "build" / "ALL").mkdir(parents=True)
    (old / "claude-info.json").write_text("{}", encoding="utf-8")
    (old / "agent-codex.json").write_text("{}", encoding="utf-8")

    store = Studio(data).store
    new = tmp_path / "resume-studio-data"
    assert store.state_dir == new and store.cache_dir == new / ".cache" and not old.exists()
    assert (new / "hunt" / "companies.json").is_file()                       # the user's records stay put
    assert sorted(p.name for p in (new / ".cache").iterdir()) == ["agent-codex.json", "build", "claude-info.json"]
    assert not (new / "build").exists() and not (new / "claude-info.json").exists()


def test_export_zips_everything_but_the_cache(server, data_file, tmp_path, monkeypatch):
    import zipfile

    state = server.inst.studio.store.state_dir
    (state / "plan").mkdir(parents=True, exist_ok=True)
    (state / "plan" / "plan.md").write_text("# plan", encoding="utf-8")
    (state / ".cache" / "build").mkdir(parents=True, exist_ok=True)
    (state / ".cache" / "build" / "x.tex").write_text("x", encoding="utf-8")
    (data_file.parent / "exports").mkdir(exist_ok=True)
    (data_file.parent / "exports" / "ALL.pdf").write_bytes(b"%PDF")

    target = tmp_path / "out" / "backup"
    target.parent.mkdir()
    pick(monkeypatch, target)
    status, r = server.req("POST", "/api/data-export")
    assert status == 200 and r["path"] == str(target.with_name("backup.zip"))
    names = zipfile.ZipFile(r["path"]).namelist()
    assert "resume.yaml" in names and "resume-studio-data/plan/plan.md" in names and "exports/ALL.pdf" in names
    assert not [n for n in names if "/.cache/" in n] and r["files"] == len(names)

    pick(monkeypatch, None)   # cancelled
    assert server.req("POST", "/api/data-export") == (200, {"path": None})


def test_move_takes_everything_and_keeps_working(server, data_file, tmp_path, monkeypatch):
    state = server.inst.studio.store.state_dir
    (state / "plan").mkdir(parents=True, exist_ok=True)
    (state / "plan" / "plan.md").write_text("# plan", encoding="utf-8")
    att = state / "chats" / "files" / "c1" / "a.pdf"
    att.parent.mkdir(parents=True, exist_ok=True)
    att.write_bytes(b"%PDF")
    (state / "chats" / "c1.json").write_text(json.dumps({"files": [{"path": str(att)}]}), encoding="utf-8")
    (data_file.parent / "exports").mkdir(exist_ok=True)
    (data_file.parent / "exports" / "ALL.pdf").write_bytes(b"%PDF")

    # a folder that already holds data is refused, and nothing moves
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "resume.yaml").write_text("x", encoding="utf-8")
    pick(monkeypatch, taken)
    status, r = server.req("POST", "/api/data-file", {"mode": "move"})
    assert status == 409 and "resume.yaml" in r["error"] and data_file.is_file()

    dest = tmp_path / "Cloud" / "my resume"
    dest.mkdir(parents=True)
    pick(monkeypatch, dest)
    status, r = server.req("POST", "/api/data-file", {"mode": "move"})
    assert status == 200 and r["moved"] and r["left"] == [] and r["path"] == str(dest / "resume.yaml")
    assert not data_file.exists() and not state.exists() and not (data_file.parent / "exports").exists()
    assert (dest / "resume-studio-data" / "plan" / "plan.md").read_text(encoding="utf-8") == "# plan"
    assert (dest / "exports" / "ALL.pdf").is_file()
    moved = json.loads((dest / "resume-studio-data" / "chats" / "c1.json").read_text(encoding="utf-8"))
    assert moved["files"][0]["path"] == str(dest / "resume-studio-data" / "chats" / "files" / "c1" / "a.pdf")
    # the window now works on the moved file, and opens it next time
    assert server.req("GET", "/api/state")[1]["data_path"] == str(dest / "resume.yaml")
    prefs = json.loads((data_file.parent / "local" / "prefs.json").read_text(encoding="utf-8"))
    assert prefs["data"] == str(dest / "resume.yaml")
