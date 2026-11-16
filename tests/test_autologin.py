"""Signing in again with the password the browser saved (autofill/autologin.py), and session cookies kept across a
browser restart (core/browser.py). Against a fake sign-in page in a headless Chrome whose password store is seeded
through Chrome's own password page.

国聘 (2026-11-16): its sign-in lasts 8 hours, and each time the window handed the page to the user to sign in,
although the browser had the password saved and signing in was three clicks.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from autofill import autologin
from core import dpapi
from core.browser import ManagedBrowser, find_browser
from test_autofill import cli, fake_site, headless, live, out  # noqa: F401  (fixtures)

GOOD_PASSWORD = "pw-correct-42"

# a sign-in page like 国聘's: a password tab that draws the form, a terms box the form requires, a sign-in button
# that posts the account and password with fetch, then goes to the profile
LOGIN = """<!doctype html><meta charset=utf-8><title>登录</title>
<div id=tabs><button id=sms type=button>短信登录</button> <button id=pwtab type=button>密码登录</button></div>
<div id=form></div><div id=msg></div>
<script>
document.getElementById('pwtab').onclick = () => {
  document.getElementById('form').innerHTML = `<form id=f>
    <input id=u name=u autocomplete=username placeholder="请输入手机号">
    <input id=p name=p type=password autocomplete=current-password placeholder="请输入密码">
    <label><input type=checkbox id=agree> 已阅读并同意用户协议</label>
    <button id=go type=submit>登 录</button></form>`;
  document.getElementById('f').onsubmit = async (e) => {
    e.preventDefault();
    if (!document.getElementById('agree').checked) { document.getElementById('msg').textContent = '请先同意协议'; return; }
    const r = await fetch('/api/login', {method: 'POST', credentials: 'include',
      body: JSON.stringify({u: document.getElementById('u').value, p: document.getElementById('p').value})});
    const j = await r.json();
    if (j.ok) location.href = '/profile';
    else if (j.captcha) document.getElementById('msg').innerHTML = '<div class="geetest_panel">拖动滑块完成验证</div>';
    else document.getElementById('msg').textContent = '账号或密码错误';
  };
};
</script>"""


class Site:
    """The fake site: what it was sent, and how it answers a sign-in (`mode`: ok | captcha)."""

    def __init__(self):
        self.posts: list[dict] = []
        self.mode = "ok"
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html; charset=utf-8", cookie=""):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(data)

            def signed(self):
                return "sess=ok" in (self.headers.get("Cookie") or "")

            def do_GET(self):
                if self.path.startswith("/login"):
                    return self._send(200, LOGIN)
                if self.path.startswith("/api/me"):
                    return self._send(200 if self.signed() else 401, json.dumps({"user": "Alex" if self.signed() else None}),
                                      "application/json")
                if self.path.startswith("/profile"):
                    return self._send(200, "<title>档案</title><h1>Alex</h1>" if self.signed() else "<title>档案</title>")
                if self.path.startswith("/set-session"):   # a session cookie: no expiry, ends with the browser
                    return self._send(200, "ok", cookie="jsess=abc123; Path=/; HttpOnly")
                return self._send(404, "")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                got = json.loads(self.rfile.read(n) or b"{}")
                if self.path.startswith("/api/login"):
                    site.posts.append(got)
                    if site.mode == "captcha":
                        return self._send(200, json.dumps({"ok": False, "captcha": True}), "application/json")
                    if got.get("p") == GOOD_PASSWORD:
                        return self._send(200, json.dumps({"ok": True}), "application/json", "sess=ok; Path=/; Max-Age=28800")
                    return self._send(200, json.dumps({"ok": False}), "application/json")
                if self.path.startswith("/api/logout"):
                    return self._send(200, "{}", "application/json", "sess=; Path=/; Max-Age=0")
                return self._send(404, "")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


RECIPE = {"steps": [{"do": "click", "name": "密码登录"}, {"do": "fill", "field": "请输入手机号"},
                    {"do": "tick", "field": "已阅读并同意用户协议"}, {"do": "submit", "name": "登 录"}]}


def recipe_for(site: Site) -> dict:
    return {"steps": [{"do": "open", "url": site.url + "/login"}, *RECIPE["steps"]]}


def seed_password(b: ManagedBrowser, origin: str, user: str, password: str) -> None:
    """Save a password the way the user's "Save" does, through Chrome's own password page."""
    conn = b.conn
    tid = conn.send("Target.createTarget", {"url": "chrome://password-manager/passwords"})["targetId"]
    sid = conn.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    try:
        for _ in range(40):
            r = conn.send("Runtime.evaluate", {"expression": "!!(window.chrome && chrome.passwordsPrivate)", "returnByValue": True},
                          session=sid)
            if (r.get("result") or {}).get("value"):
                break
            time.sleep(0.25)
        expr = (f"new Promise((ok) => chrome.passwordsPrivate.addPassword({{url: {json.dumps(origin + '/login')}, "
                f"username: {json.dumps(user)}, password: {json.dumps(password)}, note: '', useAccountStore: false}}, "
                "() => ok(chrome.runtime.lastError ? chrome.runtime.lastError.message : 'added')))")
        r = conn.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, session=sid)
        assert (r.get("result") or {}).get("value") == "added", r
    finally:
        conn.send("Target.closeTarget", {"targetId": tid})


needs_chrome = pytest.mark.skipif(not find_browser("chrome") or find_browser("chrome")[0] != "chrome",
                                  reason="Google Chrome is not installed")


@pytest.fixture
def site():
    s = Site()
    yield s
    s.srv.shutdown()


@pytest.fixture
def chrome(tmp_path):
    b = ManagedBrowser(tmp_path / "browser", tmp_path / "browser.log", prefer="chrome", headless=True, pace=0)
    b.ensure()
    yield b
    b.stop()


def signed_in(b: ManagedBrowser, site: Site):
    def confirm():
        r = b.run_script(f"async () => (await fetch({json.dumps(site.url + '/api/me')}, {{credentials: 'include'}})).status === 200")
        return bool(r)
    return confirm


# ---- the recipe as recorded --------------------------------------------------------------------
def test_a_recipe_is_clicks_only():
    rec, errs = autologin.validate(recipe_for_url("https://www.iguopin.com/login/pwd"), "domain:iguopin.com")
    assert not errs and [s["do"] for s in rec["steps"]] == ["open", "click", "fill", "tick", "submit"]
    # an account, a password or a code is never part of it
    bad = {"steps": [{"do": "fill", "field": "手机号", "value": "13800000000"}, {"do": "submit", "name": "登录"}]}
    assert any("never holds" in e for e in autologin.validate(bad, "domain:x.com")[1])
    # nothing that sends a code, registers, resets a password or signs in another way
    for name in ("获取验证码", "发送验证码", "立即注册", "忘记密码", "微信登录", "APP扫码", "短信登录"):
        r = {"steps": [{"do": "click", "name": name}, {"do": "fill", "field": "手机号"}, {"do": "submit", "name": "登录"}]}
        assert any("never pressed" in e for e in autologin.validate(r, "domain:x.com")[1]), name
    # one fill, and the sign-in button last
    assert autologin.validate({"steps": [{"do": "submit", "name": "登录"}]}, "domain:x.com")[1]
    assert autologin.validate({"steps": [{"do": "fill", "field": "a"}, {"do": "submit", "name": "登录"},
                                         {"do": "click", "name": "b"}]}, "domain:x.com")[1]
    # a recruiting system's recipe starts where the sign-in was asked: each company has an address of its own
    assert any("no open step" in e for e in autologin.validate(recipe_for_url("https://a.zhiye.com/login"), "system:北森")[1])
    # and a domain's opens only its own pages
    assert autologin.validate(recipe_for_url("https://evil.example/login"), "domain:iguopin.com")[1]


def recipe_for_url(url: str) -> dict:
    return {"steps": [{"do": "open", "url": url}, *RECIPE["steps"]]}


def test_a_failed_recipe_waits_and_a_refused_password_is_never_tried_again():
    now = time.time()
    assert autologin.may_try({"steps": [], "last": {"ok": True, "at": now}})
    assert not autologin.may_try({"steps": [], "last": {"ok": False, "kind": "no_password", "at": now - 60}}, now)
    assert autologin.may_try({"steps": [], "last": {"ok": False, "kind": "no_password", "at": now - 900}}, now)
    # a wrong password tried again and again can lock the account: never by itself, until the user signs in
    assert not autologin.may_try({"steps": [], "last": {"ok": False, "kind": "rejected", "at": now - 99999}}, now)
    assert autologin.may_try({"steps": [], "last": {"ok": False, "kind": "rejected", "at": now, "cleared": True}}, now)
    assert not autologin.may_try(None)


def test_recipes_are_kept_and_a_manual_sign_in_clears_a_failure(tmp_path):
    rs = autologin.Recipes(tmp_path)
    rs.save("domain:x.com", {"steps": []}, "https://x.com/login", autologin.Outcome(False, "rejected", "no"))
    assert not autologin.may_try(rs.get("domain:x.com"))
    rs.clear_failure("domain:x.com")
    assert autologin.may_try(rs.get("domain:x.com"))


def test_dpapi_round_trip():
    secret = "jsess=abc123; 中文".encode("utf-8")
    sealed = dpapi.protect(secret)
    assert secret not in sealed and dpapi.unprotect(sealed) == secret


# ---- the replay, in a real Chrome ------------------------------------------------------------------
@needs_chrome
def test_the_saved_password_signs_in_with_no_one(chrome, site):
    seed_password(chrome, site.url, "13800000000", GOOD_PASSWORD)
    with chrome.acting("t"):
        o = autologin.replay(chrome, recipe_for(site), signed_in(chrome, site), "测试站")
        assert o.ok and o.kind == "signed_in", o
        assert site.posts == [{"u": "13800000000", "p": GOOD_PASSWORD}]   # the browser filled both; the recipe neither
        assert signed_in(chrome, site)()


@needs_chrome
def test_no_saved_password_is_said_and_nothing_is_sent(chrome, site):
    with chrome.acting("t"):
        o = autologin.replay(chrome, recipe_for(site), signed_in(chrome, site), "测试站")
    assert not o.ok and o.kind == "no_password", o
    assert site.posts == []


@needs_chrome
def test_a_captcha_is_the_users(chrome, site):
    seed_password(chrome, site.url, "13800000000", GOOD_PASSWORD)
    site.mode = "captcha"
    with chrome.acting("t"):
        o = autologin.replay(chrome, recipe_for(site), signed_in(chrome, site), "测试站")
    assert not o.ok and o.kind == "verify", o


@needs_chrome
def test_a_refused_password_is_said(chrome, site):
    seed_password(chrome, site.url, "13800000000", "pw-old-1")
    with chrome.acting("t"):
        o = autologin.replay(chrome, recipe_for(site), signed_in(chrome, site), "测试站")
    assert not o.ok and o.kind == "rejected" and "账号或密码错误" in o.text, o
    assert len(site.posts) == 1   # tried once, never again by itself


@needs_chrome
def test_a_page_not_as_recorded_says_what_it_shows(chrome, site):
    seed_password(chrome, site.url, "13800000000", GOOD_PASSWORD)
    rec = {"steps": [{"do": "open", "url": site.url + "/login"}, {"do": "click", "name": "账号登录"},
                     {"do": "fill", "field": "请输入手机号"}, {"do": "submit", "name": "登 录"}]}
    with chrome.acting("t"):
        o = autologin.replay(chrome, rec, signed_in(chrome, site), "测试站")
    assert not o.ok and o.kind == "broken" and o.step == 1 and "密码登录" in o.detail, o
    assert site.posts == []


@needs_chrome
def test_session_cookies_outlive_a_restart(tmp_path, site):
    b = ManagedBrowser(tmp_path / "browser", tmp_path / "browser.log", prefer="chrome", headless=True, pace=0)
    try:
        b.ensure()
        with b.acting("t"):
            b.open(site.url + "/set-session")
            assert any(c["name"] == "jsess" for c in b.cookies())
        b.stop()
        jar = b.profile_dir("chrome") / "Resume Studio session cookies"
        assert jar.is_file() and b"abc123" not in jar.read_bytes()   # kept, never in the clear
        b.ensure()
        with b.acting("t"):
            b.open(site.url + "/profile")
            got = {c["name"]: c for c in b.cookies()}
        assert got["jsess"]["value"] == "abc123" and got["jsess"]["session"]
    finally:
        b.stop()


# ---- through the app, as the AI meets it ------------------------------------------------------------
@needs_chrome
def test_the_ai_records_it_once_and_the_app_signs_in_by_itself_after(live, data_file, site):
    """The AI lands on the sign-in page: the note says to record a recipe. Recorded, it signs in at once. The site
    signs the user out later: the next page that asks to sign in is signed in by the app, with no AI and no user."""
    b = live.inst.browser
    b.stop()
    b.prefer = "chrome"
    b.ensure()
    seed_password(b, site.url, "13800000000", GOOD_PASSWORD)
    chat = live.inst.chats.store.create(title="Read 测试站")["id"]
    note = out(cli(data_file, "open", site.url + "/login", chat=chat))
    assert "No sign-in recipe is recorded" in note and "sign-in-recipe" in note, note
    r = out(cli(data_file, "sign-in-recipe", "--stdin", stdin=json.dumps(recipe_for(site)), chat=chat))
    assert "saved" in r and "signed in just now" in r, r
    assert len(site.posts) == 1

    # signed out by the site (国聘 after 8 hours): the next sign-in page is met by the recipe
    with b.acting(chat):
        b.run_script(f"async () => {{ await fetch({json.dumps(site.url + '/api/logout')}, {{method: 'POST', credentials: 'include'}}); return 1; }}")
    note = out(cli(data_file, "open", site.url + "/login", chat=chat))
    assert "signed in by the site's recorded sign-in recipe" in note, note
    assert len(site.posts) == 2
    kept = json.loads((Path(live.inst.fill.root) / "sign-in-recipes.json").read_text(encoding="utf-8"))
    assert all("13800000000" not in json.dumps(v) and GOOD_PASSWORD not in json.dumps(v) for v in kept.values())


CHECK = ("async (h) => { const r = await fetch('/api/me', {credentials: 'include'}); "
         "return {signed_in: r.status === 200}; }")
READ = ("async (h) => { const n = document.querySelector('h1'); if (!n) throw new Error('简历尚未加载完整或登录已失效'); "
        "return {items: [{section: 'experience', fields: {title: n.textContent, org: '测试公司'}}]}; }")


@needs_chrome
def test_a_read_that_meets_an_expired_sign_in_signs_in_and_reads_on(live, data_file, site):
    """国聘: the window checks the profile by its route, the sign-in has expired. The recipe signs in by itself and
    the route reads again: no sign-in banner, nothing for the user to press."""
    b, fill = live.inst.browser, live.inst.fill
    b.stop()
    b.prefer = "chrome"
    b.ensure()
    seed_password(b, site.url, "13800000000", GOOD_PASSWORD)
    assert not fill.set_route(fill.site("linkedin"), {"pages": [{"url": site.url + "/profile", "reads": ["experience"],
                                                                 "script": READ}]})[1]
    fill.root.mkdir(parents=True, exist_ok=True)
    (fill.root / "sign-in-checks.json").write_text(json.dumps({"domain:127.0.0.1": {"script": CHECK, "url": site.url, "at": 1}}),
                                                   encoding="utf-8")
    autologin.Recipes(fill.root).save("domain:127.0.0.1", recipe_for(site), site.url + "/login",
                                      autologin.Outcome(True, "signed_in", "recorded"))
    st, r = live.req("POST", "/api/autofill/linkedin/check", {"wait": True})
    assert st == 200 and r.get("ok"), r
    assert len(site.posts) == 1
    v = live.req("GET", "/api/autofill/linkedin")[1]
    assert not v["signed_out"], v

    # without a recipe the same read stops for the user, as before
    autologin.Recipes(fill.root).forget("domain:127.0.0.1")
    with b.acting("the-user"):
        b.open(site.url + "/profile")
        b.run_script(f"async () => {{ await fetch({json.dumps(site.url + '/api/logout')}, {{method: 'POST', credentials: 'include'}}); return 1; }}")
    st, r = live.req("POST", "/api/autofill/linkedin/check", {"wait": True})
    assert not r.get("ok") and r.get("signed_out"), r
    assert len(site.posts) == 1
