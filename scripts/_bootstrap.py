"""Run every entry point inside the skill's own virtual environment.

The skill never installs anything globally: dependencies live in <skill>/.venv
and pip's cache in <skill>/.cache/pip. The first run creates them.
"""
from __future__ import annotations

import hashlib
import json
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


def _t(text: str, **kw) -> str:
    """Before the venv exists: core.i18n is plain standard library, so it works here too."""
    from core import i18n
    return i18n.t(text, ui=i18n.system_lang(), **kw)


def setup_message() -> str:
    return _t("First run: creating a private environment and installing dependencies "
              "(only inside the skill folder, about a minute)…")


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
        raise RuntimeError(_t("Installing dependencies failed:") + "\n" + (proc.stderr or proc.stdout)[-2000:])
    STAMP.write_text(_requirements_hash(), encoding="utf-8")


def ensure_venv(windowed: bool = False) -> None:
    """Create or update .venv. A half-finished install is retried next time (no stamp)."""
    if venv_ready():
        return
    if not windowed:
        print(setup_message())
        _install()
        return
    _install_with_splash()


def _install_with_splash() -> None:
    """No console under pythonw: show a small window so a minute of setup isn't silent."""
    import threading
    import tkinter as tk

    splash_done()        # this window says more than the launcher's "Starting…"

    errors: list[BaseException] = []
    worker = threading.Thread(target=lambda: _run_catching(_install, errors), daemon=True)
    root = tk.Tk()
    root.title("Resume Studio")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    tk.Label(root, text=setup_message(), padx=28, pady=22, wraplength=360, justify="left").pack()
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


SPLASH_ENV = "RESUME_STUDIO_SPLASH"


def splash_done(wait: float = 3.0) -> None:
    """Resume Studio.exe shows a startup window from the click on (launcher/Launcher.cs). Tell it the app is
    ready (`.ready`), then wait until it has handed the foreground over (`.go`), so what shows next comes up in
    front. Once per run; nothing when not started by it."""
    name = os.environ.pop(SPLASH_ENV, "")
    if not name or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    access = 0x0002 | 0x00100000      # EVENT_MODIFY_STATE | SYNCHRONIZE
    ready, go = k.OpenEventW(access, False, name + ".ready"), k.OpenEventW(access, False, name + ".go")
    try:
        if ready:
            k.SetEvent(ready)
            if go:
                k.WaitForSingleObject(go, int(wait * 1000))
    finally:
        for h in (ready, go):
            if h:
                k.CloseHandle(h)


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
# the app's own preferences (interface language, last data file); a trial run points this elsewhere
PREFS_DIR = Path(os.environ.get("RESUME_STUDIO_PREFS") or LOCAL)


def resolve_data(arg: str | None) -> Path:
    """--data > $RESUME_STUDIO_DATA > the file last opened in the window's Settings > <skill>/resume.yaml"""
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("RESUME_STUDIO_DATA")
    if env:
        return Path(env).expanduser().resolve()
    try:
        saved = json.loads((PREFS_DIR / "prefs.json").read_text(encoding="utf-8")).get("data")
    except (OSError, ValueError, AttributeError):
        saved = None
    if saved and Path(saved).is_file():
        return Path(saved)
    return DEFAULT_DATA


def server_state(data: Path) -> Path:
    """Where the window open on `data` keeps its address (core.store.data_dirs; an older window: .studio)."""
    for sub in ("resume-studio-data/.cache", ".studio/.cache", ".studio"):
        if (data.parent / sub / "server.json").is_file():
            return data.parent / sub / "server.json"
    return data.parent / "resume-studio-data" / ".cache" / "server.json"


def _lost(where: str, text: str) -> None:
    from core.lost_text import HOW, sample
    sys.exit(f"error: nothing was saved: {where} reached this script with its non-ASCII letters (Chinese and the like) "
             f"turned into \"?\" ({sample(text)!r}). " + HOW)


def guard_args(argv: list[str] | None = None) -> None:
    """Refuse a command whose arguments lost their non-ASCII letters on the way in."""
    from core.lost_text import lost_letters
    for arg in sys.argv[1:] if argv is None else argv:
        if lost_letters(arg):
            _lost(f"the argument {arg[:40]!r}", arg)


def read_input(args) -> str:
    """The text of --stdin or --file ("" when neither is given), as UTF-8 (a BOM, or UTF-16 from PowerShell's
    Out-File, is read too), refused when its non-ASCII letters were lost on the way."""
    if getattr(args, "stdin", False):
        raw, where = sys.stdin.buffer.read(), "the text on stdin"
    elif getattr(args, "file", None):
        try:
            raw, where = Path(args.file).read_bytes(), f"the text in {args.file}"
        except OSError as e:
            sys.exit(f"error: can't read {args.file} ({e.strerror or e})")
    else:
        return ""
    try:
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        sys.exit(f"error: nothing was saved: {where} isn't UTF-8 (probably the system's code page, such as GBK). "
                 "Write it as UTF-8 and send it again.")
    from core.lost_text import lost_letters
    if lost_letters(text):
        _lost(where, text)
    return text
