"""Run every entry point inside the skill's own virtual environment.

The skill never installs anything globally: dependencies live in <skill>/.venv
and pip's cache in <skill>/.cache/pip. The first run creates them.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
LOCAL = ROOT / ".local"          # machine-local settings (git-ignored)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to GBK; make our Chinese/Unicode output safe.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def venv_python(windowed: bool = False) -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return VENV / "bin" / "python"


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def ensure_venv(say: Callable[[str], None] = print) -> None:
    if venv_python().exists():
        return
    say("首次运行：正在创建独立环境并安装依赖（只装在 Skill 目录里，约 1 分钟）…")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    env = {**os.environ, "PIP_CACHE_DIR": str(ROOT / ".cache" / "pip"), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    subprocess.check_call([str(venv_python()), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                          env=env)


def reexec_in_venv(windowed: bool = False, detach: bool = False) -> None:
    """If we are not in the skill's venv, rerun this very command inside it."""
    if in_venv():
        return
    ensure_venv()
    cmd = [str(venv_python(windowed)), *sys.argv]
    if detach:
        subprocess.Popen(cmd, close_fds=True)
        sys.exit(0)
    sys.exit(subprocess.call(cmd))


def resolve_data(arg: str | None) -> Path:
    """--data > $RESUME_STUDIO_DATA > ./resume.yaml"""
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("RESUME_STUDIO_DATA")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "resume.yaml").resolve()
