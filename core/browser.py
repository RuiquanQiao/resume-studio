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


class BrowserError(Exception):
    """Something the caller (and the user) should read."""


COMMANDS = {"open", "snapshot", "text", "click", "type", "select", "press", "scroll", "upload", "screenshot", "tabs",
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
    elif cmd in ("click", "type", "select", "upload", "scroll") and a:
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
                 pace: float = 1.0):
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
        self.lock = threading.RLock()
        self.pages: dict[str, dict] = {}      # targetId -> {url, title, opener}
        self.current: str | None = None
        self.sessions: dict[str, str] = {}    # targetId -> sessionId
        self.contexts: dict[str, int] = {}    # sessionId -> isolated world's execution context
        self.on_change: Callable[[], None] | None = None

    # ---- lifecycle -----------------------------------------------------------
    def profile_dir(self, kind: str) -> Path:
        return self.profile_root / kind

    def status(self) -> dict:
        found = find_browser(self.prefer)
        page = self.pages.get(self.current or "") or {}
        if self.state == "running" and (self.conn is None or self.conn.closed):
            self._lost()
        return {"state": self.state, "error": self.error, "browser": NAMES.get(self.kind or (found or ("", ""))[0], ""),
                "found": bool(found), "url": page.get("url", ""), "title": page.get("title", ""),
                "tabs": sum(1 for p in self.pages.values() if p.get("type") == "page"), "signin_url": self.signin_url}

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    def _lost(self) -> None:
        self.state, self.conn = "off", None
        self.pages, self.sessions, self.contexts, self.current = {}, {}, {}, None

    def ensure(self) -> None:
        """Running and connected, starting it (or reattaching to the one left open) when needed."""
        with self.lock:
            if self.conn is not None and not self.conn.closed:
                return
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
            try:
                port = self._live_port(port_file)
                if port is None:
                    port_file.unlink(missing_ok=True)
                    self._launch(exe, profile)
                    port = self._wait_port(port_file)
                self._connect(port)
            except (BrowserError, CdpError, OSError) as e:
                self.state, self.error = "error", str(e)
                self._changed()
                raise BrowserError(str(e)) from e
            self.state = "running"
            self._changed()

    def _live_port(self, port_file: Path) -> int | None:
        try:
            port = int(port_file.read_text(encoding="utf-8").split()[0])
            version_info(port, timeout=1.0)
            return port
        except (OSError, ValueError, IndexError):
            return None

    def _launch(self, exe: str, profile: Path) -> None:
        profile.mkdir(parents=True, exist_ok=True)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        args = [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", "--no-first-run",
                "--no-default-browser-check", "--disable-search-engine-choice-screen",
                # the user watches Resume Studio, so this window is often behind it: keep it rendering
                # (otherwise screenshots stall and pages pause their timers)
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling"]
        if self.headless:
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
        conn = Connection(info["webSocketDebuggerUrl"])
        conn.listeners.append(self._on_event)
        self.conn = conn
        conn.send("Target.setDiscoverTargets", {"discover": True})
        for t in conn.send("Target.getTargets").get("targetInfos", []):
            self._track(t)
        pages = [tid for tid, p in self.pages.items() if p["type"] == "page"]
        self.current = pages[-1] if pages else None
        if self.current is None:
            self.current = conn.send("Target.createTarget", {"url": "about:blank"})["targetId"]

    def _track(self, t: dict) -> None:
        if t.get("type") != "page":
            return
        self.pages[t["targetId"]] = {"type": "page", "url": t.get("url", ""), "title": t.get("title", ""),
                                     "opener": t.get("openerId")}

    def _on_event(self, msg: dict) -> None:
        method, p = msg.get("method"), msg.get("params") or {}
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            info = p.get("targetInfo") or {}
            known = info.get("targetId") in self.pages
            self._track(info)
            # a page opened from the one being worked on (a link with target=_blank): follow it
            if method == "Target.targetCreated" and not known and info.get("type") == "page" and info.get("openerId") == self.current:
                self.current = info["targetId"]
            self._changed()
        elif method == "Target.targetDestroyed":
            tid = p.get("targetId")
            self.pages.pop(tid, None)
            sid = self.sessions.pop(tid, None)
            if sid:
                self.contexts.pop(sid, None)
            if tid == self.current:
                rest = list(self.pages)
                self.current = rest[-1] if rest else None
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

    # ---- the page being worked on ---------------------------------------------
    def _session(self) -> str:
        self.ensure()
        assert self.conn is not None
        if self.current is None or self.current not in self.pages:
            pages = list(self.pages)
            self.current = pages[-1] if pages else self.conn.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = self.sessions.get(self.current)
        if sid is None:
            sid = self.conn.send("Target.attachToTarget", {"targetId": self.current, "flatten": True})["sessionId"]
            self.sessions[self.current] = sid
            self.conn.send("Page.enable", session=sid)
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
        with self.lock:
            sid = self._session()
            assert self.conn is not None
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

    def _eval_page(self, expression: str) -> Any:
        r = self._send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    def _pause(self, lo: float = 0.25, hi: float = 0.7) -> None:
        if self.pace:
            time.sleep(random.uniform(lo, hi) * self.pace)

    def here(self) -> dict:
        with self.lock:
            self._session()
            url = self._eval_page("location.href") or ""
            title = self._eval_page("document.title") or ""
            if self.current in self.pages:
                self.pages[self.current].update(url=url, title=title)
            return {"url": url, "title": title}

    def wait_loaded(self, timeout: float = 20.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self._eval_page("document.readyState") == "complete":
                    break
            except CdpError:
                pass
            time.sleep(0.2)
        time.sleep(0.4 if self.pace else 0.05)   # single-page apps render a moment after "load"

    # ---- what callers use ----------------------------------------------------
    def open(self, url: str, new_tab: bool = False) -> dict:
        if not url.startswith(("http://", "https://", "about:")):
            raise BrowserError("only http(s) addresses can be opened")
        with self.lock:
            self.ensure()
            assert self.conn is not None
            if new_tab:
                self.current = self.conn.send("Target.createTarget", {"url": "about:blank"})["targetId"]
            r = self._send("Page.navigate", {"url": url})
            if r.get("errorText"):
                raise BrowserError(f"couldn't open {url}: {r['errorText']}")
            self.wait_loaded()
            return self.here()

    def front(self) -> None:
        """Show the page being worked on (the user signs in there, or watches)."""
        with self.lock:
            self._send("Page.bringToFront")
            try:
                assert self.conn is not None
                win = self.conn.send("Browser.getWindowForTarget", {"targetId": self.current})
                if (win.get("bounds") or {}).get("windowState") == "minimized":
                    self.conn.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
            except CdpError:
                pass

    def snapshot(self, within: str | None = None, max_chars: int = 60000) -> dict:
        with self.lock:
            here = self.here()
            arg = "null" if within is None else repr(str(within))
            r = self._js(f"snapshot({arg}, {int(max_chars)})")
            return {**here, "lines": r["lines"], "truncated": r["truncated"]}

    def text(self) -> dict:
        with self.lock:
            return {**self.here(), "text": self._eval_page("document.body ? document.body.innerText : ''") or ""}

    def run_script(self, script: str, timeout: float = 60.0) -> Any:
        """A read route's script on the open page: `async (h) => data`, where h are the helpers in
        snapshot.js. It runs in the isolated world (the page's own scripts never see it); the answer
        comes back as JSON."""
        body = ("helpers.sleep(0).then(async () => { const fn = (" + script + "\n);"
                " if (typeof fn !== 'function') throw new Error('the script must be a function: async (h) => ({...})');"
                " const out = await fn(__rs.helpers); return JSON.stringify(out === undefined ? null : out); })")
        with self.lock:
            raw = self._js(body, timeout=timeout)
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except ValueError as e:
            raise BrowserError(f"the script's answer isn't JSON: {e}") from e

    def _mouse_click(self, x: float, y: float, count: int = 1) -> None:
        base = {"x": x, "y": y, "button": "left", "clickCount": count}
        self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self._send("Input.dispatchMouseEvent", {"type": "mousePressed", **base})
        self._send("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})

    def click(self, ref: str) -> dict:
        with self.lock:
            what = self._js(f"target({int(ref)})")
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
        with self.lock:
            self.key(combo)
            self._pause()
            return self.here()

    def type(self, ref: str, text: str, clear: bool = True) -> dict:
        """Click into the field, empty it (select all + delete), then type `text` as real input."""
        with self.lock:
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
        with self.lock:
            what = self._js(f"target({int(ref)})")
            r = self._js(f"choose({int(ref)}, {option!r})")
            self._pause()
            return {**self.here(), "value": r["value"], "target": what}

    def scroll(self, ref: str | None = None, dy: int = 600) -> dict:
        with self.lock:
            if ref:
                self._js(f"point({int(ref)})")
            else:
                self._eval_page(f"window.scrollBy(0, {int(dy)})")
            self._pause(0.2, 0.4)
            return self.here()

    def upload(self, ref: str, files: list[str]) -> dict:
        with self.lock:
            if self._js(f"kind({int(ref)})") != "file":
                raise BrowserError(f"ref {ref} is not a file field")
            obj = self._js(f"el({int(ref)})", by_value=False)
            self._send("DOM.setFileInputFiles", {"files": files, "objectId": obj["objectId"]})
            self._pause()
            return {**self.here(), "target": self._js(f"target({int(ref)})")}

    def screenshot(self, path: Path, full: bool = False) -> dict:
        with self.lock:
            params: dict[str, Any] = {"format": "png"}
            if full:
                params["captureBeyondViewport"] = True
            try:
                data = self._send("Page.captureScreenshot", params, timeout=20)["data"]
            except CdpError:
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
        with self.lock:
            self.ensure()
            ids = list(self.pages)
            if not 0 <= index < len(ids):
                raise BrowserError(f"there is no tab {index}")
            self.current = ids[index]
            return self.here()

    def back(self) -> dict:
        with self.lock:
            self._eval_page("history.back()")
            self.wait_loaded(10)
            return self.here()
