"""The browser Resume Studio drives: a real Chrome (or Edge) with a profile of its own.

No extension. Resume Studio starts the browser with `--remote-debugging-port` and talks to it over
the DevTools protocol (core/cdp.py), the way Playwright does. Since Chrome 136 the debugging port is
refused on the user's everyday profile, so this browser always has its own `--user-data-dir`
(in `<skill>/.local/browser/`): the user signs in to each site there once and stays signed in.

What it offers is what an agent needs to work on a page it has never seen: a snapshot of the whole
document (not just what is on screen; see snapshot.js) with a ref for every control, and clicks,
typing and keys that arrive as real input events (`isTrusted`), so sites built on React or Vue hear
them like a person's. Nothing here submits anything by itself; callers decide what to do.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .cdp import CdpError, Connection, version_info

SNAPSHOT_JS = (Path(__file__).with_name("snapshot.js")).read_text(encoding="utf-8")
WORLD = "resume-studio"
CLOSE_WARNING = 15.0   # seconds the window offers to keep a browser open before it closes itself
MAX_TABS = 8            # past this, work takes a tab whose work is done instead of opening yet another


class BrowserError(Exception):
    """Something the caller (and the user) should read."""


class PageNotOpened(BrowserError):
    """The page didn't open at all (no answer in time, the connection failed): the network, not the page, so
    trying again later can work."""


EMPTY_NOTE = ("the page shows nothing yet, after waiting 10 seconds: it is still loading or drawing itself, not unreadable. "
              "Wait a few seconds and snapshot again, take a screenshot to see it, or open it anew; don't conclude it "
              "can't be read until a screenshot shows it blank too")

COMMANDS = {"open", "snapshot", "text", "click", "hover", "type", "select", "press", "scroll", "upload", "screenshot", "tabs",
            "tab", "back", "front", "sign-in", "profile", "report", "signed-out", "extract", "route", "sync"}


def steps_of(command: str) -> list[dict]:
    """The scripts/browser.py actions in a shell command, as the steps the chat shows ("Click “Add position”").

    Claude often runs several in one go: `B='python …/browser.py --data …'; $B open …; for s in a b; do
    $B open …/$s; done; $B text | grep …`. Every browser call in it becomes a step, in order:
    {"cmd": "click", "ref": "12"}, {"cmd": "type", "ref": "13", "text": "..."}, {"cmd": "open", "url": ...};
    steps inside a loop carry "loop": True. Other commands give []."""
    if "browser.py" not in command:
        return []
    lines = command.split("\n")
    script, bodies = [], {}
    i = 0
    while i < len(lines):   # heredocs: their text is what goes in (type --stdin, profile --stdin)
        line = lines[i]
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        if m:
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == m.group(1)), len(lines))
            key = f"__heredoc{len(bodies)}__"
            bodies[key] = "\n".join(lines[i + 1:end])
            script.append(line[:m.start()] + " " + key + " " + line[m.end():])
            i = end + 1
            continue
        script.append(line)
        i += 1
    text = "\n".join(script)
    names = {m.group(1) for m in re.finditer(r"\b(\w+)=(['\"])([^'\"]*browser\.py[^'\"]*)\2", text)}
    steps: list[dict] = []
    loop = 0
    for seg in _segments(text):
        words = seg.split(None, 1)
        if not words:
            continue
        head = words[0]
        if head in ("do", "then", "else", "{", "("):
            if head == "do":
                loop += 1
            seg = words[1] if len(words) > 1 else ""
            words = seg.split(None, 1)
            if not words:
                continue
            head = words[0]
        if head == "done":
            loop = max(0, loop - 1)
            continue
        if "browser.py" in seg and not re.match(r"\w+=", seg):
            rest = seg[seg.index("browser.py") + len("browser.py"):].lstrip("\"'")
        elif head.strip('"').lstrip("$").strip("{}") in names and head.strip('"').startswith("$"):
            rest = words[1] if len(words) > 1 else ""
        else:
            continue
        body = next((bodies[k] for k in bodies if k in rest), "")
        for k in bodies:
            rest = rest.replace(k, "")
        step = _step(rest, body)
        if step:
            if loop:
                step["loop"] = True
            steps.append(step)
    return steps


def _segments(text: str) -> list[str]:
    """Split a shell line at ; && || | and new lines, outside quotes; what follows a pipe is dropped
    (grep, sed, head: they filter what the browser printed)."""
    out, cur, quote, i, piped = [], [], "", 0, False
    while i < len(text):
        c = text[i]
        if quote:
            cur.append(c)
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c in ";\n" or text.startswith("&&", i) or text.startswith("||", i) or c == "|":
            if not piped:
                out.append("".join(cur).strip())
            cur = []
            piped = c == "|" and not text.startswith("||", i)
            i += 2 if text.startswith(("&&", "||"), i) else 1
            continue
        else:
            cur.append(c)
        i += 1
    if not piped:
        out.append("".join(cur).strip())
    return [x for x in out if x]


def _step(rest: str, body: str) -> dict | None:
    try:
        words = shlex.split(rest, posix=True)
    except ValueError:
        words = rest.split()
    args, flags, i = [], {}, 0
    while i < len(words):
        w = words[i]
        if w.startswith("--"):
            name = w[2:]
            if name in ("data", "text", "file", "note", "in", "max", "down") and i + 1 < len(words):
                flags[name] = words[i + 1]
                i += 2
                continue
            flags[name] = True
        elif not w.startswith((">", "2>", "&", "<")):
            args.append(w)
        i += 1
    if not args:
        return None
    cmd, a = args[0], [x.strip("\"'") for x in args[1:]]
    if cmd not in COMMANDS:   # a cut-off command line (older chats kept only its start)
        return None
    step: dict[str, Any] = {"cmd": cmd}
    if cmd in ("open", "sign-in") and a:
        step["url"] = a[0]
    elif cmd == "snapshot":
        step["part"] = "dialog" if flags.get("dialog") else ("part" if flags.get("in") else "page")
    elif cmd in ("click", "hover", "type", "select", "upload", "scroll") and a:
        step["ref"] = a[0]
    if cmd == "type":
        step["text"] = str(flags.get("text") if isinstance(flags.get("text"), str) else body)[:300]
    elif cmd == "select" and len(a) > 1:
        step["option"] = a[1]
    elif cmd == "press" and a:
        step["key"] = a[0]
    elif cmd == "upload":
        step["files"] = [Path(f).name for f in a[1:]]
    elif cmd == "tab" and a:
        step["index"] = a[0]
    elif cmd in ("profile", "signed-out", "route", "sync") and a:
        step["site"] = a[0]
    elif cmd == "report" and len(a) >= 3:
        step.update(site=a[0], id=a[1], state=a[2], note=str(flags.get("note") or "")[:200])
    return step


def find_browser(prefer: str | None = None) -> tuple[str, str] | None:
    """(name, path) of Chrome, else Edge. `prefer`: a path, or "chrome" / "msedge"."""
    if prefer and Path(prefer).is_file():
        return ("msedge" if "edge" in Path(prefer).name.lower() else "chrome", prefer)
    env = os.environ
    cands = {
        "chrome": [Path(env.get(v, "")) / "Google/Chrome/Application/chrome.exe"
                   for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if env.get(v)],
        "msedge": [Path(env.get(v, "")) / "Microsoft/Edge/Application/msedge.exe"
                   for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA") if env.get(v)],
    }
    for name, exe in (("chrome", "google-chrome"), ("chrome", "chromium"), ("msedge", "microsoft-edge")):
        found = shutil.which(exe)
        if found:
            cands[name].append(Path(found))
    order = [prefer] if prefer in cands else []
    for name in order + [n for n in ("chrome", "msedge") if n not in order]:
        for p in cands[name]:
            if p.is_file():
                return name, str(p)
    return None


NAMES = {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}


def profile_holder(profile: Path) -> int | None:
    """The process id of the browser that has this profile open, if one does. Chromium finds its own
    running instance the same way: on Windows by a hidden window named after the profile folder, elsewhere
    by the `SingletonLock` link ("host-pid")."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW("Chrome_MessageWindow", str(profile))
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    try:
        pid = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[1])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return None

# key name -> (key, code, Windows virtual key code, text)
KEYS = {
    "Enter": ("Enter", "Enter", 13, "\r"), "Tab": ("Tab", "Tab", 9, ""), "Escape": ("Escape", "Escape", 27, ""),
    "Backspace": ("Backspace", "Backspace", 8, ""), "Delete": ("Delete", "Delete", 46, ""),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40, ""), "ArrowUp": ("ArrowUp", "ArrowUp", 38, ""),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37, ""), "ArrowRight": ("ArrowRight", "ArrowRight", 39, ""),
    "Home": ("Home", "Home", 36, ""), "End": ("End", "End", 35, ""), "PageDown": ("PageDown", "PageDown", 34, ""),
    "PageUp": ("PageUp", "PageUp", 33, ""), "Space": (" ", "Space", 32, " "),
}
MODIFIERS = {"alt": 1, "ctrl": 2, "control": 2, "meta": 4, "cmd": 4, "shift": 8}


class ManagedBrowser:
    def __init__(self, profile_root: Path, log: Path, *, prefer: str | None = None, headless: bool = False,
                 pace: float = 1.0, idle_close: float = 60.0):
        self.profile_root = Path(profile_root).resolve()   # the browser ignores a relative --user-data-dir
        self.log = Path(log)
        self.prefer = prefer or os.environ.get("RESUME_STUDIO_BROWSER") or None
        self.headless = headless
        self.pace = pace                      # 0 in tests: no human-like pauses
        self.conn: Connection | None = None
        self.proc: subprocess.Popen | None = None
        self.kind = ""
        self.state = "off"                    # off | starting | running | error | signin
        self.error = ""
        self.signin_url = ""                  # sign-in mode: the page it opened at
        # `lock` guards the bookkeeping only (the tabs, which work has which, starting and stopping): held for
        # moments. What a piece of work does on its page holds that work's own lock (`_page`), so two chats, a
        # scan and a check read their pages at the same time instead of queuing behind the slowest (2026-11-04:
        # the daily scan held one lock for minutes and every chat's command waited, then timed out)
        self.lock = threading.RLock()
        self.work_locks: dict[str | None, threading.RLock] = {}
        self.pages: dict[str, dict] = {}      # targetId -> {url, title, opener}
        # Each piece of work drives a tab of its own, so two of them never land on each other's page: a chat by
        # its id (scripts/browser.py sends it), a check for updates by its site, read_page by its tab. None is
        # the window's own (showing a site to the user). `current` is the tab of the work this thread does.
        self.tab_of: dict[str | None, str] = {}
        self.local = threading.local()
        self.acting_now: dict[str, int] = {}  # work inside `acting()` right now
        self.active: Callable[[str], bool] | None = None   # work that may send its next command any moment (a chat replying)
        self.last_tab: str | None = None      # the tab last worked in: what the window shows
        self.closed: set[str] = set()         # tabs closed here: a late event about one never brings it back
        self.sessions: dict[str, str] = {}    # targetId -> sessionId
        self.contexts: dict[str, int] = {}    # sessionId -> isolated world's execution context
        self.on_change: Callable[[], None] | None = None
        # who the browser is for: "work" when a scan, a check or Claude started it (it closes itself once that is
        # done), "user" when the user opened it or was shown it (it stays until they close it)
        self.owner = ""
        self.holds = 0                        # work using it now (a scan's route, a check for updates)
        self.busy: Callable[[], bool] | None = None   # other work that may use it at any moment (a chat replying)
        self.last_used = 0.0
        self.idle_close = idle_close          # seconds after the work is done
        self.closing = False                  # within CLOSE_WARNING of closing itself
        # Reading needs no window: a scan, a lookup or a chat reading pages runs the browser with none (headless),
        # so nothing shows up beside the screen's edge or flashes in the taskbar while the user is in another app
        # (2026-11-09). It gets a window only when the user should see it: they ask to, they sign in, or work is
        # done there on their behalf (`show`). `shown` says which this one is.
        self.shown = False
        self.ua: dict | None = None           # a windowless browser's user agent, said as a windowed one says it

    # ---- whose tab ------------------------------------------------------------
    @property
    def work(self) -> str | None:
        """The piece of work this thread is doing (None: the window's own)."""
        return getattr(self.local, "work", None)

    @property
    def current(self) -> str | None:
        return self.tab_of.get(self.work)

    @current.setter
    def current(self, tid: str | None) -> None:
        if tid is None:
            self.tab_of.pop(self.work, None)
        else:
            self.tab_of[self.work] = tid

    class _Acting:
        def __init__(self, b: "ManagedBrowser", key: str | None):
            self.b, self.key = b, key

        def __enter__(self):
            self.before = self.b.work
            self.b.local.work = self.key
            if self.key is not None:
                with self.b.working_flip():
                    self.b.acting_now[self.key] = self.b.acting_now.get(self.key, 0) + 1
            return self.b

        def __exit__(self, *exc):
            self.b.local.work = self.before
            if self.key is not None:
                with self.b.working_flip():
                    n = self.b.acting_now.get(self.key, 1) - 1
                    if n:
                        self.b.acting_now[self.key] = n
                    else:
                        self.b.acting_now.pop(self.key, None)
            return False

    def acting(self, key: str | None) -> "ManagedBrowser._Acting":
        """`with browser.acting(chat_id):` around what one piece of work does: it works in that work's own tab."""
        return self._Acting(self, key or None)

    def _page(self) -> "ManagedBrowser._Waited":
        """The lock of the work this thread does: its commands run one at a time, other work's alongside."""
        key = self.work
        with self.lock:
            lk = self.work_locks.get(key)
            if lk is None:
                lk = self.work_locks[key] = threading.RLock()
        return self._Waited(self, lk, "an earlier command of this same work")

    class _Waited:
        """Takes a lock, and when that had to wait, keeps how long and for what (`waited` on the thread), so the
        answer can say so: time spent behind other work is never the page's slowness."""
        def __init__(self, b: "ManagedBrowser", lk: Any, what: str):
            self.b, self.lk, self.what = b, lk, what

        def __enter__(self):
            if not self.lk.acquire(blocking=False):
                t0 = time.time()
                self.lk.acquire()
                w = getattr(self.b.local, "waited", None) or {}
                w[self.what] = w.get(self.what, 0.0) + time.time() - t0
                self.b.local.waited = w
            return self.b

        def __exit__(self, *exc):
            self.lk.release()
            return False

    def take_waited(self) -> dict[str, float]:
        """What this thread's commands waited for, since it was last asked (seconds by what)."""
        w = getattr(self.local, "waited", None) or {}
        self.local.waited = {}
        return w

    def _shared(self) -> "ManagedBrowser._Waited":
        """The bookkeeping lock: held for moments, except while the browser starts or stops."""
        return self._Waited(self, self.lock, "the browser starting, or other work choosing its tab")

    def _busy_with(self, key: str | None) -> bool:
        if key is None:
            return False          # the window's own: a page shown to the user is never in the way
        if self.acting_now.get(key):
            return True
        try:
            return bool(self.active and self.active(key))
        except Exception:  # noqa: BLE001
            return False

    def _taken(self, tid: str, key: str | None) -> bool:
        """Another piece of work is using tab `tid` now."""
        return any(t == tid and k != key and self._busy_with(k) for k, t in list(self.tab_of.items()))

    # ---- lifecycle -----------------------------------------------------------
    def profile_dir(self, kind: str) -> Path:
        return self.profile_root / kind

    def status(self) -> dict:
        found = find_browser(self.prefer)
        shown = self.last_tab if self.last_tab in self.pages else self.tab_of.get(None)
        page = self.pages.get(shown or "") or {}
        if self.state == "running" and (self.conn is None or self.conn.closed):
            self._lost()
        return {"state": self.state, "error": self.error, "browser": NAMES.get(self.kind or (found or ("", ""))[0], ""),
                "found": bool(found), "url": page.get("url", ""), "title": page.get("title", ""),
                "tabs": sum(1 for p in self.pages.values() if p.get("type") == "page"), "signin_url": self.signin_url,
                "owner": self.owner, "closing": self.closing, "shown": self.shown, "working": self.working}

    @property
    def working(self) -> bool:
        """Work is using the browser this moment (a scan's read, a chat's command): the window shows it busy."""
        return bool(self.holds or self.acting_now)

    @contextlib.contextmanager
    def working_flip(self):
        """The bookkeeping lock around a change to `holds` / `acting_now`; the window hears when `working` flips."""
        with self.lock:
            was = self.working
            yield
            now = self.working
        if was != now:
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    def _lost(self) -> None:
        self.state, self.conn, self.owner, self.closing, self.shown, self.ua = "off", None, "", False, False, None
        self.pages, self.sessions, self.contexts, self.tab_of, self.last_tab = {}, {}, {}, {}, None
        self.closed = set()

    def ensure(self, window: bool = False) -> bool:
        """Running and connected, starting it (or reattaching to the one left open) when needed: without a window,
        unless `window` (then a windowless one running now is opened anew with one, see `show`).
        True when this call started the browser (a caller that did may close it again when it is done)."""
        with self.lock:
            if self.conn is not None and not self.conn.closed:
                if window and not self.shown:
                    self._reopen_shown()
                    return False
                return False
            if self.state == "signin":
                raise BrowserError("The browser is open for signing in, without Resume Studio's control. "
                                   "When the sign-in is done, switch back in the window (or close that browser window).")
            self._lost()
            found = find_browser(self.prefer)
            if not found:
                self.state, self.error = "error", "no-browser"
                self._changed()
                raise BrowserError("Neither Google Chrome nor Microsoft Edge was found on this computer.")
            self.kind, exe = found
            profile = self.profile_dir(self.kind)
            port_file = profile / "DevToolsActivePort"
            self.state, self.error = "starting", ""
            self._changed()
            started = False
            try:
                port = self._live_port(port_file)
                if port is None:
                    port_file.unlink(missing_ok=True)
                    # the one just closed may still be letting go of the profile (a busy machine, 2026-11-10): a new
                    # one would hand its address to it and exit, and the start failed
                    self._wait_free(profile, timeout=15)
                    self._launch(exe, profile, window)
                    started = True
                    port = self._wait_port(port_file)
                    self.shown = window
                else:   # one left open by an earlier run: shown unless it is a windowless one
                    # (Edge names itself "Edg/…" either way; the user agent says HeadlessChrome)
                    self.shown = "HeadlessChrome" not in str(version_info(port).get("User-Agent", ""))
                self._connect(port)
            except (BrowserError, CdpError, OSError) as e:
                self.state, self.error = "error", str(e)
                self._changed()
                raise BrowserError(str(e)) from e
            self.state = "running"
            # one left open by an earlier run of the app was not started for this work: it is the user's, unless it
            # has no window (nobody can be using it: it closes itself like one work started)
            work = started or not self.shown
            self.owner, self.last_used = ("work" if work else "user"), time.time()
            if work:
                threading.Thread(target=self._close_when_done, args=(self.conn,), daemon=True, name="browser-idle").start()
            self._changed()
            if window and not self.shown:
                self._reopen_shown()
            return started

    def _reopen_shown(self) -> None:
        """The windowless browser, opened anew with a window on the same profile (one profile is one browser
        process, so it can't just grow a window). Each piece of work finds its page again, in a tab of its own as
        before; what was typed into a page and not sent is lost, so work that fills a form shows the browser first."""
        with self.lock:
            keep = [(k, (self.pages.get(t) or {}).get("url", "")) for k, t in self.tab_of.items()
                    if not str(k or "").startswith("read:")]
            last = next((k for k, t in self.tab_of.items() if t == self.last_tab), None)
            owner = self.owner
            self.stop()
            if not self._wait_free(self.profile_dir(self.kind)):
                raise BrowserError(f"{NAMES.get(self.kind, 'The browser')} didn't close to open again with a window. Try again.")
            self.ensure(window=True)
            assert self.conn is not None
            if owner == "user":
                self.owner = "user"
            for k, url in keep:
                if not url.startswith(("http://", "https://")):
                    continue
                tid = self.tab_of.get(None) if k is None else self._new_tab()
                if tid is None:
                    tid = self.tab_of[None] = self._new_tab()
                self.tab_of[k] = tid
                try:
                    sid = self.conn.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
                    self.sessions[tid] = sid
                    self.conn.send("Page.enable", session=sid)
                    self.conn.send("Page.navigate", {"url": url}, session=sid)
                except CdpError:
                    pass   # the work's next command opens it again
                if k == last:
                    self.last_tab = tid
            self._changed()

    def _live_port(self, port_file: Path) -> int | None:
        try:
            port = int(port_file.read_text(encoding="utf-8").split()[0])
            version_info(port, timeout=1.0)
            return port
        except (OSError, ValueError, IndexError):
            return None

    def _launch(self, exe: str, profile: Path, window: bool = False) -> None:
        profile.mkdir(parents=True, exist_ok=True)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        args = [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", "--no-first-run",
                "--no-default-browser-check", "--disable-search-engine-choice-screen",
                # the user watches Resume Studio, so this window is often behind it: keep it rendering
                # (otherwise screenshots stall and pages pause their timers)
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling"]
        if self.headless or not window:   # self.headless: the tests', never with a window
            args += ["--headless=new", "--window-size=1280,900"]
        args.append("about:blank")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with open(self.log, "ab") as logf:
            self.proc = subprocess.Popen(args, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, creationflags=flags)

    def _wait_port(self, port_file: Path, timeout: float = 25.0) -> int:
        end = time.time() + timeout
        gone = 0.0
        while time.time() < end:
            port = self._live_port(port_file)
            if port is not None:
                return port
            # the process we started may hand over to another and exit (Edge's startup boost does): the browser
            # then comes up a moment later, so only a port that never appears after that is a failure
            if self.proc and self.proc.poll() is not None and not port_file.exists():
                gone = gone or time.time()
                if time.time() - gone > 10:
                    raise BrowserError(f"{NAMES.get(self.kind, 'The browser')} closed right after starting (see {self.log}).")
            time.sleep(0.2)
        raise BrowserError(f"{NAMES.get(self.kind, 'The browser')} didn't open its debugging port in time (see {self.log}).")

    def _connect(self, port: int) -> None:
        info = version_info(port)
        self.ua = self._windowed_ua(str(info.get("User-Agent") or ""))
        conn = Connection(info["webSocketDebuggerUrl"])
        conn.listeners.append(self._on_event)
        self.conn = conn
        conn.send("Target.setDiscoverTargets", {"discover": True})
        for t in conn.send("Target.getTargets").get("targetInfos", []):
            self._track(t)
        pages = [tid for tid, p in self.pages.items() if p["type"] == "page"]
        self.tab_of = {None: pages[-1] if pages else self._new_tab()}

    def _windowed_ua(self, ua: str) -> dict | None:
        """A windowless browser names itself "HeadlessChrome" in its user agent, which some sites refuse; every
        page it works on is told the user agent the same browser says with a window (a real browser either way).
        None for a browser with a window."""
        if "HeadlessChrome" not in ua:
            return None
        full = (re.search(r"HeadlessChrome/([\d.]+)", ua) or re.search(r"Chrome/([\d.]+)", ua) or [None, "0"])[1]
        major = full.split(".")[0]
        brand = "Microsoft Edge" if self.kind == "msedge" else "Google Chrome"
        brands = [{"brand": "Chromium", "version": major}, {"brand": brand, "version": major},
                  {"brand": "Not?A_Brand", "version": "99"}]
        meta = {"brands": brands, "fullVersionList": [{**b, "version": full if b["brand"] != "Not?A_Brand" else "99.0.0.0"}
                                                      for b in brands],
                "fullVersion": full, "platform": "Windows" if os.name == "nt" else "Linux", "platformVersion": "",
                "architecture": "x86", "model": "", "mobile": False, "bitness": "64", "wow64": False}
        return {"userAgent": ua.replace("HeadlessChrome", "Chrome"), "userAgentMetadata": meta}

    def _new_tab(self, **opts: Any) -> str:
        """A blank tab, known at once (its targetCreated event may come after the first command for it)."""
        assert self.conn is not None
        tid = self.conn.send("Target.createTarget", {"url": "about:blank", **opts})["targetId"]
        self.pages.setdefault(tid, {"type": "page", "url": "about:blank", "title": "", "opener": None})
        return tid

    def _track(self, t: dict) -> None:
        if t.get("type") != "page" or t.get("targetId") in self.closed:
            return
        self.pages[t["targetId"]] = {"type": "page", "url": t.get("url", ""), "title": t.get("title", ""),
                                     "opener": t.get("openerId")}

    def _on_event(self, msg: dict) -> None:
        method, p = msg.get("method"), msg.get("params") or {}
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            info = p.get("targetInfo") or {}
            known = info.get("targetId") in self.pages
            self._track(info)
            # a page opened from one being worked on (a link with target=_blank): that work follows it
            if method == "Target.targetCreated" and not known and info.get("type") == "page" and info.get("openerId"):
                for k, t in list(self.tab_of.items()):
                    if t == info["openerId"]:
                        self.tab_of[k] = info["targetId"]
            self._changed()
        elif method == "Target.targetDestroyed":
            tid = p.get("targetId")
            self.pages.pop(tid, None)
            sid = self.sessions.pop(tid, None)
            if sid:
                self.contexts.pop(sid, None)
            for k, t in list(self.tab_of.items()):
                if t == tid:
                    self.tab_of.pop(k, None)   # its next command finds it another tab
            self._changed()
        elif method == "Runtime.executionContextsCleared" or method == "Page.frameNavigated":
            sid = msg.get("sessionId")
            if sid and (method != "Page.frameNavigated" or not (p.get("frame") or {}).get("parentId")):
                self.contexts.pop(sid, None)

    # ---- sign-in mode ---------------------------------------------------------
    # Some sites refuse to sign in a browser that is being driven: their risk checks see
    # `navigator.webdriver`, which the debugging port turns on, and quietly drop the request (Xinghe's
    # "send the code" did). Sign-in mode closes Resume Studio's browser and opens the same profile
    # as a plain browser, with no debugging port, so the user signs in like anywhere else. The
    # sign-in stays in the profile; switching back closes that window normally (so it saves its
    # cookies) and the next command opens the controlled browser again.
    def sign_in(self, url: str) -> dict:
        if not url.startswith(("http://", "https://")):
            raise BrowserError("only http(s) addresses can be opened")
        with self.lock:
            if self.state == "signin":
                self._close_signin()
            self.stop()
            found = find_browser(self.prefer)
            if not found:
                self.state, self.error = "error", "no-browser"
                self._changed()
                raise BrowserError("Neither Google Chrome nor Microsoft Edge was found on this computer.")
            self.kind, exe = found
            profile = self.profile_dir(self.kind)
            port = self._live_port(profile / "DevToolsActivePort")   # one left open by an earlier window
            if port is not None:
                try:
                    self._connect(port)
                except (CdpError, OSError):
                    pass
                self.stop()
            if not self._wait_free(profile):
                raise BrowserError(f"{NAMES.get(self.kind, 'The browser')} is still open on Resume Studio's profile. Close it, then try again.")
            profile.mkdir(parents=True, exist_ok=True)
            self.log.parent.mkdir(parents=True, exist_ok=True)
            args = [exe, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                    "--disable-search-engine-choice-screen"]
            if self.headless:
                args += ["--headless=new", "--window-size=1280,900"]
            args.append(url)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            with open(self.log, "ab") as logf:
                proc = subprocess.Popen(args, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, creationflags=flags)
            self.proc, self.state, self.error, self.signin_url = proc, "signin", "", url
            threading.Thread(target=self._watch_signin, args=(proc,), daemon=True).start()
            self._changed()
            return self.status()

    def _watch_signin(self, proc: subprocess.Popen) -> None:
        """The user closed the sign-in window themselves: the browser is simply off. The process started may
        have handed over to another and exited (Edge's startup boost), so what counts is the profile's holder,
        once it has shown up (or the process started has gone and none came within a few seconds)."""
        profile = self.profile_dir(self.kind)
        started, seen = time.time(), False
        while self.proc is proc and self.state == "signin":
            if profile_holder(profile):
                seen = True
            elif seen or (proc.poll() is not None and time.time() - started > 10):
                break
            time.sleep(0.5)
        with self.lock:
            if self.proc is proc and self.state == "signin":
                self.proc, self.state, self.signin_url = None, "off", ""
                self._changed()

    def end_sign_in(self) -> dict:
        """Close the sign-in window and bring the controlled browser back at the page signed in to."""
        with self.lock:
            url = self.signin_url
            if self.state == "signin":
                self._close_signin()
        if url:
            self.ensure(window=True)   # the user is looking at that page: it opens where they can see it
            self.open(url)
            self.front()
        return self.status()

    def _close_signin(self) -> None:
        profile = self.profile_dir(self.kind)
        pid = profile_holder(profile) or (self.proc.pid if self.proc is not None and self.proc.poll() is None else None)
        if pid:
            # a normal close (WM_CLOSE on Windows, SIGTERM elsewhere), so the browser writes its cookies;
            # a forced kill could lose the sign-in that just happened
            if self.headless:   # the tests' browser has no window to close
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.kill(pid, 9)
            elif os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True,   # not /T: its children refuse it
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.kill(pid, 15)
        if not self._wait_free(profile, timeout=15):
            raise BrowserError("The sign-in window didn't close. Close it yourself, then switch back.")
        self.proc, self.state, self.signin_url = None, "off", ""
        self._changed()

    @staticmethod
    def _wait_free(profile: Path, timeout: float = 10.0) -> bool:
        """Wait for the last browser on this profile to let go of it (a second one would just hand the
        address to the first and exit). False: one still has it open."""
        end = time.time() + timeout
        while profile_holder(profile):
            if time.time() >= end:
                return False
            time.sleep(0.2)
        return True

    def stop(self) -> None:
        """Close the browser (its profile, with the sign-ins, stays)."""
        with self.lock:
            if self.state == "signin":
                self._close_signin()
                return
            if self.conn is not None and not self.conn.closed:
                try:
                    self.conn.send("Browser.close", timeout=5)
                except CdpError:
                    pass
                self.conn.close()
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                self.proc = None
            self._lost()
            self._changed()

    # ---- closing itself when the work is done ----------------------------------
    def claim(self) -> None:
        """The user opened the browser, kept it open, or Claude showed it to them: it stays until they close it."""
        with self.lock:
            if self.state == "running":
                self.owner = "user"
            self._set_closing(False)

    class _Hold:
        def __init__(self, b: "ManagedBrowser"):
            self.b = b

        def __enter__(self):
            with self.b.working_flip():
                self.b.holds += 1
            return self.b

        def __exit__(self, *exc):
            with self.b.working_flip():
                self.b.holds -= 1
                self.b.last_used = time.time()
            return False

    def hold(self) -> "ManagedBrowser._Hold":
        """`with browser.hold():` around work that uses the browser between commands (a scan, a check)."""
        return self._Hold(self)

    def in_use(self) -> bool:
        # work in the middle of a command (a posting read for a logo or a JD holds no `hold`): with no lock
        # shared any more, nothing else keeps the browser from closing under it
        if self.holds or self.acting_now:
            return True
        try:
            return bool(self.busy and self.busy())
        except Exception:  # noqa: BLE001
            return True

    def close_if_idle(self, after: float | None = None) -> bool:
        """Close a browser that work started once nothing uses it and `idle_close` seconds have passed since it last
        did; never one the user opened or was shown. For the last `CLOSE_WARNING` seconds the status says
        `closing`, so the window can offer to keep it open. The sign-ins stay in the profile. True when it closed."""
        with self.lock:
            if self.state != "running" or self.conn is None or self.conn.closed or self.owner != "work":
                return False
            if self.in_use():
                self.last_used = time.time()
                self._set_closing(False)
                return False
            wait = self.idle_close if after is None else after
            idle = time.time() - self.last_used
            if idle < wait:
                self._set_closing(idle >= wait - min(CLOSE_WARNING, wait / 2))
                return False
            self.stop()
            return True

    def _set_closing(self, on: bool) -> None:
        if self.closing != on:
            self.closing = on
            self._changed()

    def _close_when_done(self, conn: Connection) -> None:
        while self.conn is conn and not conn.closed and self.owner == "work":
            time.sleep(min(2.0, max(0.2, self.idle_close / 8)))
            try:
                if self.close_if_idle():
                    return
            except Exception:  # noqa: BLE001  closing is a courtesy; never break the browser over it
                pass
    # ---- the page being worked on ---------------------------------------------
    def _session(self) -> str:
        with self._shared():
            self.ensure()
            return self._session_locked()

    def _session_locked(self) -> str:
        self.last_used = time.time()
        assert self.conn is not None
        key = self.work
        tid = self.current
        if tid is None or tid not in self.pages or self._taken(tid, key):
            # no tab yet, or another piece of work is in it: a tab no other work has (the window's first), else a new
            # one, so a chat coming back to its tab finds its page there; past MAX_TABS, one whose work is done
            claimed = {t for k, t in self.tab_of.items() if k is not None and k != key}
            pages = [t for t in reversed(self.pages) if not self._taken(t, key)]
            free = [t for t in [self.tab_of.get(None), *pages] if t in pages and t not in claimed]
            if not free and len(self.pages) >= MAX_TABS:
                free = pages
            tid = free[0] if free else self._new_tab()
            self.current = tid
        if not (key or "").startswith("read:") and self.last_tab != tid:
            self.last_tab = tid
            self._changed()
        sid = self.sessions.get(tid)
        if sid is None:
            sid = self.conn.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            self.sessions[tid] = sid
            self.conn.send("Page.enable", session=sid)
            if self.ua:
                try:
                    self.conn.send("Emulation.setUserAgentOverride", self.ua, session=sid)
                except CdpError:
                    pass   # it reads all the same, as itself
        return sid

    def _send(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        assert self.conn is not None
        return self.conn.send(method, params, session=self._session(), timeout=timeout)

    def _context(self, sid: str) -> int:
        ctx = self.contexts.get(sid)
        if ctx is None:
            assert self.conn is not None
            frame = self.conn.send("Page.getFrameTree", session=sid)["frameTree"]["frame"]["id"]
            ctx = self.conn.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": WORLD,
                                                              "grantUniveralAccess": False}, session=sid)["executionContextId"]
            self.contexts[sid] = ctx
        return ctx

    def _js(self, call: str, *, by_value: bool = True, timeout: float = 30.0) -> Any:
        """Run `__rs.<call>` in the isolated world (installing it there first)."""
        with self._page():
            sid = self._session()
            assert self.conn is not None
            self._ask()
            for attempt in (0, 1):
                ctx = self._context(sid)
                try:
                    r = self.conn.send("Runtime.evaluate", {"expression": SNAPSHOT_JS + "\n;__rs." + call, "contextId": ctx,
                                                            "returnByValue": by_value, "awaitPromise": True}, session=sid,
                                       timeout=timeout)
                except CdpError as e:
                    if attempt == 0 and "context" in str(e).lower():
                        self.contexts.pop(sid, None)   # the page navigated: a new world
                        continue
                    raise BrowserError(str(e)) from e
                if r.get("exceptionDetails"):
                    d = r["exceptionDetails"]
                    text = ((d.get("exception") or {}).get("description") or d.get("text") or "").split("\n")[0]
                    raise BrowserError(text.removeprefix("Error: "))
                res = r.get("result") or {}
                return res.get("value") if by_value else res
            raise BrowserError("the page kept changing; try again")

    def _eval_page(self, expression: str, timeout: float = 30.0) -> Any:
        r = self._send("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=timeout)
        return (r.get("result") or {}).get("value")

    def _pause(self, lo: float = 0.25, hi: float = 0.7) -> None:
        if self.pace:
            time.sleep(random.uniform(lo, hi) * self.pace)

    def _ask(self, expression: str = "1", wait: float = 60.0) -> Any:
        """A short question to the page. Some pages (BOSS直聘's home) keep their main thread busy for many
        seconds while loading, and every script waits for it: keep asking until it answers, then say so
        rather than time out."""
        end = time.time() + wait
        while True:
            try:
                return self._eval_page(expression, timeout=5)
            except CdpError:
                if time.time() > end:
                    raise BrowserError(f"the page has been busy (its own scripts running) for {int(wait)} seconds and "
                                       "doesn't answer; run the command again in a moment, or open the page anew")

    def here(self) -> dict:
        with self._page():
            self._session()
            url = self._ask("location.href") or ""
            title = self._ask("document.title") or ""
            if self.current in self.pages:
                self.pages[self.current].update(url=url, title=title)
            return {"url": url, "title": title}

    def wait_loaded(self, timeout: float = 20.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self._eval_page("document.readyState", timeout=3) == "complete":
                    break
            except CdpError:   # busy loading: ask again
                pass
            time.sleep(0.2)
        time.sleep(0.4 if self.pace else 0.05)   # single-page apps render a moment after "load"

    # how much the page shows: its visible text and controls. A single-page app (campus.nio.com, `/#/`)
    # is "complete" with an empty body and draws itself a second or more later.
    _DRAWN_JS = ("(() => { const b = document.body; if (!b) return [0, 0];"
                 " return [(b.innerText || '').trim().length,"
                 " b.querySelectorAll('a[href],button,input,select,textarea,[role=button]').length]; })()")

    def wait_drawn(self, timeout: float = 10.0) -> bool:
        """Wait until the page shows something and has stopped changing (two equal readings in a row), at most
        `timeout` seconds. False: still nothing to show when the time ran out."""
        end = time.time() + timeout
        last = None
        while True:
            try:
                now = self._eval_page(self._DRAWN_JS, timeout=5)
            except CdpError:   # busy, or navigating: ask again
                now = None
            if isinstance(now, list) and now[0] > 0:
                if now == last:
                    return True
                last = now
            if time.time() >= end:
                return isinstance(last, list) and last[0] > 0
            time.sleep(0.3)

    # ---- what callers use ----------------------------------------------------
    def open(self, url: str, new_tab: bool = False) -> dict:
        if not url.startswith(("http://", "https://", "about:")):
            raise BrowserError("only http(s) addresses can be opened")
        with self._page():
            self.ensure()
            assert self.conn is not None
            if new_tab:
                with self.lock:
                    self.current = self._new_tab()
            r = self._send("Page.navigate", {"url": url})
            if r.get("errorText"):
                raise BrowserError(f"couldn't open {url}: {r['errorText']}")
            self.wait_loaded()
            drawn = self.wait_drawn()
            here = self.here()
            if not drawn:
                here["note"] = EMPTY_NOTE
            return here

    def front(self) -> None:
        """Show the page being worked on (the user signs in there, or watches): a windowless browser is opened
        anew with a window first."""
        self.ensure(window=True)
        with self._page():
            self._send("Page.bringToFront")
            try:
                assert self.conn is not None
                win = self.conn.send("Browser.getWindowForTarget", {"targetId": self.current})
                if (win.get("bounds") or {}).get("windowState") == "minimized":
                    self.conn.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
            except CdpError:
                pass

    def snapshot(self, within: str | None = None, max_chars: int = 60000) -> dict:
        with self._page():
            here = self.here()
            arg = "null" if within is None else repr(str(within))
            r = self._js(f"snapshot({arg}, {int(max_chars)})")
            if not r["lines"] and within is None:   # not drawn yet: wait for it once, then read again
                self.wait_drawn()
                here = self.here()
                r = self._js(f"snapshot({arg}, {int(max_chars)})")
            out = {**here, "lines": r["lines"], "truncated": r["truncated"]}
            if not r["lines"] and within is None:
                out["lines"] = ["(" + EMPTY_NOTE + ")"]
            return out

    def text(self) -> dict:
        read = "document.body ? document.body.innerText : ''"
        with self._page():
            txt = (self._eval_page(read) or "").strip()
            if not txt:
                self.wait_drawn()
                txt = (self._eval_page(read) or "").strip()
            return {**self.here(), "text": txt or "(" + EMPTY_NOTE + ")"}

    def run_script(self, script: str, timeout: float = 60.0) -> Any:
        """A read route's script on the open page: `async (h) => data`, where h are the helpers in
        snapshot.js. It runs in the isolated world (the page's own scripts never see it); the answer
        comes back as JSON."""
        body = ("helpers.sleep(0).then(async () => { const fn = (" + script + "\n);"
                " if (typeof fn !== 'function') throw new Error('the script must be a function: async (h) => ({...})');"
                " const out = await fn(__rs.helpers); return JSON.stringify(out === undefined ? null : out); })")
        with self._page():
            raw = self._js(body, timeout=timeout)
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except ValueError as e:
            raise BrowserError(f"the script's answer isn't JSON: {e}") from e

    def read_page(self, url: str, script: str, timeout: float = 60.0) -> Any:
        """Run a read script on `url` in a tab of its own, in a window off the screen, then close it: reading a
        posting never takes the tab Claude or the user is working in, nor covers the screen. Off the screen,
        not minimized: a minimized window stops painting, and sites drawn by scripts (Feishu) then never
        draw the posting."""
        out = self.read_pages(url, [script], timeout=timeout)[0]
        if isinstance(out, Exception):
            raise out
        return out

    def _href(self) -> str:
        try:
            return str(self._eval_page("location.href", timeout=5))
        except CdpError:   # mid-navigation, or gone: not the page that was opened
            return ""

    def _open_here(self, url: str, timeout: float) -> None:
        try:
            r = self._send("Page.navigate", {"url": url}, timeout=timeout)
        except CdpError as e:
            raise PageNotOpened(f"{url} didn't open: {e}") from e
        if r.get("errorText"):   # net::ERR_CONNECTION_TIMED_OUT and the like
            raise PageNotOpened(f"couldn't open {url}: {r['errorText']}")
        self.wait_loaded()

    def read_pages(self, url: str, scripts: list[str], timeout: float = 60.0, open_timeout: float = 30.0) -> list[Any]:
        """read_page for several scripts on one address: the page opens once and they run one after another, so a
        route with 17 pages on one address opens it once, not 17 times. A script that took the page elsewhere
        gets it opened again for the next one. Each item is that script's answer, or the error it ran into: a
        script that fails is its trouble alone, and a page that doesn't open is PageNotOpened for each script
        it was to run."""
        if not url.startswith(("http://", "https://")):
            raise BrowserError("only http(s) addresses can be opened")
        out: list[Any] = []
        with self.lock:
            self.ensure()
            assert self.conn is not None
            tid = self._new_tab(newWindow=True, background=True)
        acting = self.acting(f"read:{tid}")
        acting.__enter__()
        with self._page():
            try:
                try:
                    win = self.conn.send("Browser.getWindowForTarget", {"targetId": tid})
                    self.conn.send("Browser.setWindowBounds", {"windowId": win["windowId"],
                                                               "bounds": {"left": -32000, "top": 0, "width": 1280, "height": 900}})
                except CdpError:
                    pass   # headless, or a browser that can't: it reads all the same
                self.current = tid
                opened = ""
                for script in scripts:
                    try:
                        if not opened or self._href() != opened:
                            self._open_here(url, open_timeout)
                            opened = self._href()
                        out.append(self.run_script(script, timeout=timeout))
                    except PageNotOpened as e:
                        out += [e] * (len(scripts) - len(out))
                        break
                    except (BrowserError, CdpError) as e:
                        out.append(e)
                return out
            finally:
                try:
                    if self.conn is not None:
                        self.conn.send("Target.closeTarget", {"targetId": tid})
                except CdpError:
                    pass
                with self.lock:
                    self.closed.add(tid)
                    self.pages.pop(tid, None)
                    sid = self.sessions.pop(tid, None)
                    if sid:
                        self.contexts.pop(sid, None)
                    self.current = None
                    self.work_locks.pop(f"read:{tid}", None)
                acting.__exit__(None, None, None)

    def _mouse_click(self, x: float, y: float, count: int = 1) -> None:
        base = {"x": x, "y": y, "button": "left", "clickCount": count}
        self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self._send("Input.dispatchMouseEvent", {"type": "mousePressed", **base})
        self._send("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})

    def _mouse_move(self, x: float, y: float) -> None:
        self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})

    def _reveal(self, ref: str) -> bool:
        """A control the site shows only while the mouse is over its row: put the mouse there, as a person would.
        True when it had to (and it showed)."""
        h = self._js(f"hoverPoint({int(ref)})")
        if not h:
            return False
        self._mouse_move(h["x"], h["y"])
        for _ in range(10):
            time.sleep(0.05)
            if self._js(f"isShown({int(ref)})"):
                return True
        raise BrowserError(f"ref {ref} stayed hidden with the mouse over its part of the page; "
                           "take a new snapshot, or hover a nearer ref first")

    def hover(self, ref: str) -> dict:
        with self._page():
            what = self._js(f"target({int(ref)})")
            if not self._reveal(ref):
                p = self._js(f"point({int(ref)})")
                self._mouse_move(p["x"], p["y"])
            self._pause(0.2, 0.4)
            return {**self.here(), "target": what}

    def click(self, ref: str) -> dict:
        with self._page():
            what = self._js(f"target({int(ref)})")
            self._reveal(ref)
            p = self._js(f"point({int(ref)})")
            self._pause(0.1, 0.3)
            self._mouse_click(p["x"], p["y"])
            self._pause()
            self.wait_loaded(8)
            out = {**self.here(), "target": what}
            if p.get("covered"):
                out["note"] = f"another element was on top at that spot: {p['covered']}"
            return out

    def key(self, combo: str) -> None:
        """'Enter', 'ctrl+a', 'shift+Tab', a single character…"""
        parts = combo.split("+")
        mods = sum(MODIFIERS.get(m.lower(), 0) for m in parts[:-1])
        name = parts[-1]
        if name in KEYS:
            key, code, vk, text = KEYS[name]
        elif len(name) == 1:
            key, code, vk, text = name, ("Key" + name.upper()) if name.isalpha() else "", ord(name.upper()), name
        else:
            raise BrowserError(f"unknown key {name!r}; use one of: {', '.join(KEYS)} or a single character")
        if mods & ~8:   # with ctrl / alt / meta the key is a command, not text
            text = ""
        down = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk,
                "modifiers": mods}
        if text:
            down["text"] = text
        self._send("Input.dispatchKeyEvent", down)
        self._send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": vk,
                                              "modifiers": mods})

    def press(self, combo: str) -> dict:
        with self._page():
            self.key(combo)
            self._pause()
            return self.here()

    def type(self, ref: str, text: str, clear: bool = True) -> dict:
        """Click into the field, empty it (select all + delete), then type `text` as real input."""
        with self._page():
            k = self._js(f"kind({int(ref)})")
            if k not in ("textbox", "combobox"):
                raise BrowserError(f"ref {ref} is a {k or 'element'}, not a text field")
            what = self._js(f"target({int(ref)})")
            p = self._js(f"point({int(ref)})")
            self._mouse_click(p["x"], p["y"])
            self._js(f"focus({int(ref)})")
            if clear:
                self.key("ctrl+a")
                self.key("Backspace")
            # a rich editor (contenteditable) takes a new line as Enter; a textarea takes "\n" as text
            chunks = text.split("\n") if self._js(f"rich({int(ref)})") else [text]
            for i, part in enumerate(chunks):
                if i:
                    self.key("Enter")
                for j in range(0, len(part), 24):   # in pieces, a little like typing
                    self._send("Input.insertText", {"text": part[j:j + 24]})
                    if self.pace:
                        time.sleep(random.uniform(0.02, 0.06) * self.pace)
            self._pause()
            got = self._js(f"read({int(ref)})")
            out = {**self.here(), "value": got["value"], "target": what}
            if got["value"].replace("\r", "").strip() != text.replace("\r", "").strip():
                out["note"] = "the field now holds something other than what was typed (the site may format or limit it)"
            return out

    def select(self, ref: str, option: str) -> dict:
        with self._page():
            what = self._js(f"target({int(ref)})")
            r = self._js(f"choose({int(ref)}, {option!r})")
            self._pause()
            return {**self.here(), "value": r["value"], "target": what}

    def scroll(self, ref: str | None = None, dy: int = 600) -> dict:
        with self._page():
            if ref:
                self._js(f"point({int(ref)})")
            else:
                self._eval_page(f"window.scrollBy(0, {int(dy)})")
            self._pause(0.2, 0.4)
            return self.here()

    def upload(self, ref: str, files: list[str]) -> dict:
        with self._page():
            if self._js(f"kind({int(ref)})") != "file":
                raise BrowserError(f"ref {ref} is not a file field")
            obj = self._js(f"el({int(ref)})", by_value=False)
            self._send("DOM.setFileInputFiles", {"files": files, "objectId": obj["objectId"]})
            self._pause()
            return {**self.here(), "target": self._js(f"target({int(ref)})")}

    def screenshot(self, path: Path, full: bool = False) -> dict:
        with self._page():
            params: dict[str, Any] = {"format": "png"}
            if full:
                params["captureBeyondViewport"] = True
            try:
                data = self._send("Page.captureScreenshot", params, timeout=20)["data"]
            except CdpError:
                if not self.shown:   # no window to be minimized: a page still drawing, so wait and try once more
                    time.sleep(1.0)
                    data = self._send("Page.captureScreenshot", params, timeout=30)["data"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(data))
                    return {**self.here(), "path": str(path)}
                # a minimized window draws nothing to capture: show it, then try once more
                self.front()
                time.sleep(0.5)
                data = self._send("Page.captureScreenshot", params, timeout=30)["data"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(data))
            return {**self.here(), "path": str(path)}

    def tabs(self) -> list[dict]:
        self.ensure()
        return [{"index": i, "url": p.get("url", ""), "title": p.get("title", ""), "current": tid == self.current}
                for i, (tid, p) in enumerate(self.pages.items())]

    def use_tab(self, index: int) -> dict:
        with self._page(), self.lock:
            self.ensure()
            ids = list(self.pages)
            if not 0 <= index < len(ids):
                raise BrowserError(f"there is no tab {index}")
            if self._taken(ids[index], self.work):
                raise BrowserError(f"other work (another chat, or a check) is using tab {index} right now; "
                                   "stay in your own tab, or open a new one with `open <url> --new-tab`")
            self.current = ids[index]
            return self.here()

    def back(self) -> dict:
        with self._page():
            self._eval_page("history.back()")
            self.wait_loaded(10)
            return self.here()
