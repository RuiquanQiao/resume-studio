"""The window's own title bar (Windows): the app's header row is the title bar, like VS Code, Teams or Edge.

The title bar itself is compiled (native/TitleBar.cs, built into native/TitleBar.dll by scripts/build_launcher.py):
every message to the window passes through it, and in Python each one waited for the interpreter's lock while
other threads worked, so the buttons were drawn a moment after the window (2026-11-11). This file loads it into
pywebview's .NET runtime, turns on WebView2's non-client regions (`app-region: drag` in the page acts as title
bar) and tells the page how much room the buttons take.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DLL = Path(__file__).resolve().parent.parent / "native" / "TitleBar.dll"


def load():
    """The compiled TitleBar class (pywebview has loaded the .NET runtime by then)."""
    import clr
    clr.AddReference(str(DLL))
    from ResumeStudio import TitleBar
    return TitleBar


def css_script(zoom: float) -> str:
    return "document.documentElement.style.setProperty('--caption-w', '%gpx')" % load().CaptionCssWidth(zoom or 1.0)


def attach(window, on: dict, log=None) -> None:
    """Hook `window` (a pywebview Window, before webview.start) so its header row is the title bar.
    `on["on"]` turns True once installed; the page reads it (window.js) to keep room for the buttons.
    Anything going wrong leaves the system title bar, and the page as it was."""
    if os.name != "nt":
        return
    state: dict = {}

    def before_show() -> None:
        try:
            form = window.native
            bar = load()(form, form.webview)
            browser = form.browser
            load_url = browser.load_url

            def load_with_regions(url):   # the first navigation starts here, right after WebView2 is ready
                try:
                    browser.webview.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
                except Exception as exc:
                    _report(log, exc)
                browser.load_url = load_url
                return load_url(url)

            def zoomed(sender, e) -> None:   # rare: keep the header's room for the buttons
                try:
                    form.webview.CoreWebView2.ExecuteScriptAsync(css_script(float(form.webview.ZoomFactor)))
                except Exception:
                    pass

            browser.load_url = load_with_regions
            bar.Install()
            form.webview.ZoomFactorChanged += zoomed
            state["bar"] = bar
            on["on"] = True
        except Exception as exc:
            _report(log, exc)

    def loaded() -> None:
        # runs on a worker thread: touch no WinForms / WebView2 object here (a property read blocks on the GUI
        # thread); the bar's Zoom is a plain value
        bar = state.get("bar")
        if bar:
            window.evaluate_js(css_script(float(bar.Zoom)))

    window.events.before_show += before_show
    window.events.loaded += loaded


def _report(log, exc: BaseException) -> None:
    if log:
        try:
            import traceback
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        except OSError:
            pass
    print(f"  title bar: {exc}", file=sys.stderr)
