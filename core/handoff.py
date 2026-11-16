"""Who drives a browser tab, the AI or the user, and the bar on the page that shows it (agentbar.js).

The way Gemini in Chrome, ChatGPT agent and Claude in Chrome do it: while the AI works, the page glows and a bar
says what it is doing, with "Take over" and "Stop". The user takes the tab with that button, or simply by clicking
or typing in it; a sign-in the AI asks for hands the tab to the user too. While the tab is the user's, every command
of the AI's on it is refused here, in code (UserHasControl), not by asking the AI nicely: it waits (`browser.py
wait` / `wait-signed-in`) until the user hands the tab back with "Hand back to AI" / "I've signed in", or a recorded
sign-in check (autofill/signin.py) says the site has them signed in.

Resume Studio tells its own input from the user's by time: every Input.* command it sends is noted with when it
went, and only the page's trusted clicks and keys outside those moments are the user's.

The bar is drawn by polling: a thread asks each tab that should show one, a few times a second, in an isolated
world of its own. No Runtime.enable and no binding, so the page sees nothing it could tell a driven browser by.
"""
from __future__ import annotations

import json
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .cdp import CdpError
from .i18n import t

if TYPE_CHECKING:
    from .browser import ManagedBrowser

BAR_JS = (Path(__file__).with_name("agentbar.js")).read_text(encoding="utf-8")
BAR_WORLD = "resume-studio-bar"
POLL = 0.25               # seconds between two looks at a tab's bar
AI_INPUT_GRACE = 0.6      # a page event this long after Resume Studio's own input is still that input's
LINGER = 1.5              # seconds the bar keeps saying what the AI did once that command is over


@dataclass
class Control:
    mode: str = "ai"                 # "ai" | "user" (the user took over) | "signin" (the user signs in)
    since: float = 0.0
    reason: str = ""                 # user: "button" | "input"; signin: "signin"
    site: str = ""                   # signin: the site's name, for the bar
    detected: str = ""               # signin: "" | "likely" (sign-in traffic seen on the page)
    back: str = ""                   # how the tab came back to the AI: "button" | "check" | "switch" | "end"
    back_at: float = 0.0
    pos: dict | None = None          # where the user dragged the bar


@dataclass
class _Tab:
    ctx: int | None = None           # the bar's world in this tab's page
    shown: bool = False              # the bar was drawn here (hidden again once it shouldn't show)
    cursor: dict | None = None
    sent: str = ""


class Handoff:
    def __init__(self, browser: "ManagedBrowser"):
        self.b = browser
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.tabs: dict[str, Control] = {}             # targetId -> who drives it (none listed: the AI)
        self.activity: dict[str, tuple[str, float]] = {}   # targetId -> (what the AI does there, when it ended; 0: now)
        self.ai_input: dict[str, deque] = {}           # targetId -> (start, end) of Resume Studio's own input
        self.bars: dict[str, _Tab] = {}
        self.always = False                            # tests: a bar in a windowless browser too
        self.cli = "browser.py"                        # how the AI runs the command, for the texts it gets
        self.on_change: Callable[[str | None, Control], None] | None = None   # (work key, its control): the chat says it
        self.on_stop: Callable[[str], None] | None = None                   # the user pressed Stop: end that work
        self.stoppable: Callable[[str], bool] = lambda key: False           # work the bar's Stop can end (a chat's reply)
        self.thread: threading.Thread | None = None
        self.local = threading.local()
        self._seq = 0

    # ---- whose tab ---------------------------------------------------------------
    def get(self, tid: str | None) -> Control:
        with self.lock:
            return self.tabs.get(tid or "") or Control()

    def key_of(self, tid: str) -> str | None:
        """The work (a chat's id, "push:<site>") whose tab this is; None for the window's own or nobody's."""
        for k, v in list(self.b.tab_of.items()):
            if v == tid and k is not None and not str(k).startswith("read:"):
                return k
        return None

    def exempt(self) -> "_Exempt":
        """`with handoff.exempt():` around what may look at a tab that is the user's (the wait itself, a sign-in check)."""
        return _Exempt(self)

    def gate(self, tid: str | None, key: str | None) -> None:
        """Refuse a command of work `key` on tab `tid` when the tab is the user's."""
        if not tid or key is None or str(key).startswith("read:") or getattr(self.local, "exempt", 0):
            return
        from .browser import UserHasControl
        c = self.get(tid)
        if c.mode == "ai":
            return
        b = self.cli
        if c.mode == "signin":
            raise UserHasControl("signin", f"paused: the user is signing in to {c.site or 'the site'} on this page, and the tab is "
                                           f"theirs until they choose \"I've signed in\". Run `{b} wait-signed-in` (with the "
                                           "site's id when it is on the list) and go on once it returns; never end your reply "
                                           "to wait.")
        raise UserHasControl("user", f"paused: the user has taken over this tab ({'the Take over button' if c.reason == 'button' else 'they clicked or typed in the page'}). "
                                     f"Your browser commands are refused until they hand it back. Run `{b} wait`: it returns "
                                     "once they do. Then take a new snapshot before anything else: the page may have changed. "
                                     "Never end your reply to wait.")

    def _set(self, tid: str, c: Control) -> None:
        with self.cond:
            old = self.tabs.get(tid)
            if old is not None and c.pos is None:
                c.pos = old.pos          # where the user put the bar stays, whoever drives
            self.tabs[tid] = c
            self.cond.notify_all()
        if self.on_change:
            try:
                self.on_change(self.key_of(tid), c)
            except Exception:  # noqa: BLE001 - telling the chat must never break the browser
                pass

    def take(self, tid: str, reason: str = "button") -> None:
        """The tab is the user's: they pressed Take over, or used the page."""
        c = self.get(tid)
        if c.mode != "ai":
            return
        self._set(tid, Control(mode="user", since=time.time(), reason=reason, pos=c.pos))

    def give(self, tid: str, how: str = "button") -> None:
        """The tab is the AI's again."""
        if self.get(tid).mode == "ai":
            return
        self._set(tid, Control(mode="ai", back=how, back_at=time.time()))

    def ask_sign_in(self, tid: str, site: str) -> None:
        """The AI waits for the user to sign in: the tab is theirs until they say so (or a sign-in check does)."""
        c = self.get(tid)
        if c.mode == "signin":
            if site and c.site != site:
                c.site = site
                self._set(tid, c)
            return
        self._set(tid, Control(mode="signin", since=time.time(), reason="signin", site=site, pos=c.pos))

    def detected(self, tid: str, what: str) -> None:
        c = self.get(tid)
        if c.mode == "signin" and c.detected != what:
            c.detected = what
            self._set(tid, c)

    def release(self, key: str) -> None:
        """Work `key` ended (the chat's reply is over): its tabs are nobody's to hold any more."""
        for tid in [v for k, v in list(self.b.tab_of.items()) if k == key]:
            if self.get(tid).mode != "ai":
                self.give(tid, "end")
            self.activity.pop(tid, None)

    def wait_ai(self, tid: str, timeout: float) -> Control:
        """Until the tab is the AI's again (or `timeout`): its control then."""
        end = time.time() + timeout
        with self.cond:
            while (self.tabs.get(tid) or Control()).mode != "ai":
                left = end - time.time()
                if left <= 0 or tid not in self.b.pages:
                    break
                self.cond.wait(min(left, 1.0))
            return self.tabs.get(tid) or Control()

    def forget(self, tid: str) -> None:
        """The tab closed."""
        with self.cond:
            self.tabs.pop(tid, None)
            self.cond.notify_all()
        self.activity.pop(tid, None)
        self.ai_input.pop(tid, None)
        self.bars.pop(tid, None)

    def reset(self) -> None:
        """The browser closed: no tab is anyone's."""
        with self.cond:
            self.tabs.clear()
            self.cond.notify_all()
        self.activity.clear()
        self.ai_input.clear()
        self.bars.clear()

    # ---- what the AI does, for the bar ----------------------------------------------
    def doing(self, tid: str | None, text: str | None) -> None:
        """What the AI does on the tab now; None when that command is over. The bar keeps saying it a moment
        longer, so commands one after another read as one action rather than a flicker."""
        if not tid:
            return
        if text:
            self.activity[tid] = (text, 0.0)
        elif tid in self.activity:
            self.activity[tid] = (self.activity[tid][0], time.time())

    def doing_now(self, tid: str) -> str:
        text, ended = self.activity.get(tid) or ("", 0.0)
        return text if text and (not ended or time.time() - ended < LINGER) else ""

    def input_sent(self, tid: str | None, start: float, end: float) -> None:
        if not tid:
            return
        q = self.ai_input.setdefault(tid, deque(maxlen=64))
        q.append((start - 0.05, end + AI_INPUT_GRACE))

    def ours(self, tid: str, at: float) -> bool:
        return any(s <= at <= e for s, e in list(self.ai_input.get(tid) or ()))

    def tap(self, tid: str | None, x: float, y: float) -> None:
        """Where the AI clicks: the bar shows a ring there, at once."""
        bar = self.bars.get(tid or "")
        if not tid or not bar or not bar.shown:
            return
        self._seq += 1
        bar.cursor = {"x": round(x), "y": round(y), "seq": self._seq}
        self._call(tid, f"__rsBar.through(1500) && __rsBar.tap({round(x)}, {round(y)})")

    def through(self, tid: str | None) -> None:
        """The AI is about to find and click something: the bar lets its hit test pass for a moment."""
        bar = self.bars.get(tid or "")
        if tid and bar and bar.shown:
            self._call(tid, "__rsBar.through(2500)")

    def hidden(self, tid: str | None) -> "_Hidden":
        """`with handoff.hidden(tid):` around a screenshot: the page without the bar."""
        return _Hidden(self, tid)

    # ---- the bar ---------------------------------------------------------------------
    def wants_bar(self, tid: str) -> bool:
        if not (self.b.shown or self.always) or tid not in self.b.pages:
            return False
        if self.get(tid).mode != "ai":
            return True
        key = self.key_of(tid)
        return key is not None and self.b._busy_with(key)

    def state(self, tid: str) -> dict:
        c = self.get(tid)
        bar = self.bars.get(tid) or _Tab()
        key = self.key_of(tid)
        try:
            can_stop = key is not None and self.stoppable(key)
        except Exception:  # noqa: BLE001
            can_stop = False
        # Stop only where it ends something: a chat's reply (a recipe replaying a change has no reply to stop)
        stop = [{"id": "stop", "label": t("Stop"), "title": t("Stop the AI's reply. The page stays as it is.")}] if can_stop else []
        if c.mode == "signin":
            # the password saved here is what lets Resume Studio sign in again by itself (autofill/autologin.py)
            sub, good = t("If the browser offers to save the password, choose Save: from then on Resume Studio signs in to this site by itself."), False
            if c.detected == "likely":
                sub, good = t("Sign-in activity detected on this page."), True
            out = {"mode": "signin", "text": t("Sign in to {site} on this page. The AI continues once you choose “I've signed in”.",
                                               site=c.site or t("the site")),
                   "sub": sub, "sub_good": good, "glow": False, "acting": False,
                   "buttons": [{"id": "done", "label": t("I've signed in"), "primary": True,
                                "title": t("Hand the page back to the AI to continue.")}, *stop]}
        elif c.mode == "user":
            out = {"mode": "user", "text": t("You have taken over this page. The AI is paused."), "sub": "",
                   "glow": False, "acting": False,
                   "buttons": [{"id": "give", "label": t("Hand back to AI"), "primary": True,
                                "title": t("The AI continues from the page as you leave it.")}, *stop]}
        else:
            what = self.doing_now(tid)
            out = {"mode": "ai", "text": what or t("The AI is deciding the next step."), "sub": "", "glow": True,
                   "acting": bool(what),
                   "buttons": [{"id": "take", "label": t("Take over"),
                                "title": t("Pause the AI and use this page yourself. Clicking or typing in the page does the same.")}, *stop]}
        out.update(visible=True, label=t("Resume Studio: AI control of this page"), pos=c.pos, cursor=bar.cursor)
        return out

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            # the thread holds the handoff only weakly: a browser nobody uses any more is freed (with its process
            # handle) as before, instead of living on in this thread until the program exits
            self.thread = threading.Thread(target=_loop, args=(weakref.ref(self),), daemon=True, name="browser-bar")
            self.thread.start()

    def step(self) -> bool:
        """One look at the bars; False once the browser is gone for good (the thread ends)."""
        conn = self.b.conn
        if conn is None or conn.closed:
            if self.b.state not in ("running", "starting"):
                with self.lock:
                    self.thread = None
                return False
            return True
        try:
            self.poll()
        except Exception:  # noqa: BLE001 - the bar is a courtesy: never let it break the browser
            pass
        return True

    def poll(self) -> None:
        for tid in list(self.b.pages):
            want = self.wants_bar(tid)
            bar = self.bars.get(tid)
            if not want and not (bar and bar.shown):
                continue
            bar = self.bars.setdefault(tid, _Tab())
            st = self.state(tid) if want else {"visible": False}
            call = "__rsBar.sync(" + json.dumps(st, ensure_ascii=False) + ")"
            events = self._call(tid, call)
            if events is None and want:   # a new page: the bar isn't there yet (the script goes over once per page)
                events = self._call(tid, call, install=True)
            if events is None:
                continue
            bar.shown = want
            bar.sent = st.get("mode", "")
            for ev in events if isinstance(events, list) else []:
                self._event(tid, ev)

    def _event(self, tid: str, ev: dict) -> None:
        kind = ev.get("t")
        if kind == "button":
            what = ev.get("id")
            if what == "take":
                self.take(tid, "button")
            elif what in ("give", "done"):
                self.give(tid, "button")
            elif what == "stop":
                key = self.key_of(tid)
                if self.get(tid).mode != "ai":
                    self.give(tid, "end")
                if key and self.on_stop:
                    try:
                        self.on_stop(key)
                    except Exception:  # noqa: BLE001
                        pass
        elif kind == "input":
            at = float(ev.get("at") or 0) / 1000.0
            if self.get(tid).mode == "ai" and not self.ours(tid, at):
                key = self.key_of(tid)
                if key is not None and self.b._busy_with(key):
                    self.take(tid, "input")
        elif kind == "pos":
            pos = None if ev.get("x") is None else {"x": int(ev["x"]), "y": int(ev["y"])}
            with self.lock:
                self.tabs.setdefault(tid, Control()).pos = pos

    def _context(self, tid: str, sid: str, fresh: bool = False) -> int | None:
        bar = self.bars.setdefault(tid, _Tab())
        if bar.ctx is not None and not fresh:
            return bar.ctx
        conn = self.b.conn
        if conn is None:
            return None
        frame = conn.send("Page.getFrameTree", session=sid, timeout=3)["frameTree"]["frame"]["id"]
        bar.ctx = conn.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": BAR_WORLD,
                                                         "grantUniveralAccess": False}, session=sid, timeout=3)["executionContextId"]
        return bar.ctx

    def _call(self, tid: str, expr: str, install: bool = False) -> Any:
        """`expr` in the bar's world of tab `tid` (installing the bar there first when `install`); None when the
        page can't answer now (busy, navigating, a page that isn't attached)."""
        conn = self.b.conn
        sid = self.b.sessions.get(tid)
        if conn is None or conn.closed or not sid:
            return None
        code = (BAR_JS + "\n;" if install else "globalThis.__rsBar && ") + expr
        for attempt in (0, 1):
            try:
                ctx = self._context(tid, sid, fresh=attempt == 1)
                r = conn.send("Runtime.evaluate", {"expression": code, "contextId": ctx, "returnByValue": True},
                              session=sid, timeout=3)
            except CdpError:
                if attempt == 0:
                    continue   # the page navigated: its new document has a new world
                return None
            if r.get("exceptionDetails"):
                if attempt == 0:
                    continue
                return None
            return (r.get("result") or {}).get("value")
        return None

    def page_changed(self, sid: str) -> None:
        """A tab's page navigated: the bar's world there is gone (the next poll draws it anew)."""
        for tid, s in list(self.b.sessions.items()):
            if s == sid and tid in self.bars:
                self.bars[tid].ctx = None


def _loop(ref: "weakref.ref[Handoff]") -> None:
    while True:
        h = ref()
        if h is None or not h.step():
            return
        del h
        time.sleep(POLL)


class _Exempt:
    def __init__(self, h: Handoff):
        self.h = h

    def __enter__(self):
        self.h.local.exempt = getattr(self.h.local, "exempt", 0) + 1
        return self.h

    def __exit__(self, *exc):
        self.h.local.exempt -= 1
        return False


class _Hidden:
    def __init__(self, h: Handoff, tid: str | None):
        self.h, self.tid = h, tid
        self.did = False

    def __enter__(self):
        bar = self.h.bars.get(self.tid or "")
        if self.tid and bar and bar.shown:
            self.did = bool(self.h._call(self.tid, "__rsBar.hide(true)"))
        return self

    def __exit__(self, *exc):
        if self.did:
            self.h._call(self.tid, "__rsBar.hide(false)")
        return False


@dataclass
class NetLog:
    """What one tab's page asked the network for lately: `browser.py requests`, and sign-in traffic for the bar."""
    entries: deque = field(default_factory=lambda: deque(maxlen=300))
    by_id: dict = field(default_factory=dict)

    def event(self, method: str, p: dict) -> None:
        if method == "Network.requestWillBeSent":
            req = p.get("request") or {}
            e = {"id": p.get("requestId"), "url": str(req.get("url") or ""), "method": str(req.get("method") or "GET"),
                 "type": str(p.get("type") or ""), "at": time.time(), "status": None, "mime": ""}
            self.entries.append(e)
            self.by_id[e["id"]] = e
            if len(self.by_id) > 400:
                for k in list(self.by_id)[:100]:
                    self.by_id.pop(k, None)
        elif method == "Network.responseReceived":
            e = self.by_id.get(p.get("requestId"))
            if e is not None:
                resp = p.get("response") or {}
                e["status"] = resp.get("status")
                e["mime"] = str(resp.get("mimeType") or "")

    def recent(self, since: float = 0.0, api_only: bool = True) -> list[dict]:
        out = [e for e in list(self.entries) if e["at"] >= since]
        if api_only:
            out = [e for e in out if e["type"] in ("XHR", "Fetch") or "json" in e["mime"]]
        return out
