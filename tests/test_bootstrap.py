"""Where the data lives, and when .venv counts as ready."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _bootstrap  # noqa: E402


def test_default_data_is_inside_the_skill_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.yaml").write_text("someone else's file", encoding="utf-8")
    monkeypatch.delenv("RESUME_STUDIO_DATA", raising=False)
    assert _bootstrap.resolve_data(None) == ROOT / "resume.yaml"


def test_explicit_data_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("RESUME_STUDIO_DATA", str(tmp_path / "env.yaml"))
    assert _bootstrap.resolve_data(None) == (tmp_path / "env.yaml").resolve()
    assert _bootstrap.resolve_data(str(tmp_path / "arg.yaml")) == (tmp_path / "arg.yaml").resolve()


def test_venv_needs_a_matching_stamp(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("jinja2\n", encoding="utf-8")
    venv = tmp_path / ".venv"
    py = venv / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    monkeypatch.setattr(_bootstrap, "REQUIREMENTS", req)
    monkeypatch.setattr(_bootstrap, "STAMP", venv / ".installed")
    monkeypatch.setattr(_bootstrap, "venv_python", lambda windowed=False: py)

    assert not _bootstrap.venv_ready()                    # interrupted install: no stamp
    (venv / ".installed").write_text(_bootstrap._requirements_hash(), encoding="utf-8")
    assert _bootstrap.venv_ready()
    req.write_text("jinja2\npywebview\n", encoding="utf-8")
    assert not _bootstrap.venv_ready()                    # requirements changed: update
