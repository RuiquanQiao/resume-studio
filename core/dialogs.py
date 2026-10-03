"""Native pickers and clipboard for a local UI.

The page cannot learn a real file-system path from the browser, so picking a folder
is done by the server process:
  - in the app window, pywebview's dialog (the modern Windows picker, owned by the window);
  - in browser mode, Tk's chooser (also the modern picker on Windows) on a private Tk thread.
STUDIO_FAKE_PICK (a JSON list of paths, or null for "cancelled") replaces both in tests.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable


class TkWorker:
    """One hidden Tk root on its own thread; every Tk call runs there."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def _loop(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        while True:
            fn, box, done = self.q.get()
            try:
                box["value"] = fn(root)
            except Exception as e:  # noqa: BLE001 - reported to the caller
                box["error"] = e
            done.set()

    def call(self, fn: Callable[[Any], Any], timeout: float | None = None) -> Any:
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
        box: dict = {}
        done = threading.Event()
        self.q.put((fn, box, done))
        done.wait(timeout)
        if "error" in box:
            raise box["error"]
        return box.get("value")


_tk = TkWorker()
_pick_lock = threading.Lock()   # one dialog at a time


def _fake() -> tuple[bool, Any]:
    raw = os.environ.get("STUDIO_FAKE_PICK")
    if raw is None:
        return False, None
    return True, json.loads(raw)


def pick(kind: str, initial: str | None, window_dialog: Callable | None = None) -> list[str]:
    """kind: 'folder' or 'files'. Returns chosen paths (forward slashes), [] if cancelled."""
    is_fake, value = _fake()
    start = initial if initial and Path(initial).is_dir() else str(Path.home())
    with _pick_lock:
        if is_fake:
            res = value
        elif window_dialog is not None:
            res = window_dialog(kind, start)
        else:
            def ask(root):
                from tkinter import filedialog
                root.attributes("-topmost", True)
                if kind == "folder":
                    r = filedialog.askdirectory(parent=root, initialdir=start, mustexist=True, title="选择项目文件夹")
                    return [r] if r else []
                return list(filedialog.askopenfilenames(parent=root, initialdir=start, title="选择文件"))
            res = _tk.call(ask)
    return [str(p).replace("\\", "/") for p in res or [] if p]


def read_clipboard() -> str:
    def get(root):
        try:
            return root.clipboard_get()
        except Exception:  # noqa: BLE001 - empty or non-text clipboard
            return ""
    return _tk.call(get, timeout=5) or ""
