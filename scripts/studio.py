"""Launch Resume Studio.

    python scripts/studio.py --data E:/Resume/resume.yaml            # in the browser
    python scripts/studio.py --window                                 # native window (what the .pyw does)
    python scripts/studio.py --data ... --port 9000 --no-browser

Only one UI runs per data file: launching again brings the existing one to front.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import webbrowser
from pathlib import Path

from _bootstrap import LOCAL, reexec_in_venv, resolve_data

APP_CONFIG = LOCAL / "app.json"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true", help="open a native window instead of the browser")
    return ap.parse_args()


def window_data_path(arg: str | None) -> Path | None:
    """The window has no working directory to go by: remember the file, ask once."""
    if arg:
        path = Path(arg).expanduser().resolve()
    else:
        try:
            path = Path(json.loads(APP_CONFIG.read_text(encoding="utf-8"))["data"])
        except (OSError, ValueError, KeyError):
            path = ask_data_file()
            if path is None:
                return None
    LOCAL.mkdir(parents=True, exist_ok=True)
    APP_CONFIG.write_text(json.dumps({"data": str(path)}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def ask_data_file() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("Resume Studio", "选择你的简历数据文件 resume.yaml。\n还没有的话，选一个文件夹并输入文件名，会自动新建。")
    name = filedialog.asksaveasfilename(title="选择或新建 resume.yaml", defaultextension=".yaml",
                                        initialfile="resume.yaml", confirmoverwrite=False,
                                        filetypes=[("YAML", "*.yaml *.yml")])
    root.destroy()
    return Path(name).resolve() if name else None


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

    data = window_data_path(args.data) if args.window else resolve_data(args.data)
    if data is None:
        return
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
    try:
        # keep WebView2's profile (localStorage etc.) inside the skill folder, not in AppData
        webview.start(private_mode=False, storage_path=str(LOCAL / "webview"))
    finally:
        httpd.stop()
        cleanup()


if __name__ == "__main__":
    main()
    sys.exit(0)
