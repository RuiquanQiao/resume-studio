"""Launch Resume Studio.

    python scripts/studio.py --window                                 # native window (what Resume Studio.exe does)
    python scripts/studio.py                                          # in the browser
    python scripts/studio.py --data path/to/resume.yaml --port 9000 --no-browser

The data file defaults to <skill>/resume.yaml (git-ignored).
Only one UI runs per data file: launching again brings the existing one to front.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import webbrowser

from _bootstrap import LOCAL, ROOT, reexec_in_venv, resolve_data

ICON = ROOT / "assets" / "icon.ico"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true", help="open a native window instead of the browser")
    return ap.parse_args()


def show_startup_error(exc: BaseException) -> None:
    """Window launches have no console: show the error instead of vanishing."""
    import traceback
    import tkinter as tk
    from tkinter import messagebox

    log = LOCAL / "last-error.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
    tk.Tk().withdraw()
    messagebox.showerror("Resume Studio", f"启动失败：{exc}\n\n详细信息：{log}")


def set_app_id() -> None:
    """Own taskbar button and icon, instead of being grouped under pythonw.exe."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RuiquanQiao.ResumeStudio")


def focus_existing(url: str) -> None:
    try:
        req = urllib.request.Request(url + "api/focus", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
    except OSError:
        webbrowser.open(url)


def main() -> None:
    args = parse_args()
    reexec_in_venv(windowed=args.window, detach=args.window)

    from core.server import running_instance
    from resume.app import create_app
    from resume.service import Studio

    data = resolve_data(args.data)
    probe = Studio(data)
    state_file = probe.store.state_dir / "server.json"
    url = running_instance(state_file, {"app": "resume-studio", "data": str(probe.store.path)})
    if url:
        print(f"  already running: {url}")
        print(f"  UI running at {url}")
        if not args.no_browser:
            focus_existing(url)
        return

    existed = probe.store.exists()
    inst = create_app(data, window=args.window)
    if not existed:
        print(f"  created a new data file: {inst.studio.store.path}")
    print(f"  Resume Studio  data: {inst.studio.store.path}")

    def write_state(url: str) -> None:
        inst.url["url"] = url
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")

    def cleanup() -> None:
        inst.chats.cancel_all()
        try:
            state_file.unlink()
        except OSError:
            pass

    if not args.window:
        inst.app.serve(port=args.port, open_browser=not args.no_browser, on_ready=write_state, on_exit=cleanup)
        return

    import webview

    set_app_id()
    httpd, url = inst.app.start_background(port=args.port)
    write_state(url)
    window = webview.create_window("Resume Studio", url, width=1480, height=920, min_size=(1100, 680),
                                   background_color="#F3F4F6", text_select=True)

    def bring_to_front() -> None:
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False

    inst.focus["fn"] = bring_to_front

    def pick(kind: str, start: str):
        # Windows uses `directory` only until the user has picked once; then it reopens at the last folder
        dialog = webview.FileDialog.FOLDER if kind == "folder" else webview.FileDialog.OPEN
        return window.create_file_dialog(dialog, directory=os.path.normpath(start), allow_multiple=kind == "files") or []

    inst.dialog["fn"] = pick
    try:
        # keep WebView2's profile (localStorage etc.) inside the skill folder, not in AppData
        webview.start(private_mode=False, storage_path=str(LOCAL / "webview"), icon=str(ICON))
    finally:
        httpd.stop()
        cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if "--window" not in sys.argv:
            raise
        show_startup_error(exc)
        sys.exit(1)
    sys.exit(0)
