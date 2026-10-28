"""The window runs under pythonw: a console program started without CREATE_NO_WINDOW flashes a black
terminal over it. Every start of another program in the app's code says how its window is handled."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_CODE = ["core", "resume", "jobs", "jobhunt", "autofill", "studyplan"]
STARTS = {"run", "Popen", "call", "check_output", "check_call"}
MAC_ONLY = {("core/i18n.py", "defaults")}   # never runs on Windows


def test_every_program_start_hides_its_console():
    missing = []
    for folder in APP_CODE:
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                        and node.func.attr in STARTS):
                    continue
                first = node.args[0] if node.args else None
                exe = first.elts[0].value if isinstance(first, ast.List) and first.elts and isinstance(first.elts[0], ast.Constant) else None
                if (rel, exe) in MAC_ONLY:
                    continue
                if not any(k.arg == "creationflags" or k.arg is None for k in node.keywords):   # k.arg None: **kwargs
                    missing.append(f"{rel}:{node.lineno}")
    assert not missing, "started without CREATE_NO_WINDOW: " + ", ".join(missing)
