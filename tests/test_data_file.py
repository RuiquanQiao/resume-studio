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
    assert server.inst.chats.store.root == other.parent / ".studio" / "chats"
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
