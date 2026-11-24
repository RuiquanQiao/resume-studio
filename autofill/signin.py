"""Is the user signed in to this site? Asked of the site itself, not guessed from the page.

A recruiting site's own pages ask its server who is signed in (北森's careers sites, Moka, 飞书招聘, LinkedIn all
have such a call). A **sign-in check** is that call, recorded once by the AI after a sign-in (`browser.py
sign-in-check`): a short read-only script, run in the page with the browser's own cookies, that returns
`{"signed_in": true|false}`. It belongs to the recruiting system when the site's company is on one (one check for
every company on 北森), else to the site's domain. With one recorded, Resume Studio knows by itself, in a second
and without AI, whether a sign-in happened.

Without one, it can only see signs: the page sent a sign-in request that went through, or the site set a new
session cookie. Those are signs, not an answer: the bar says "sign-in activity detected", and the user's
"I've signed in" decides.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.browser import BrowserError, ManagedBrowser
from core.domains import base_domain  # noqa: F401  (autologin.py imports it from here)
from core.i18n import t

from .sites import Site

# a request that signs in: its address names a sign-in, a code or a token
SIGN_IN_CALL = re.compile(r"(log-?in|sign-?in|signon|passport|sso|auth|token|verify|captcha|sms|code|session|oauth)", re.I)
# a cookie that holds a session (not a visitor id or a setting)
SESSION_COOKIE = re.compile(r"(sess|token|auth|ticket|jwt|login|passport|sid\b|_sid|uid|user|access|refresh|credential)", re.I)
# cookies every visitor gets: never a sign of a sign-in
VISITOR_COOKIE = re.compile(r"^(_ga|_gid|_gat|hm_|hmaccount|_clck|_clsk|__utm|acw_|cna|isg|tfstk|l$|__cf|cf_|_fbp|lang|locale)", re.I)



def key_for(url: str, site: Site | None) -> str:
    """Whose check a page uses: its recruiting system's when its company is on one, else its domain's."""
    if site is not None and site.system:
        return "system:" + site.system.strip().lower()
    return "domain:" + base_domain(urlparse(url or "").hostname or "")


class Checks:
    def __init__(self, root: Path):
        self.path = Path(root) / "sign-in-checks.json"
        self.lock = threading.Lock()

    def all(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def find(self, url: str, site: Site | None) -> dict | None:
        return self.all().get(key_for(url, site))

    def save(self, key: str, script: str, url: str) -> None:
        with self.lock:
            data = self.all()
            data[key] = {"script": script, "url": url, "at": time.time()}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def forget(self, key: str) -> None:
        with self.lock:
            data = self.all()
            if data.pop(key, None) is not None:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self.path)


def problems(script: str) -> list[str]:
    errs = []
    if not re.match(r"\s*async\b", script or ""):
        errs.append("the script must be an async function: async (h) => ({signed_in: ...})")
    if not re.search(r"\bfetch\s*\(|XMLHttpRequest", script or ""):
        errs.append("the script must ask the site's server (fetch the address the page itself calls for the signed-in "
                    "user), not read the page: that is what makes it an answer rather than a guess")
    if re.search(r"\.click\s*\(|\.submit\s*\(|method\s*:\s*['\"](POST|PUT|DELETE|PATCH)", script or "", re.I):
        errs.append("the script may only read (a GET): no clicks, no submits, no POST")
    return errs


def run(browser: ManagedBrowser, script: str, timeout: float = 10.0) -> tuple[bool | None, str]:
    """(signed in?, what it said) by the check, on the page open in this work's tab. None: no answer (the page is
    elsewhere, the call failed, the script broke): the check can't tell now, which is never "signed in"."""
    try:
        with browser.handoff.exempt():
            out: Any = browser.run_script(script, timeout=timeout)
    except BrowserError as e:
        return None, str(e)
    if isinstance(out, dict) and isinstance(out.get("signed_in"), bool):
        return out["signed_in"], json.dumps(out, ensure_ascii=False)[:300]
    return None, "the check didn't return {signed_in: true|false}: " + json.dumps(out, ensure_ascii=False)[:200]


# what a signed-out page shows, read only once a read went wrong (a page with a sign-in dialog or a "登录" button in
# its header, at the same address: 国聘, 2026-11-16): a visible password field, or a visible control that is only
# "sign in". Signs, not an answer: a recorded sign-in check always goes first.
PAGE_SIGNS = r"""async () => {
  const shown = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  if ([...document.querySelectorAll('input[type=password]')].some(shown)) return 'password';
  const only = /^(登录|登錄|立即登录|登录\s*[\/|｜]\s*注册|注册\s*[\/|｜]\s*登录|sign\s*in|log\s*in|login|sign\s*in\s*\/\s*sign\s*up)$/i;
  const c = [...document.querySelectorAll('a, button, [role=button], input[type=submit], input[type=button]')]
    .find((e) => shown(e) && only.test((e.innerText || e.value || '').replace(/\s+/g, ' ').trim()));
  return c ? 'button:' + (c.innerText || c.value).replace(/\s+/g, ' ').trim() : '';
}"""


def page_signs(browser: Any) -> str:
    """What on the open page says it is signed out ("" for nothing)."""
    try:
        h = getattr(browser, "handoff", None)
        if h is not None:
            with h.exempt():
                out = browser.run_script(PAGE_SIGNS, timeout=8)
        else:
            out = browser.run_script(PAGE_SIGNS, timeout=8)
    except BrowserError:
        return ""
    if not isinstance(out, str) or not out:
        return ""
    return t("a password field") if out == "password" else t("a “{name}” button", name=out.split(":", 1)[1])


class Judge:
    """Is the user signed in on the page open in this work's tab? One answer for every reader of a site (the read
    route, a recipe's replay, the chat's sign-in wait):

    1. the address: the site's sign-in paths, or one that names a sign-in (/login, /passport…): signed out;
    2. the recorded sign-in check, which asks the site's server: its answer, either way;
    3. `deep` (only once something went wrong, it costs a script): the page's signs, a password field or a lone
       "登录" button: signed out.

    Returns (True | False | None, how it knows); None: nothing can tell, which is never "signed in".
    国聘 (2026-11-16): a signed-out visit stays at c.iguopin.com/resume and shows an empty page, so the address alone
    said "signed in" and the window called the route broken."""

    def __init__(self, browser: Any, checks: Checks | None):
        self.b, self.checks = browser, checks

    def __call__(self, site: Site | None, url: str, deep: bool = False) -> tuple[bool | None, str]:
        from .sites import login_url
        if url.startswith("http") and ((site is not None and site.signed_out(url)) or login_url(url)):
            return False, t("the address is a sign-in page")
        rec = self.checks.find(url, site) if self.checks is not None and url.startswith("http") else None
        if rec:
            said, what = run(self.b, str(rec.get("script") or ""))
            if said is not None:
                return said, t("the site's sign-in check says so")
        if deep:
            sign = page_signs(self.b)
            if sign:
                return False, t("the page shows {what}", what=sign)
        return None, t("no sign-in check is recorded") if not rec else t("the sign-in check had no answer")


class Signs:
    """Signs of a sign-in on a tab since the user started: a sign-in request that went through, a new session
    cookie. Not an answer: the bar mentions them; the user decides."""

    def __init__(self, browser: ManagedBrowser):
        self.b = browser
        self.since = time.time()
        self.cookies = self._cookies()

    def _cookies(self) -> dict[str, str]:
        try:
            with self.b.handoff.exempt():
                return {f"{c.get('domain')}|{c.get('name')}": str(c.get("value")) for c in self.b.cookies()}
        except BrowserError:
            return {}

    def seen(self) -> bool:
        try:
            with self.b.handoff.exempt():
                calls = self.b.requests(self.since)
        except BrowserError:
            calls = []
        call = any(c["method"] != "GET" and isinstance(c.get("status"), int) and 200 <= c["status"] < 300
                   and SIGN_IN_CALL.search(urlparse(c["url"]).path) for c in calls)
        now = self._cookies()
        new = [k for k, v in now.items() if self.cookies.get(k) != v]
        cookie = any(SESSION_COOKIE.search(k.split("|", 1)[1]) and not VISITOR_COOKIE.search(k.split("|", 1)[1]) for k in new)
        return call and cookie or (cookie and len(new) >= 2)
