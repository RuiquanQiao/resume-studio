"""Signing in with the password the browser saved, by code: a site's sign-in recipe.

A sign-in is the user's: the AI never types an account, a password or a code. But once the user has signed in
to a site in Resume Studio's browser and let it save the password (Chrome's own "Save password?"), signing in
again is only a few clicks: open the sign-in page, choose its password form, click the account field (the
browser then fills the account and the password it saved), tick the terms box the form requires, and press
"登录". A **sign-in recipe** is those clicks, recorded once by the AI on the site's sign-in page
(`browser.py sign-in-recipe`) and replayed by Resume Studio whenever the site asks to sign in again (国聘's
sign-in lasts 8 hours): no AI, no user, a few seconds, in the windowless browser too.

    {"steps": [{"do": "open", "url": "https://www.iguopin.com/login/pwd"},
               {"do": "click", "name": "密码登录"},
               {"do": "fill", "field": "请输入手机号"},
               {"do": "tick", "field": "已阅读并同意"},
               {"do": "submit", "name": "登 录"}]}

The recipe holds no account and no password: the browser fills both, and the code reads only whether the
password field is filled, never what it holds. It belongs to the recruiting system when the site's company is
on one (every company on 北森), else to the site's domain, like the sign-in check (autofill/signin.py), which
says afterwards whether it worked.

What it never does: press anything that sends a code, registers, resets a password or signs in some other
way (WeChat, a QR code); solve a captcha or a slider; try again after the site refused the password. Each of
those is the user's, and the tab is handed to them as before. A site that signs in only by a code or a QR code
has no password to fill, so no recipe either.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from core.browser import BrowserError, UserHasControl

from .signin import base_domain

STEPS = ("open", "click", "fill", "tick", "submit", "wait")
CLICK_KINDS = ("button", "link", "clickable", "tab", "menuitem", "radio")
# what a sign-in recipe never presses: another way in, a code sent, an account made or reset
NEVER = re.compile(r"(发送|获取|重新获取|重发|send|get|resend).{0,6}(验证码|动态码|code|短信)|验证码登录|短信登录|扫码|二维码|"
                   r"微信|wechat|qq\s*登录|支付宝|注册|sign\s*up|register|create\s+account|忘记|找回|forgot|reset|"
                   r"退出|注销|log\s*out|sign\s*out", re.I)
FIND_WAIT = 8.0       # seconds a control may take to show
FILL_WAIT = 4.0       # seconds the browser may take to fill the saved account and password
SIGNED_WAIT = 20.0    # seconds the site may take to sign in once "登录" is pressed
COOL_DOWN = 600.0     # a recipe that failed waits this long before it is tried by itself again

# the sign-in form, read without ever reading the password: how many password fields show, whether one is
# filled, a captcha or slider on the page, and the site's own "wrong password"
PAGE_STATE = r"""async () => {
  const shown = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const pw = [...document.querySelectorAll('input[type=password]')].filter(shown);
  const sel = 'iframe[src*=captcha i], iframe[src*=geetest i], iframe[src*=tcaptcha i], [class*=captcha i], [id*=captcha i], ' +
              '[class*=geetest i], [class*=tcaptcha i], [class*=yidun i], [class*=nc_wrapper i], [class*=nc-container i], ' +
              '[class*=slide-verify i], [class*=verify-slide i], [class*=slider-verify i]';
  const cap = [...document.querySelectorAll(sel)].some(shown);
  const text = ((document.body && document.body.innerText) || '').slice(0, 30000);
  const v = text.match(/(拖动滑块|向右滑动|滑动验证|拼图|安全验证|请完成验证|请完成安全验证|点击按钮进行验证|人机验证|verify you are (a )?human|security check|complete the captcha)/i);
  const e = text.match(/(密码错误|账号或密码(错误|不正确|有误)|用户名或密码(错误|不正确|有误)|密码不正确|密码有误|账号不存在|incorrect password|wrong password|invalid (username|email|account) or password|password (is )?incorrect)/i);
  return {password: pw.length, filled: pw.some((x) => x.value.length > 0), verify: cap ? 'captcha' : (v ? v[0] : ''), error: e ? e[0] : ''};
}"""


@dataclass
class Outcome:
    """How a replay went. `kind`: "signed_in"; "no_password" (the browser has none saved for the site); "verify"
    (a captcha, a slider or a code: the user's); "rejected" (the site refused the saved password); "broken" (the
    page isn't as recorded); "unconfirmed" (pressed, and the site still shows its sign-in); "user" (the user took
    the tab); "browser" (the browser stopped)."""
    ok: bool
    kind: str
    text: str
    step: int = -1
    detail: str = ""     # what the page showed, for the AI fixing the recipe

    def public(self) -> dict:
        return {"ok": self.ok, "kind": self.kind, "text": self.text, "step": self.step + 1 if self.step >= 0 else None,
                "at": time.time()}


# ---- recording ---------------------------------------------------------------------------
def validate(data: Any, key: str) -> tuple[dict, list[str]]:
    """The AI's recipe, checked: (the recipe as kept, problems). `key` says whose it is (signin.key_for)."""
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        return {}, ['a sign-in recipe is {"steps": [{"do": "open", "url": ...}, {"do": "click", "name": ...}, '
                    '{"do": "fill", "field": ...}, {"do": "tick", "field": ...}, {"do": "submit", "name": ...}]}']
    errs: list[str] = []
    steps: list[dict] = []
    if len(data["steps"]) > 12:
        errs.append("a sign-in takes a few clicks: at most 12 steps")
    for n, st in enumerate(data["steps"][:12]):
        w = f"steps[{n}]"
        if not isinstance(st, dict) or st.get("do") not in STEPS:
            errs.append(f"{w}.do must be one of: {', '.join(STEPS)}")
            continue
        do = st["do"]
        out: dict[str, Any] = {"do": do}
        if do == "open":
            url = str(st.get("url") or "")
            host = urlparse(url).hostname or ""
            if n != 0:
                errs.append(f"{w}: only the first step opens a page")
            elif key.startswith("system:"):
                errs.append(f"{w}: this recipe is for every company on {key[7:]}, each at an address of its own: start "
                            "on the page that asks to sign in (no open step)")
            elif not url.startswith(("http://", "https://")) or base_domain(host) != key.removeprefix("domain:"):
                errs.append(f"{w}.url must be a page of {key.removeprefix('domain:')}, written out in full")
            out["url"] = url
        elif do in ("click", "submit"):
            name = st.get("name")
            if not isinstance(name, str) or not name.strip():
                errs.append(f"{w}.name must be the control's text, as the snapshot shows it")
            elif NEVER.search(name):
                errs.append(f"{w}: “{name}” is never pressed by a sign-in recipe (it sends a code, registers, resets a "
                            "password or signs in another way)")
            out["name"] = str(name or "").strip()
            kinds = st.get("kinds") or []
            if not isinstance(kinds, list) or not all(k in CLICK_KINDS for k in kinds):
                errs.append(f"{w}.kinds must list kinds from: {', '.join(CLICK_KINDS)}")
                kinds = []
            if kinds:
                out["kinds"] = list(kinds)
        elif do in ("fill", "tick"):
            field = st.get("field")
            if not isinstance(field, str) or not field.strip():
                errs.append(f"{w}.field must be the field's label or placeholder, as the snapshot shows it")
            out["field"] = str(field or "").strip()
        elif do == "wait":
            secs = st.get("seconds")
            if not isinstance(secs, (int, float)) or not 0 < secs <= 10:
                errs.append(f"{w}.seconds must be at most 10")
            out["seconds"] = secs if isinstance(secs, (int, float)) else 1
        for k in st:
            if k not in ("do", "url", "name", "kinds", "field", "seconds"):
                errs.append(f"{w}: “{k}” isn't part of a sign-in step (a recipe never holds an account, a password or a code)")
        steps.append(out)
    fills = [i for i, s in enumerate(steps) if s["do"] == "fill"]
    submits = [i for i, s in enumerate(steps) if s["do"] == "submit"]
    if len(fills) != 1:
        errs.append('one {"do": "fill", "field": "<the account field>"} step: clicking it makes the browser fill the '
                    "account and the password it saved")
    if len(submits) != 1 or submits[-1:] != [len(steps) - 1]:
        errs.append('the last step, and only it, presses the sign-in button: {"do": "submit", "name": "登录"}')
    if fills and submits and fills[0] > submits[0]:
        errs.append("the fill step comes before the submit")
    if errs:
        return {}, errs
    return {"steps": steps}, []


def describe(st: dict) -> str:
    do = st["do"]
    if do == "open":
        return f"open {st['url']}"
    if do == "fill":
        return f"click “{st['field']}” for the browser to fill the saved account and password"
    if do == "tick":
        return f"tick “{st['field']}”"
    if do == "wait":
        return f"wait {st['seconds']} s"
    return f"{'press' if do == 'submit' else 'click'} “{st['name']}”"


# ---- keeping them -------------------------------------------------------------------------
class Recipes:
    """`sign-in-recipes.json` next to the sign-in checks: {key: {steps, url, at, last: Outcome.public()}}."""

    def __init__(self, root: Path):
        self.path = Path(root) / "sign-in-recipes.json"
        self.lock = threading.Lock()

    def all(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key: str) -> dict | None:
        return self.all().get(key)

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def save(self, key: str, recipe: dict, url: str, last: Outcome) -> None:
        with self.lock:
            data = self.all()
            data[key] = {**recipe, "url": url, "at": time.time(), "last": last.public()}
            self._write(data)

    def note(self, key: str, last: Outcome) -> None:
        with self.lock:
            data = self.all()
            if key in data:
                data[key]["last"] = last.public()
                self._write(data)

    def clear_failure(self, key: str) -> None:
        """The user signed in by hand: a recipe that failed may be tried again (they may have saved the password)."""
        with self.lock:
            data = self.all()
            last = (data.get(key) or {}).get("last") or {}
            if last and not last.get("ok"):
                data[key]["last"] = {**last, "cleared": True}
                self._write(data)

    def forget(self, key: str) -> None:
        with self.lock:
            data = self.all()
            if data.pop(key, None) is not None:
                self._write(data)


def may_try(rec: dict | None, now: float | None = None) -> bool:
    """Whether Resume Studio tries the recipe by itself now: not after the site refused the saved password (trying
    a wrong password again can lock the account), nor within ten minutes of another failure, until the user has
    signed in by hand."""
    if not rec:
        return False
    last = rec.get("last") or {}
    if last.get("ok") or last.get("cleared") or not last:
        return True
    if last.get("kind") == "rejected":
        return False
    return (now or time.time()) - float(last.get("at") or 0) >= COOL_DOWN


# ---- replay -------------------------------------------------------------------------------
class _Failed(Exception):
    def __init__(self, outcome: Outcome):
        super().__init__(outcome.text)
        self.outcome = outcome


def replay(browser: Any, rec: dict, confirm: Callable[[], bool | None], site_name: str = "the site") -> Outcome:
    """Sign in by the recipe, in this work's tab. `confirm`: is the user signed in now (the site's sign-in check:
    True / False; None when nothing can tell, which counts once the sign-in form is gone)."""
    n = -1

    def fail(kind: str, text: str, detail: str = "") -> _Failed:
        return _Failed(Outcome(False, kind, text, n, detail))

    def state() -> dict:
        try:
            out = browser.run_script(PAGE_STATE, timeout=8)
        except BrowserError:
            return {}
        return out if isinstance(out, dict) else {}

    def within() -> str:
        try:
            return "dialog" if browser.dialogs() else "page"
        except BrowserError:
            return "page"

    def locate(spec: dict) -> dict:
        end = time.time() + FIND_WAIT
        while True:
            hit = browser.find({**spec, "within": within()})
            if hit.get("ref"):
                return hit
            if spec.get("within") is None and within() == "dialog":   # a form drawn on the page beneath a dialog
                hit = browser.find({**spec, "within": "page"})
                if hit.get("ref"):
                    return hit
            if time.time() >= end:
                seen = ", ".join(hit.get("seen") or [])
                raise fail("broken", f"no {' or '.join(spec.get('kinds') or ['control'])} “{spec.get('name')}” on the page",
                           ("the page shows: " + seen)[:900] if seen else "")
            time.sleep(0.4)

    def filled() -> dict:
        end = time.time() + FILL_WAIT
        while True:
            st = state()
            if st.get("filled") or time.time() >= end:
                return st
            time.sleep(0.3)

    try:
        for n, st in enumerate(rec.get("steps") or []):
            do = st["do"]
            if do == "open":
                browser.open(st["url"])
            elif do == "wait":
                time.sleep(float(st.get("seconds") or 1))
            elif do in ("click", "submit"):
                kinds = st.get("kinds") or (["button", "clickable", "link"] if do == "submit" else list(CLICK_KINDS))
                hit = locate({"kinds": kinds, "name": st["name"]})
                if NEVER.search(str(hit.get("name") or "")):
                    raise fail("broken", f"“{hit.get('name')}” is never pressed by a sign-in recipe")
                if do == "submit":
                    pre = state()
                    if pre.get("verify"):
                        raise fail("verify", f"{site_name} shows a verification ({pre['verify']}) before signing in")
                    if pre and not pre.get("filled"):
                        raise fail("no_password", f"the password field is empty: the browser has no password saved for {site_name}")
                browser.click(str(hit["ref"]))
            elif do == "tick":
                hit = locate({"kinds": ["checkbox", "switch", "radio"], "name": st["field"]})
                if not hit.get("checked"):
                    browser.click(str(hit["ref"]))
            elif do == "fill":
                hit = locate({"kinds": ["textbox", "combobox"], "name": st["field"]})
                if hit.get("secret"):
                    raise fail("broken", f"“{st['field']}” is the password field: the fill step names the account field")
                browser.click(str(hit["ref"]))   # a real click: the browser fills what it saved for this page
                got = filled()
                if not got.get("filled") and got.get("password"):
                    # more than one account saved: the browser offers them instead of choosing; take the first
                    browser.press("ArrowDown")
                    browser.press("Enter")
                    got = filled()
                if not got.get("password"):
                    raise fail("broken", "no password field shows after clicking the account field: a step that "
                                         "shows the password form (its tab, such as “密码登录”) comes first")
                if not got.get("filled"):
                    raise fail("no_password", f"the browser has no password saved for {site_name}")
        n = len(rec.get("steps") or [])
        return _signed_in(browser, confirm, state, site_name, n)
    except _Failed as e:
        return e.outcome
    except UserHasControl:
        return Outcome(False, "user", "the user took the tab", n)
    except BrowserError as e:
        return Outcome(False, "browser", f"the browser stopped: {e}", n)


def _signed_in(browser: Any, confirm: Callable[[], bool | None], state: Callable[[], dict], site_name: str,
               n: int) -> Outcome:
    """After "登录": the site's check says signed in, or the form is gone; a captcha, a code or the site's "wrong
    password" is said as it is."""
    start = time.time()
    end = start + SIGNED_WAIT
    while True:
        time.sleep(0.7)
        try:
            said = confirm()
        except BrowserError:
            said = None
        if said is True:
            return Outcome(True, "signed_in", f"signed in to {site_name}: its sign-in check says so", n)
        st = state()
        if st.get("error"):
            return Outcome(False, "rejected", f"{site_name} refused the saved password (“{st['error']}”)", n)
        if st.get("verify"):
            return Outcome(False, "verify", f"{site_name} asks for a verification ({st['verify']})", n)
        if said is None and st and not st.get("password") and time.time() - start >= 2:
            return Outcome(True, "signed_in", f"signed in to {site_name}: its sign-in form is gone", n)
        if time.time() >= end:
            return Outcome(False, "unconfirmed", f"pressed sign-in; after {int(SIGNED_WAIT)} s {site_name} still shows its "
                                                 "sign-in", n)


__all__ = ["Outcome", "Recipes", "describe", "may_try", "replay", "validate"]
