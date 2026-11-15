"""Who drives a browser tab, the AI or the user (core/handoff.py), and whether the user is signed in, asked of the
site itself (autofill/signin.py). Against a fake careers site in a headless Edge.

零跑 (北森, 2026-11-15): the site's "登录/注册" opened a dialog on the same page with a phone number and an SMS
code and no password field; the wait took that for "signed in", and the AI went on clicking while the user was
typing the code. Then the AI switched the browser to sign-in mode by itself, which closed the page the user was on.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.i18n import t
from test_autofill import SITES_DIR, cli, fake_site, headless, live, out, wait_for  # noqa: F401  (fixtures)


@pytest.fixture
def auth_site():
    """The fake sites, plus a server that knows who is signed in: /api/me answers the user while the `sess`
    cookie is set, and POST /api/login sets it (the SMS code accepted)."""
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(SITES_DIR), **k)

        def log_message(self, *a, **k):
            pass

        def _json(self, code: int, data: dict, cookie: str = "") -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/me"):
                signed = "sess=ok" in (self.headers.get("Cookie") or "")
                return self._json(200 if signed else 401, {"user": {"name": "Alex", "id": 7} if signed else None})
            super().do_GET()

        def do_POST(self):
            if self.path.startswith("/api/login"):
                return self._json(200, {"ok": True}, "sess=ok; Path=/; HttpOnly")
            self.send_response(404)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def tab_of(live, chat):
    b = live.inst.browser
    return b.tab_of[chat]


def raw(b, tid, method, params):
    """A command straight to the page, not through Resume Studio's own input path: the user's hand."""
    return b.conn.send(method, params, session=b.sessions[tid])


def user_clicks(b, tid, x=600, y=500):
    for kind in ("mousePressed", "mouseReleased"):
        raw(b, tid, "Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})


def sign_in_on_the_site(b, tid, base):
    """What the user does by hand: the code accepted, the site sets its session cookie."""
    raw(b, tid, "Runtime.evaluate", {"expression": f"fetch('{base}/api/login', {{method: 'POST', credentials: 'include'}}).then(() => 1)",
                                     "awaitPromise": True})


CHECK = ("async (h) => { const r = await fetch('/api/me', {credentials: 'include'}); const j = await r.json(); "
         "return {signed_in: !!(j && j.user)}; }")


def test_a_sign_in_without_a_password_field_waits_for_the_user(live, data_file, auth_site):
    """The dialog (phone + SMS code, no password, same address) is not "signed in": the tab is the user's until they
    say so, the AI's commands on it are refused meanwhile, and the user's "I've signed in" gives it back. The old wait
    returned at once here (no password field, no /login in the address) and the AI clicked on."""
    b = live.inst.browser
    chat = live.inst.chats.store.create(title="Read 零跑汽车")["id"]
    home = auth_site + "/auth/home.html"
    snap = out(cli(data_file, "open", home, chat=chat))
    assert "ok:" in snap
    login = next(l for l in out(cli(data_file, "snapshot", chat=chat)).splitlines() if "登录/注册" in l)
    assert "ok:" in out(cli(data_file, "click", login.split("]")[0].strip(" ["), chat=chat))

    live.inst.fill.sign_in_wait = 2.0
    t0 = time.time()
    r = out(cli(data_file, "wait-signed-in", chat=chat))
    assert "not signed in yet" in r and time.time() - t0 >= 1.5, r       # it waited: no answer without the user
    tid = tab_of(live, chat)
    assert b.handoff.get(tid).mode == "signin"

    # the AI can't act on the page while the user signs in, whatever it tries
    for args in (["snapshot"], ["click", "1"], ["open", home], ["screenshot"], ["requests"]):
        p = cli(data_file, *args, chat=chat)
        assert p.returncode != 0 and "paused" in out(p) and "wait-signed-in" in out(p), (args, out(p))
    # nor switch the browser to sign-in mode, which would close the page the user is on
    p = cli(data_file, "sign-in", home, chat=chat)
    assert p.returncode != 0
    assert live.req("POST", "/api/browser/do", {"cmd": "sign-in", "url": home})[0] == 409
    assert b.state == "running"

    # the user signs in and says so: the wait returns, the tab is the AI's, and it is told to record a check
    sign_in_on_the_site(b, tid, auth_site)
    live.inst.fill.sign_in_wait = 30.0
    threading.Timer(1.5, lambda: live.req("POST", "/api/browser/control", {"chat": chat, "action": "give"})).start()
    t0 = time.time()
    r = out(cli(data_file, "wait-signed-in", chat=chat))
    assert "handed the tab back" in r and "sign-in-check" in r and time.time() - t0 < 15, r
    assert b.handoff.get(tid).mode == "ai"
    assert "登录/注册" in out(cli(data_file, "snapshot", chat=chat))


def test_no_code_arriving_during_the_wait_is_the_users_switch(live, data_file, auth_site):
    """While the tab is handed over, the user picks "No code arriving?" (the browser without remote control) and later
    switches back: the wait follows them through it, instead of taking the restarted browser for a closed tab."""
    b = live.inst.browser
    chat = live.inst.chats.store.create(title="Read 零跑汽车")["id"]
    home = auth_site + "/auth/home.html"
    cli(data_file, "open", home, chat=chat)
    live.inst.fill.sign_in_wait = 60.0

    def user():
        wait_for(lambda: b.tab_of.get(chat) and b.handoff.get(b.tab_of[chat]).mode == "signin", "the tab handed over")
        assert live.req("POST", "/api/browser/sign-in", {"url": home})[0] == 200
        wait_for(lambda: b.state == "signin", "sign-in mode")
        time.sleep(2.0)
        assert live.req("POST", "/api/browser/sign-in/done", {})[0] == 200
    threading.Thread(target=user, daemon=True).start()
    r = out(cli(data_file, "wait-signed-in", chat=chat))
    assert "switched back" in r and "closed" not in r, r
    p = cli(data_file, "snapshot", chat=chat)
    assert p.returncode == 0 and "登录/注册" in out(p), out(p)
    live.req("POST", "/api/browser/stop", {})


def test_a_recorded_sign_in_check_asks_the_site(live, data_file, auth_site):
    """`requests` shows the page's own "who am I" call; a check built on it is tried both ways before it is kept
    (true signed in, false in a fresh browser with no sign-in), and from then on the wait asks the site."""
    b = live.inst.browser
    chat = live.inst.chats.store.create(title="Read 零跑汽车")["id"]
    home = auth_site + "/auth/home.html"
    cli(data_file, "open", home, chat=chat)
    tid = tab_of(live, chat)
    wait_for(lambda: "/api/me" in out(cli(data_file, "requests", chat=chat)), "the page's own call")
    assert "GET 401" in out(cli(data_file, "requests", chat=chat))

    # signed out, a check can't be recorded (it must say true where the user is signed in)
    p = cli(data_file, "sign-in-check", "--stdin", stdin=json.dumps({"script": CHECK}), chat=chat)
    assert p.returncode != 0 and "didn't say signed_in true" in out(p), out(p)
    sign_in_on_the_site(b, tid, auth_site)
    # a "check" that reads what every visitor has is refused: it says true without a sign-in too
    bogus = "async (h) => { await fetch('/auth/home.html'); return {signed_in: true}; }"
    p = cli(data_file, "sign-in-check", "--stdin", stdin=json.dumps({"script": bogus}), chat=chat)
    assert p.returncode != 0 and "didn't say signed_in false" in out(p), out(p)
    # one that only reads the page is no answer at all
    p = cli(data_file, "sign-in-check", "--stdin", stdin=json.dumps({"script": "async (h) => ({signed_in: true})"}), chat=chat)
    assert p.returncode != 0 and "ask the site's server" in out(p), out(p)
    r = out(cli(data_file, "sign-in-check", "--stdin", stdin=json.dumps({"script": CHECK}), chat=chat))
    assert "saved" in r and "true here and false without" in r, r
    assert "domain:127.0.0.1" in json.loads((live.inst.fill.root / "sign-in-checks.json").read_text(encoding="utf-8"))
    assert b.cookies() and any(c["name"] == "sess" for c in b.cookies())   # the fresh context left the real one alone

    # signed in: the wait answers at once, by the site, and never hands the tab over
    t0 = time.time()
    r = out(cli(data_file, "wait-signed-in", chat=chat))
    assert "check says the user is signed in" in r and time.time() - t0 < 10, r
    assert b.handoff.get(tid).mode == "ai"

    # signed out: the tab is the user's; the moment the site says they signed in, it is the AI's again, by itself
    raw(b, tid, "Network.clearBrowserCookies", {})
    live.inst.fill.sign_in_wait = 30.0

    def user_signs_in():
        # once the tab is theirs (the wait shows the page first: a windowless browser opens anew with a window)
        wait_for(lambda: b.tab_of.get(chat) and b.handoff.get(b.tab_of[chat]).mode == "signin", "the tab handed over")
        time.sleep(1.0)
        sign_in_on_the_site(b, b.tab_of[chat], auth_site)
    threading.Thread(target=user_signs_in, daemon=True).start()
    r = out(cli(data_file, "wait-signed-in", chat=chat))
    assert "check says the user is signed in" in r, r
    assert b.handoff.get(b.tab_of[chat]).mode == "ai"


def test_the_users_own_click_takes_over_and_the_ais_doesnt(live, data_file, fake_site):
    """While the AI works, the user clicking in its tab takes the tab: the AI's next command is refused with what to
    run, `wait` returns once they hand it back. The AI's own clicks never count as the user's."""
    b = live.inst.browser
    b.handoff.always = True                                  # the bar, in a browser with no window
    chat = live.inst.chats.store.create(title="Update LinkedIn")["id"]
    b.active = lambda k: k == chat                           # its reply is running
    page = fake_site + "/linkedin/profile.html"
    cli(data_file, "open", page, chat=chat)
    tid = tab_of(live, chat)
    wait_for(lambda: b.handoff.bars.get(tid) and b.handoff.bars[tid].shown, "the bar on the page")

    snap = out(cli(data_file, "snapshot", chat=chat))
    ref = next(l for l in snap.splitlines() if "button" in l).split("]")[0].strip(" [")
    for _ in range(3):
        assert "ok:" in out(cli(data_file, "click", ref, chat=chat))
    time.sleep(1.0)                                          # polls that saw those clicks
    assert b.handoff.get(tid).mode == "ai"

    user_clicks(b, tid)
    wait_for(lambda: b.handoff.get(tid).mode == "user", "the tab to be the user's")
    p = cli(data_file, "snapshot", chat=chat)
    assert p.returncode != 0 and "paused" in out(p) and "browser.py" in out(p) and " wait`" in out(p), out(p)
    live.inst.fill.sign_in_wait = 1.0
    assert "still the user's" in out(cli(data_file, "wait", chat=chat))
    live.inst.fill.sign_in_wait = 30.0
    threading.Timer(1.0, lambda: live.req("POST", "/api/browser/control", {"chat": chat, "action": "give"})).start()
    r = out(cli(data_file, "wait", chat=chat))
    assert "handed the tab back" in r and "new snapshot" in r, r
    assert "ok:" in out(cli(data_file, "click", ref, chat=chat))

    # the bar's own buttons do the same as the chat's
    b.handoff._event(tid, {"t": "button", "id": "take"})
    assert b.handoff.get(tid).mode == "user"
    b.handoff._event(tid, {"t": "button", "id": "give"})
    assert b.handoff.get(tid).mode == "ai"
    # a reply that ends leaves no tab held
    b.handoff.take(tid, "button")
    b.handoff.release(chat)
    assert b.handoff.get(tid).mode == "ai"


def test_the_bar_stays_out_of_what_the_ai_reads_and_clicks(live, data_file, fake_site):
    """The bar is the user's, not the page's: snapshots and text never show it, the site's scripts can't reach into
    it, and a control under it is still clicked."""
    b = live.inst.browser
    b.handoff.always = True
    chat = live.inst.chats.store.create(title="Read")["id"]
    b.active = lambda k: k == chat
    page = fake_site + "/linkedin/profile.html"
    cli(data_file, "open", page, chat=chat)
    tid = tab_of(live, chat)
    wait_for(lambda: b.handoff.bars.get(tid) and b.handoff.bars[tid].shown, "the bar on the page")
    st = b.handoff.state(tid)
    assert st["mode"] == "ai" and st["glow"] and [x["id"] for x in st["buttons"]] == ["take", "stop"]
    # the page sees one element with a closed root: nothing of the bar's text or buttons
    seen = raw(b, tid, "Runtime.evaluate", {"expression": "(() => { const e = document.documentElement.lastElementChild;"
                                                          " return [e.tagName, e.shadowRoot === null, document.body.innerText.includes('Take over') || document.body.innerText.includes('接管')]; })()",
                                            "returnByValue": True})["result"]["value"]
    assert seen == ["DIV", True, False]
    snap, text = out(cli(data_file, "snapshot", chat=chat)), out(cli(data_file, "text", chat=chat))
    for word in (t("Take over"), t("Stop"), t("The AI is deciding the next step.")):
        assert word not in snap and word not in text
    # a button right where the bar sits (bottom centre) is clicked, not the bar
    raw(b, tid, "Runtime.evaluate", {"expression": "(() => { const x = document.createElement('button'); x.textContent = 'Under the bar';"
                                                   " x.style.cssText = 'position:fixed;bottom:22px;left:50%;transform:translateX(-50%);z-index:5;width:120px;height:24px';"
                                                   " x.onclick = () => { document.title = 'clicked under'; }; document.body.appendChild(x); return 1; })()"})
    snap = out(cli(data_file, "snapshot", chat=chat))
    ref = next(l for l in snap.splitlines() if "Under the bar" in l).split("]")[0].strip(" [")
    r = out(cli(data_file, "click", ref, chat=chat))
    assert "clicked under" in r and "another element was on top" not in r, r
    # a screenshot is the page without the bar, and the bar comes back afterwards
    assert "saved:" in out(cli(data_file, "screenshot", chat=chat))
    assert b.handoff._call(tid, "1") == 1


def test_the_bars_buttons_and_dragging_work_by_hand(live, data_file, fake_site):
    """Real clicks on the bar (trusted mouse events at its buttons): Take over and Hand back to AI do what they say,
    a press on the bar itself is never taken for the user using the page, and the bar can be dragged aside."""
    b = live.inst.browser
    h = b.handoff
    h.always = True
    chat = live.inst.chats.store.create(title="Read")["id"]
    b.active = lambda k: k == chat
    cli(data_file, "open", fake_site + "/linkedin/profile.html", chat=chat)
    tid = tab_of(live, chat)
    wait_for(lambda: h.bars.get(tid) and h.bars[tid].shown, "the bar on the page")

    def button(id_):
        return wait_for(lambda: next((x for x in (h._call(tid, "__rsBar.metrics()") or {}).get("buttons") or []
                                      if x["id"] == id_), None), f"the {id_} button")

    take = button("take")
    user_clicks(b, tid, take["x"], take["y"])
    wait_for(lambda: h.get(tid).mode == "user", "Take over to take the tab")
    assert h.get(tid).reason == "button"                     # the button, not "they clicked in the page"
    give = button("give")
    user_clicks(b, tid, give["x"], give["y"])
    wait_for(lambda: h.get(tid).mode == "ai", "Hand back to AI to give it back")

    # drag the bar by its words, 200 px up and 50 px left: it stays there, on this page and the next
    m = h._call(tid, "__rsBar.metrics()")
    x, y = m["left"] + 20, m["top"] + 15
    raw(b, tid, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    for i in range(1, 6):
        raw(b, tid, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x - 10 * i, "y": y - 40 * i, "button": "left", "buttons": 1})
    raw(b, tid, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x - 50, "y": y - 200, "button": "left", "clickCount": 1})
    wait_for(lambda: h.get(tid).pos is not None, "the bar's new place")
    assert abs(h.get(tid).pos["x"] - (m["left"] - 50)) <= 2 and abs(h.get(tid).pos["y"] - (m["top"] - 200)) <= 2
    assert h.get(tid).mode == "ai"                            # dragging the bar is not using the page
    cli(data_file, "open", fake_site + "/linkedin/profile.html?again", chat=chat)
    wait_for(lambda: abs(((h._call(tid, "__rsBar.metrics()") or {}).get("left") or 0) - (m["left"] - 50)) <= 2,
             "the bar where it was put, on the next page")
    # dragged far past the window's edge, it stops at the edge (never off the page)
    m = h._call(tid, "__rsBar.metrics()")
    x, y = m["left"] + 20, m["top"] + 15
    raw(b, tid, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    raw(b, tid, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x - 3000, "y": y + 3000, "button": "left", "buttons": 1})
    raw(b, tid, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x - 3000, "y": y + 3000, "button": "left", "clickCount": 1})
    wait_for(lambda: (h.get(tid).pos or {}).get("x") == 4, "the bar at the left edge")
    m = h._call(tid, "__rsBar.metrics()")
    assert m["left"] >= 4 and m["bottom"] <= m["vh"] - 4 + 1, m


def test_the_bar_says_who_drives_and_stop_ends_the_reply(live, data_file, fake_site):
    b = live.inst.browser
    h = b.handoff
    h.always = True
    chat = live.inst.chats.store.create(title="Read")["id"]
    b.active = lambda k: k == chat
    cli(data_file, "open", fake_site + "/linkedin/profile.html", chat=chat)
    tid = tab_of(live, chat)
    h.doing(tid, "Clicking “Save”")
    assert h.state(tid)["text"] == "Clicking “Save”" and h.state(tid)["acting"]
    h.doing(tid, None)
    assert h.state(tid)["text"] == "Clicking “Save”"                    # a moment longer, not a flicker
    h.activity[tid] = ("Clicking “Save”", time.time() - 5)
    assert h.state(tid)["text"] == t("The AI is deciding the next step.") and not h.state(tid)["acting"]
    h.ask_sign_in(tid, "零跑汽车")
    st = h.state(tid)
    assert st["mode"] == "signin" and "零跑汽车" in st["text"] and not st["glow"] and st["buttons"][0]["id"] == "done"
    h.detected(tid, "likely")
    assert h.state(tid)["sub"] == t("Sign-in activity detected on this page.")
    # the user dragged the bar aside: it stays there whoever drives, and a double click puts it back
    h._event(tid, {"t": "pos", "x": 40, "y": 300})
    h.give(tid, "button")
    assert h.state(tid)["pos"] == {"x": 40, "y": 300}
    h._event(tid, {"t": "pos", "x": None, "y": None})
    assert h.state(tid)["pos"] is None

    # Stop only where it ends something: a chat's reply, not a recipe replaying a change
    real = h.stoppable
    h.stoppable = lambda k: False
    assert [x["id"] for x in h.state(tid)["buttons"]] == ["take"]
    h.stoppable = real
    assert [x["id"] for x in h.state(tid)["buttons"]] == ["take", "stop"]

    stopped = []
    h.on_stop = stopped.append
    h.take(tid, "button")
    h._event(tid, {"t": "button", "id": "stop"})
    assert stopped == [chat] and h.get(tid).mode == "ai"
    # the chat says the tab is the user's while it is, with the way back
    said = []
    live.inst.chats.wait_status = lambda cid, text, kind="signin", data=None: said.append((cid, text, kind))
    live.inst.chats.turns[chat] = type("T", (), {"status_kind": None})()
    try:
        h.take(tid, "input")
        assert said[-1][0] == chat and said[-1][2] == "control" and said[-1][1] == t("You have taken over the browser. The AI is paused until you hand it back.")
        live.inst.chats.turns[chat].status_kind = "control"
        h.give(tid, "button")
        assert said[-1] == (chat, None, "signin")
    finally:
        live.inst.chats.turns.pop(chat, None)
