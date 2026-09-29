"""Double-click to open Resume Studio in its own window.

Runs scripts/studio.py --window inside the skill's .venv (created on first run).
Pin it to Start or make a shortcut to it; there is nothing else to configure.
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.argv = [str(ROOT / "scripts" / "studio.py"), "--window", *sys.argv[1:]]

try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit:
    pass
except Exception as exc:  # no console under pythonw: show the error instead of vanishing
    import traceback
    import tkinter as tk
    from tkinter import messagebox

    log = ROOT / ".local" / "last-error.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(traceback.format_exc(), encoding="utf-8")
    tk.Tk().withdraw()
    messagebox.showerror("Resume Studio", f"启动失败：{exc}\n\n详细信息：{log}")
