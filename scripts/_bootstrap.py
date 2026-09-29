"""Make the skill root importable and check the two runtime dependencies."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to GBK; make our Chinese/Unicode output safe.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REQUIRED = {"jinja2": "jinja2", "ruamel": "ruamel.yaml"}


def ensure_deps() -> None:
    missing = [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print(f"  installing missing packages: {', '.join(missing)}", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", *missing])


def resolve_data(arg: str | None) -> Path:
    """--data > $RESUME_STUDIO_DATA > ./resume.yaml"""
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("RESUME_STUDIO_DATA")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "resume.yaml").resolve()
