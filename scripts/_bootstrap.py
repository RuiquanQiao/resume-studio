"""Run every entry point inside the skill's own virtual environment.

The skill never installs anything globally: dependencies live in <skill>/.venv
and pip's cache in <skill>/.cache/pip. The first run creates them.
"""
from __future__ import annotations

import hashlib
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


REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".installed"      # holds the hash of requirements.txt the venv was built from
SETUP_MESSAGE = "首次运行：正在创建独立环境并安装依赖（只装在 Skill 目录里，约 1 分钟）…"


def venv_ready() -> bool:
    try:
        return venv_python().exists() and STAMP.read_text(encoding="utf-8") == _requirements_hash()
    except OSError:
        return False


def _requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def _install() -> None:
    quiet = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    if not venv_python().exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True, capture_output=True, **quiet)
    env = {**os.environ, "PIP_CACHE_DIR": str(ROOT / ".cache" / "pip"), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    proc = subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
                          env=env, capture_output=True, text=True, **quiet)
    if proc.returncode:
        raise RuntimeError("依赖安装失败：\n" + (proc.stderr or proc.stdout)[-2000:])
    STAMP.write_text(_requirements_hash(), encoding="utf-8")


def ensure_venv(windowed: bool = False) -> None:
    """Create or update .venv. A half-finished install is retried next time (no stamp)."""
    if venv_ready():
        return
    if not windowed:
        print(SETUP_MESSAGE)
        _install()
        return
    _install_with_splash()


def _install_with_splash() -> None:
    """No console under pythonw: show a small window so a minute of setup isn't silent."""
    import threading
    import tkinter as tk

    errors: list[BaseException] = []
    worker = threading.Thread(target=lambda: _run_catching(_install, errors), daemon=True)
    root = tk.Tk()
    root.title("Resume Studio")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    tk.Label(root, text=SETUP_MESSAGE, padx=28, pady=22, wraplength=360, justify="left").pack()
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")

    def poll() -> None:
        if worker.is_alive():
            root.after(200, poll)
        else:
            root.destroy()

    worker.start()
    root.after(200, poll)
    root.mainloop()
    worker.join()
    if errors:
        raise errors[0]


def _run_catching(fn: Callable[[], None], errors: list[BaseException]) -> None:
    try:
        fn()
    except BaseException as exc:  # handed back to the main thread
        errors.append(exc)


def reexec_in_venv(windowed: bool = False, detach: bool = False) -> None:
    """If we are not in the skill's venv, rerun this very command inside it."""
    ensure_venv(windowed)          # also catches up when requirements.txt changed
    if in_venv():
        return
    cmd = [str(venv_python(windowed)), *sys.argv]
    if detach:
        subprocess.Popen(cmd, close_fds=True, cwd=str(ROOT))
        sys.exit(0)
    sys.exit(subprocess.call(cmd))


DEFAULT_DATA = ROOT / "resume.yaml"   # git-ignored; never somewhere outside the skill by surprise


def resolve_data(arg: str | None) -> Path:
    """--data > $RESUME_STUDIO_DATA > <skill>/resume.yaml"""
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("RESUME_STUDIO_DATA")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DATA
