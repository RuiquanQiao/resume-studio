"""A browser nobody sees leaves nothing behind: no tiles in Alt+Tab, no processes.

2026-11-14 the user found dozens of "Resume Studio" and "Alex Chen | Profile" tiles in Alt+Tab that could not be
clicked and did not show in Win+Tab: the tests' headless Edge had handed every tab to Windows 11's Alt+Tab
(WindowTabManager), and Windows kept the entries after Edge had gone. core.browser.NO_ALT_TAB turns that off.
Edge's own log says whether a tab was handed over ("IWindowTabHelper ctr"), so this is checked without pressing
Alt+Tab. The same day five Edge crash reporters of earlier runs were still running; tests/conftest.py's
end_browsers closes whatever a run leaves.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from conftest import ROOT, _playwright_disabled_features, browsers_in, edge_args, end_browsers
from core.browser import HEADLESS_ARGS, NO_ALT_TAB, NO_ALT_TAB_FEATURE, ManagedBrowser, find_browser

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Edge writes what it does with Alt+Tab's tabs only at these log levels
LOGGING = ["--enable-logging", "--v=1", "--vmodule=*window_tab*=3,*browser_collection*=3"]
windows = pytest.mark.skipif(os.name != "nt", reason="Alt+Tab's tabs are Windows 11's")


def _edge() -> str:
    found = find_browser("msedge")
    if not (found and found[0] == "msedge"):
        pytest.skip("Microsoft Edge not installed")
    return found[1]


def _handed_over(log: Path, timeout: float = 30) -> bool:
    """Whether Edge gave Alt+Tab a tab. Waits for Edge to have looked at Alt+Tab's support and for its first
    tab to have had the time to be handed over."""
    end, seen = time.time() + timeout, None
    while time.time() < end:
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        if "IWindowTabHelper ctr" in text:
            return True
        if seen is None and "WTM BrowserCollectionObserver::MaybeInitialize" in text:
            seen = time.time()
        if seen is not None and time.time() - seen > 4:
            return False
        time.sleep(0.25)
    pytest.skip("this Edge doesn't log what it does with Alt+Tab's tabs; nothing to check against")


def _page(tmp_path: Path) -> str:
    page = tmp_path / "page.html"
    page.write_text("<title>Alt Tab Probe</title><p>x", encoding="utf-8")
    return page.as_uri()


@windows
def test_the_apps_headless_edge_hands_no_tab_to_alt_tab(tmp_path):
    edge, log = _edge(), tmp_path / "edge.log"
    args = [edge, f"--user-data-dir={tmp_path / 'profile'}", "--no-first-run", *LOGGING, f"--log-file={log}",
            *HEADLESS_ARGS, _page(tmp_path)]
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=NO_WINDOW)
    try:
        assert not _handed_over(log), "the app's headless Edge handed a tab to Alt+Tab"
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        proc.wait(timeout=10)


@windows
def test_the_tests_playwright_edge_hands_no_tab_and_keeps_playwrights_own_switches(tmp_path):
    _edge()
    from playwright.sync_api import sync_playwright

    log, profile = tmp_path / "edge.log", tmp_path / "pw-profile"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), channel="msedge", headless=True,
                                                   args=[*edge_args(), *LOGGING, f"--log-file={log}"])
        try:
            ctx.pages[0].goto(_page(tmp_path)) if ctx.pages else ctx.new_page().goto(_page(tmp_path))
            # the browser's own command line: only its last --disable-features counts
            root = [cmd for _, cmd in browsers_in(tmp_path) if "--type=" not in cmd]
            assert root, "the browser Playwright started was not found"
            last = re.findall(r"--disable-features=(\S+)", root[0])[-1].strip('"').split(",")
            assert NO_ALT_TAB_FEATURE in last
            assert set(_playwright_disabled_features()) <= set(last), "Playwright's own switches were dropped"
            assert not _handed_over(log), "the tests' Playwright Edge handed a tab to Alt+Tab"
        finally:
            ctx.close()


def test_playwrights_list_is_read():
    names = _playwright_disabled_features()
    assert len(names) >= 5 and all(re.fullmatch(r"[A-Za-z0-9_]+", n) for n in names)


def test_the_apps_headless_browser_starts_with_it(tmp_path, monkeypatch):
    started: list[list[str]] = []

    class Fake:
        pid = 0

        def __init__(self, args, **kw):
            started.append(list(args))

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", Fake)
    ManagedBrowser(tmp_path / "profiles", tmp_path / "browser.log", pace=0)._launch("msedge.exe", tmp_path / "p")
    assert NO_ALT_TAB in started[-1]
    assert sum(a.startswith("--disable-features=") for a in started[-1]) == 1   # a second would replace it


def test_every_test_browser_starts_with_it():
    """A new headless Playwright launch in the tests must not bring the tiles back."""
    missing = []
    for path in (ROOT / "tests").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\.launch(?:_persistent_context)?\((?:[^()]|\([^()]*\))*\)", text):
            if "edge_args()" not in m.group(0):
                missing.append(f"{path.relative_to(ROOT)}: {m.group(0)[:80]}")
    assert not missing, missing


@windows
def test_a_run_closes_the_browsers_its_tests_left(tmp_path):
    edge = _edge()
    proc = subprocess.Popen([edge, f"--user-data-dir={tmp_path / 'left'}", "--no-first-run", *HEADLESS_ARGS,
                             "about:blank"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        end = time.time() + 20
        while time.time() < end and len(browsers_in(tmp_path)) < 2:
            time.sleep(0.5)
        assert browsers_in(tmp_path), "the left browser didn't start"
        end_browsers(tmp_path)
        end = time.time() + 10
        while time.time() < end and browsers_in(tmp_path):
            time.sleep(0.5)
        assert not browsers_in(tmp_path)
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
