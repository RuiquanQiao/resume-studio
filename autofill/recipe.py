"""A site's write recipes: how to push one kind of change without Claude, recorded by Claude the first time.

The read route (autofill/route.py) reads the profile without AI; a recipe writes one kind of change the same
way. One recipe per section and operation, kept in the site's state next to the route:

    "experience.update" · "experience.add" · "experience.remove" (and the same for education, projects, awards)
    "basics.headline" · "basics.about"      "skills.add" · "skills.remove" (one skill at a time)

A recipe is a list of steps, parameterised by the change's values:

    {"cancel": "取消",
     "steps": [{"do": "open", "url": "https://www.zhipin.com/web/geek/resume"},
               {"do": "click", "kinds": ["button", "link"], "name": "编辑", "near": ["{site.title}", "{site.org}"]},
               {"do": "type", "field": "职位名称", "value": "{title}"},
               {"do": "search", "field": "公司名称", "value": "{org}"},
               {"do": "select", "field": "城市", "value": "{location}", "options": ["北京", "上海", ...]},
               {"do": "pick", "field": "学历", "value": "{degree}", "options": ["本科", "硕士", "博士"]},
               {"do": "date", "field": "开始时间", "value": "{start}", "parts": ["{start.year}", "{start.month}月"]},
               {"do": "date", "field": "结束时间", "value": "{end}", "parts": ["{end.year}", "{end.month}月"], "present": "至今"},
               {"do": "check", "field": "I currently work here", "on": "end=present"},
               {"do": "save", "name": "保存"}]}

Controls are found by their role and their label or text (core/snapshot.js `find`), never by class names, and
acted on with real input (core/browser.py click / type / select), so React, Vue and AntD inputs take the value.
`{title}` is the value the change puts there, `{site.title}` what the site has now (to find the item);
`{start.year}`, `.month` (1-12), `.mm` (01-12), `.mon` (Jan), `.month_name` (January) are parts of a date.

Before anything is touched, every value is checked against the options a static dropdown has (`options`); a
searchable or async picker (`search`) types the value and takes only the option that reads exactly that. Any
other choice is a failure, never a guess. After saving, the read route reads the section again and the change
counts as done only when the site says it. A failure stops that change and says at which step; a recipe the site
no longer fits is marked broken, and the window offers to let the AI do it and record the recipe anew.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from core.browser import BrowserError

from . import route, signin
from .profile import norm_date, norm_text, same
from .sites import FIELDS, Site, login_url

OPS = ("update", "add", "remove")
STEPS = ("open", "click", "type", "select", "pick", "search", "date", "check", "save", "confirm", "wait", "press")
FIELD_STEPS = ("type", "select", "pick", "search", "date", "check")
CLICK_KINDS = ("button", "link", "clickable", "menuitem", "option", "tab", "checkbox", "radio", "switch", "combobox", "textbox")
KEYS = ("Enter", "Escape", "Tab")
PARTS = ("year", "month", "mm", "mon", "month_name")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
TEMPLATE = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)*)\}")
DELETES = re.compile(r"delete|remove|删除|移除|删掉", re.I)
SECRET = re.compile(r"password|passcode|密码|验证码|verification|one-time|\botp\b|短信码", re.I)
FIND_WAIT = 8.0       # seconds a control may take to show (pages draw themselves, dialogs animate in)
SAVE_WAIT = 20.0      # seconds a dialog may take to close once saved


class RecipeFailed(Exception):
    """A change the recipe didn't make. `kind`: "broken" (the site isn't as recorded: the AI can record it anew),
    "value" (the site has no option for the value), "signed_out", "unsafe" (it would press something it never
    may), "verify" (saved, but the site doesn't show it). `saved`: whether the site may have taken anything."""

    def __init__(self, message: str, step: int = -1, kind: str = "broken", saved: bool = False, what: str = "",
                 detail: str = ""):
        super().__init__(message)
        self.step, self.kind, self.saved, self.what = step, kind, saved, what
        self.detail = detail   # for the AI fixing the recipe (the controls the page showed): never in the window


# ---- which recipe a change needs --------------------------------------------------------
def keys_of(change: dict) -> list[str]:
    sec = change["section"]
    if sec == "basics":
        return [f"basics.{change['item']}"]
    if sec == "skills":
        return [k for k, xs in (("skills.add", change.get("add")), ("skills.remove", change.get("remove"))) if xs]
    return [f"{sec}.{change['op']}"]


def known_keys(site: Site) -> list[str]:
    out = [f"basics.{b}" for b in site.basics]
    for s in site.sections:
        out += ["skills.add", "skills.remove"] if s == "skills" else [f"{s}.{op}" for op in OPS] if s in FIELDS else []
    return out


def reads_for(key: str) -> str:
    """The read route's part that shows what this recipe changed."""
    return key.split(".")[0]


def _fields(site: Site, key: str) -> list[str]:
    sec, _, rest = key.partition(".")
    if sec == "basics":
        return [rest]
    if sec == "skills":
        return ["skill"]
    return site.fields(sec)


# ---- recording: what Claude sends is checked like a read route ---------------------------
def _refs(text: str) -> list[str]:
    return TEMPLATE.findall(text or "")


def validate(data: Any, site: Site, key: str) -> tuple[dict, list[str]]:
    if key not in known_keys(site):
        return {}, [f"{key!r} isn't a recipe {site.name} can have; one of: {', '.join(known_keys(site))}"]
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        return {}, ['a recipe must be {"steps": [{"do": "open", "url": ...}, ...], "cancel": "<the dialog\'s cancel button>"}']
    errs: list[str] = []
    sec, op = key.split(".", 1)
    removing = op == "remove"
    names = _fields(site, key)
    can_site = sec in FIELDS and op in ("update", "remove")

    def template_ok(where: str, text: Any) -> None:
        if not isinstance(text, str):
            errs.append(f"{where} must be text")
            return
        for ref in _refs(text):
            parts = ref.split(".")
            if parts[0] == "site":
                if not can_site:
                    errs.append(f"{where}: {{{ref}}}: what the site has now is known only when changing or removing an item")
                    continue
                parts = parts[1:]
            if not parts or parts[0] not in names or len(parts) > 2:
                errs.append(f"{where}: {{{ref}}} isn't one of {', '.join('{' + n + '}' for n in names)}")
            elif len(parts) == 2 and (parts[1] not in PARTS or parts[0] not in ("start", "end", "date")):
                errs.append(f"{where}: {{{ref}}}: only dates have parts ({', '.join(PARTS)})")

    def cond_ok(where: str, c: Any) -> None:
        m = re.fullmatch(r"(!?)([a-z_]+)(?:(!?=)(.*))?", c) if isinstance(c, str) else None
        if not m or m.group(2) not in names:
            errs.append(f"{where} must be a condition on a field: \"end=present\", \"end!=present\", \"link\", \"!link\"")

    def name_ok(where: str, name: Any, deleting_ok: bool) -> None:
        if not isinstance(name, str) or not name.strip():
            errs.append(f"{where}: `name` must be the control's label or text, as the snapshot shows it")
            return
        rule = site.forbids(name)
        if rule:
            errs.append(f"{where}: “{name}” is never pressed on {site.name} (it applies, messages, or changes a status)")
        if DELETES.search(name) and not deleting_ok:
            errs.append(f"{where}: only a {sec}.remove recipe may press “{name}”")

    steps = []
    for n, st in enumerate(data["steps"]):
        w = f"steps[{n}]"
        if not isinstance(st, dict) or st.get("do") not in STEPS:
            errs.append(f"{w}.do must be one of: {', '.join(STEPS)}")
            continue
        do = st["do"]
        out: dict[str, Any] = {"do": do}
        if n == 0 and do != "open":
            errs.append("steps[0] must open the page the change is made on ({\"do\": \"open\", \"url\": ...})")
        if st.get("if") is not None:
            cond_ok(f"{w}.if", st["if"])
            out["if"] = st["if"]
        if st.get("in") is not None:
            if st["in"] not in ("page", "dialog"):
                errs.append(f"{w}.in must be page or dialog")
            out["in"] = st["in"]
        if do == "open":
            url = str(st.get("url") or "")
            if not url.startswith(("http://", "https://")) or not site.owns(url) or _refs(url):
                errs.append(f"{w}.url must be a page of {site.name} ({', '.join(site.domains)}), written out in full")
            out["url"] = url
        elif do in ("click", "save", "confirm"):
            name_ok(w, st.get("name"), removing)
            out["name"] = str(st.get("name") or "")
            if do == "confirm" and not removing:
                errs.append(f"{w}: only a {sec}.remove recipe confirms (a site's own “delete?” question)")
            if do == "click":
                kinds = st.get("kinds") or []
                if not isinstance(kinds, list) or not all(k in CLICK_KINDS for k in kinds):
                    errs.append(f"{w}.kinds must list kinds from: {', '.join(CLICK_KINDS)}")
                near = st.get("near") or []
                if not isinstance(near, list):
                    errs.append(f"{w}.near must list the item's texts, like [\"{{site.title}}\", \"{{site.org}}\"]")
                    near = []
                for i, t in enumerate(near):
                    template_ok(f"{w}.near[{i}]", t)
                out.update(kinds=list(kinds), near=[str(t) for t in near])
                if _refs(out["name"]):
                    errs.append(f"{w}.name must be the control's own text; find the item with `near`")
        elif do in FIELD_STEPS:
            if removing:
                errs.append(f"{w}: a remove recipe changes no fields")
            field = st.get("field")
            if not isinstance(field, str) or not field.strip():
                errs.append(f"{w}.field must be the field's label, as the snapshot shows it")
            elif SECRET.search(field):
                errs.append(f"{w}: a recipe never types passwords or codes")
            out["field"] = str(field or "")
            if do == "check":
                cond_ok(f"{w}.on", st.get("on"))
                out["on"] = st.get("on")
            else:
                template_ok(f"{w}.value", st.get("value"))
                out["value"] = str(st.get("value") or "")
                if not _refs(out["value"]):
                    errs.append(f"{w}.value must take the change's value, like \"{{{names[0]}}}\"")
            if do in ("select", "pick") and st.get("options") is not None:
                opts = st["options"]
                if not isinstance(opts, list) or not opts or not all(isinstance(o, str) and o.strip() for o in opts):
                    errs.append(f"{w}.options must list the dropdown's options as it shows them")
                else:
                    out["options"] = [o.strip() for o in opts]
            if do == "search":
                if st.get("input") is not None:
                    out["input"] = str(st["input"])
                secs = st.get("wait", 8)
                if not isinstance(secs, (int, float)) or not 0 < secs <= 30:
                    errs.append(f"{w}.wait must be the seconds suggestions may take (at most 30)")
                out["wait"] = secs if isinstance(secs, (int, float)) else 8
            if do == "date":
                if st.get("parts") is not None:
                    parts = st["parts"]
                    if not isinstance(parts, list) or not parts:
                        errs.append(f"{w}.parts must list what to pick in the date picker, like [\"{{start.year}}\", \"{{start.month}}月\"]")
                    else:
                        for i, t in enumerate(parts):
                            template_ok(f"{w}.parts[{i}]", t)
                        out["parts"] = [str(t) for t in parts]
                elif st.get("text") is not None:
                    template_ok(f"{w}.text", st["text"])
                    out["text"] = str(st["text"])
                else:
                    errs.append(f"{w}: a date step picks `parts` in the picker, or types `text` (like \"{{start.year}}-{{start.mm}}\")")
                if st.get("present") is not None:
                    out["present"] = str(st["present"])
        elif do == "wait":
            what = st.get("for")
            if not isinstance(what, str) or not what.strip():
                errs.append(f"{w}.for must be dialog, gone (no dialog open) or a text the page shows then")
            out["for"] = str(what or "")
            out["seconds"] = min(30, max(1, int(st.get("seconds") or 10)))
        elif do == "press":
            if st.get("key") not in KEYS:
                errs.append(f"{w}.key must be one of {', '.join(KEYS)}")
            out["key"] = st.get("key")
        steps.append(out)
    if not any(s["do"] in ("save", "confirm") for s in steps):
        errs.append("a recipe ends by saving: a {\"do\": \"save\", \"name\": ...} step (or confirm, for a remove)")
    if removing and not any(s["do"] in ("click", "confirm") and DELETES.search(s.get("name") or "") for s in steps):
        errs.append("a remove recipe presses the item's delete")
    if sec in FIELDS and op in ("update", "remove") and not any(s.get("near") for s in steps):
        errs.append("find the item first: a click with `near` the item's own texts (like [\"{site.title}\", \"{site.org}\"])")
    cancel = data.get("cancel")
    if cancel is not None and (not isinstance(cancel, str) or site.forbids(cancel)):
        errs.append("`cancel` must be the label of the button that closes the dialog without saving")
    if errs:
        return {}, errs
    return {"steps": steps, "cancel": str(cancel or ""), "at": time.time()}, []


# ---- values ---------------------------------------------------------------------------
def _part(v: str, part: str) -> str:
    d = norm_date(v)
    if not re.fullmatch(r"\d{4}(-\d{2})?", d):
        return ""
    y, m = d[:4], int(d[5:7]) if len(d) > 4 else 0
    if part == "year":
        return y
    if not m:
        return ""
    return {"month": str(m), "mm": f"{m:02d}", "mon": MONTHS[m - 1][:3], "month_name": MONTHS[m - 1]}[part]


def render(text: str, vals: dict, site_vals: dict) -> str:
    def one(m: re.Match) -> str:
        parts = m.group(1).split(".")
        src = vals
        if parts[0] == "site":
            src, parts = site_vals, parts[1:]
        v = str(src.get(parts[0]) or "")
        return _part(v, parts[1]) if len(parts) > 1 else v
    return TEMPLATE.sub(one, text or "")


def holds(cond: str, vals: dict) -> bool:
    m = re.fullmatch(r"(!?)([a-z_]+)(?:(!?=)(.*))?", cond or "")
    if not m:
        return False
    v = str(vals.get(m.group(2)) or "")
    if m.group(3):
        eq = same(m.group(2), v, m.group(4)) if v else not m.group(4)
        return eq if m.group(3) == "=" else not eq
    return (not v) if m.group(1) else bool(v)


def _bare(s: str) -> str:
    return re.sub(r"[\s*:：]+$", "", re.sub(r"\s+", " ", s or "").strip().lower())


def describe(st: dict) -> str:
    do = st["do"]
    if do == "open":
        return f"open {st['url']}"
    if do in ("click", "save", "confirm"):
        near = " next to " + " · ".join(f"“{x}”" for x in st.get("near") or []) if st.get("near") else ""
        return f"{do} “{st['name']}”{near}"
    if do == "check":
        return f"tick “{st['field']}” when {st['on']}"
    if do == "wait":
        return f"wait for {st['for']}"
    if do == "press":
        return f"press {st['key']}"
    return {"type": "type into", "select": "choose in", "pick": "pick in", "search": "search and pick in",
            "date": "set the date"}[do] + f" “{st['field']}”"


def plan(site: Site, recipe: dict, op: str, vals: dict, site_vals: dict, changed: list[str] | None) -> list[tuple[int, dict, str]]:
    """The steps this change runs, with their values: (index, step, value). Raises RecipeFailed (nothing touched)
    when a value isn't one of a static dropdown's options."""
    out = []
    for n, st in enumerate(recipe["steps"]):
        if st.get("if") and not holds(st["if"], vals):
            continue
        do = st["do"]
        v = render(st.get("value", ""), vals, site_vals)
        if do in FIELD_STEPS:
            mine = {r.split(".")[0] for r in _refs(st.get("value", "")) + _refs(st.get("text", "")) + _refs(" ".join(st.get("parts") or []))
                    if not r.startswith("site.")}
            if do == "check":
                mine = {re.sub(r"[!=].*|^!", "", str(st.get("on") or "").lstrip("!"))}
            if op == "update" and changed is not None and mine and not mine & set(changed):
                continue   # a field this change leaves as it is
            if do in ("select", "pick", "search", "date") and not v:
                continue   # nothing to choose (a field the item doesn't fill)
            if do == "date" and norm_date(v) == "present" and not st.get("present"):
                raise RecipeFailed(f"the date picker “{st['field']}” has no choice recorded for “present”", n, "value",
                                   what=describe(st))
            if st.get("options") and not any(_bare(o) == _bare(v) for o in st["options"]):
                raise RecipeFailed(f"{site.name} offers no “{v}” in “{st['field']}”; its options are: "
                                   + " | ".join(st["options"][:30]), n, "value", what=describe(st))
        out.append((n, st, v))
    return out


# ---- replay ---------------------------------------------------------------------------
class _Run:
    def __init__(self, browser: Any, site: Site, key: str, recipe: dict, stop_before_save: bool = False,
                 judge: Callable[..., tuple[bool | None, str]] | None = None):
        self.b, self.site, self.key, self.recipe = browser, site, key, recipe
        self.judge = judge or signin.Judge(browser, None)
        self.removing = key.endswith(".remove")
        self.saved = False
        self.n, self.what = -1, ""
        self.dry = stop_before_save

    def fail(self, msg: str, kind: str = "broken", detail: str = "") -> RecipeFailed:
        return RecipeFailed(msg, self.n, kind, self.saved, self.what, detail)

    def where_am_i(self) -> None:
        url = str(self.b.here().get("url") or "")
        if self.site.signed_out(url) or login_url(url):
            raise self.fail(f"{self.site.name} asks to sign in", "signed_out")
        if url.startswith("http") and not self.site.owns(url):
            raise self.fail(f"the page went off {self.site.name}, to {url}")

    def _url(self) -> str:
        try:
            return str(self.b.here().get("url") or "")
        except BrowserError:
            return ""

    def locate(self, spec: dict, wait: float = FIND_WAIT) -> dict:
        end = time.time() + wait
        while True:
            hit = self.b.find(spec)
            if hit.get("ref"):
                return hit
            if time.time() >= end:
                seen = "the page shows: " + ", ".join(hit.get("seen") or []) if hit.get("seen") else ""
                where = " in the open dialog" if spec.get("within") == "dialog" else ""
                raise self.fail(f"{hit.get('error') or 'not found'}: {' or '.join(spec.get('kinds') or ['control'])} "
                                f"“{spec.get('name')}”{where}", detail=seen[:900])
            time.sleep(0.4)

    def guard(self, hit: dict) -> None:
        name = str(hit.get("name") or "")
        if self.site.forbids(name):
            raise self.fail(f"“{name}” is never pressed on {self.site.name}", "unsafe")
        if DELETES.search(name) and not self.removing:
            raise self.fail(f"“{name}” deletes, and this change deletes nothing", "unsafe")

    def option(self, want: str, exclude: str | None, wait: float, searched: bool = False) -> str:
        """The ref of the open picker's option that reads exactly `want`; any other is a failure, never a guess.
        `searched`: the options are suggestions for typed text, so none at all means the site knows no such value."""
        end = time.time() + wait
        while True:
            o = self.b.options(want, exclude)
            if o.get("ref"):
                return str(o["ref"])
            if time.time() >= end:
                if not o.get("texts"):
                    if searched:
                        raise self.fail(f"{self.site.name} suggested nothing for “{want}”", "value")
                    raise self.fail("the picker showed no options")
                raise self.fail(f"no option reads “{want}”; it offered: " + " | ".join(o["texts"][:20]), "value")
            time.sleep(0.4)

    def field(self, st: dict, kinds: list[str] | None = None) -> dict:
        within = st.get("in") or ("dialog" if self.b.dialogs() else "page")
        hit = self.locate({"within": within, "kinds": kinds or [], "name": st["field"]})
        if hit.get("secret"):
            raise self.fail(f"“{st['field']}” takes a password or a code: never typed by a recipe", "unsafe")
        return hit

    def typed(self, ref: str, text: str) -> None:
        r = self.b.type(ref, text)
        if norm_text(str(r.get("value") or "")) != norm_text(text):
            raise self.fail(f"the field holds “{str(r.get('value'))[:80]}” after typing “{text[:80]}”")

    def cancel(self) -> None:
        """Close what is open without saving: the dialog's cancel, else Escape."""
        try:
            if self.recipe.get("cancel") and self.b.dialogs():
                hit = self.b.find({"within": "dialog", "name": self.recipe["cancel"]})
                if hit.get("ref") and not self.site.forbids(str(hit.get("name") or "")):
                    self.b.click(str(hit["ref"]))
            if self.b.dialogs():
                self.b.press("Escape")
        except BrowserError:
            pass

    def run(self, steps: list[tuple[int, dict, str]], vals: dict, site_vals: dict) -> None:
        try:
            for n, st, v in steps:
                self.n, self.what = n, describe(st)
                do = st["do"]
                if do in ("save", "confirm") and self.dry:
                    return
                if do == "open":
                    self.b.open(st["url"])
                elif do in ("click", "save", "confirm"):
                    within = st.get("in") or ("dialog" if do == "confirm" or (do == "save" and self.b.dialogs()) else "page")
                    near = [render(x, vals, site_vals) for x in st.get("near") or []]
                    kinds = st.get("kinds") or (["button", "link", "clickable"] if do != "click" else [])
                    hit = self.locate({"within": within, "kinds": kinds, "name": st["name"], "near": [x for x in near if x]})
                    self.guard(hit)
                    before = len(self.b.dialogs())
                    self.b.click(str(hit["ref"]))
                    if do in ("save", "confirm"):
                        self.closed(before)
                elif do == "type":
                    hit = self.field(st, ["textbox", "combobox"])
                    self.typed(str(hit["ref"]), v)
                elif do == "select":
                    hit = self.field(st, ["select"])
                    got = self.b.select(str(hit["ref"]), v)
                    if _bare(str(got.get("value") or "")) != _bare(v):
                        raise self.fail(f"“{st['field']}” took “{got.get('value')}”, not “{v}”", "value")
                elif do == "pick":
                    hit = self.field(st)
                    self.guard(hit)
                    self.b.click(str(hit["ref"]))
                    self.b.click(self.option(v, str(hit["ref"]), 5))
                elif do == "search":
                    hit = self.field(st)
                    box = hit
                    if hit.get("kind") not in ("textbox", "combobox"):
                        self.guard(hit)
                        self.b.click(str(hit["ref"]))
                        box = (self.locate({"within": "page", "kinds": ["textbox", "combobox"], "name": st["input"]})
                               if st.get("input") else self.b.focused() or {})
                        if not box.get("ref"):
                            raise self.fail(f"clicking “{st['field']}” opened no field to type the search in")
                    if box.get("secret"):
                        raise self.fail(f"“{st['field']}” takes a password or a code", "unsafe")
                    self.b.type(str(box["ref"]), v)
                    self.b.click(self.option(v, None, float(st.get("wait") or 8), searched=True))
                elif do == "date":
                    hit = self.field(st)
                    if st.get("text") is not None and norm_date(v) != "present":
                        self.typed(str(hit["ref"]), render(st["text"], vals, site_vals))
                    else:
                        self.guard(hit)
                        self.b.click(str(hit["ref"]))
                        parts = [st["present"]] if norm_date(v) == "present" else [render(p, vals, site_vals) for p in st["parts"]]
                        for p in parts:
                            if p:
                                self.b.click(self.option(p, str(hit["ref"]), 5))
                elif do == "check":
                    hit = self.field(st, ["checkbox", "switch", "radio"])
                    if bool(hit.get("checked")) != holds(str(st["on"]), vals):
                        self.b.click(str(hit["ref"]))
                elif do == "wait":
                    self.wait_for(st["for"], st.get("seconds", 10))
                elif do == "press":
                    self.b.press(st["key"])
                self.where_am_i()
        except RecipeFailed as e:
            if e.kind == "broken" and not e.saved:
                # a control that isn't there: often a page that signed out (a sign-in dialog over it, an empty
                # editor at the same address), and then nothing is wrong with the recipe
                said, how = self.judge(self.site, self._url(), deep=True)
                if said is False:
                    raise self.fail(f"{self.site.name} asks to sign in ({how})", "signed_out") from e
            if not e.saved and e.kind != "signed_out":
                self.cancel()
            raise
        except BrowserError as e:
            failed = self.fail(f"the browser stopped: {e}")
            if not self.saved:
                self.cancel()
            raise failed from e

    def closed(self, before: int) -> None:
        """After save / confirm: the dialog closes (the site took it), or stays with what it flags."""
        end = time.time() + SAVE_WAIT
        while True:
            ds = self.b.dialogs()
            if len(ds) < before or not before:
                self.saved = True
                return
            if time.time() >= end:
                flags = [a for d in ds for a in d.get("alerts") or []]
                raise self.fail(f"the dialog “{ds[-1].get('name')}” stayed open after “{self.what}”"
                                + (": " + "; ".join(flags[:4]) if flags else ""))
            time.sleep(0.4)

    def wait_for(self, what: str, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ds = self.b.dialogs()
            if (what == "dialog" and ds) or (what == "gone" and not ds):
                return
            if what not in ("dialog", "gone") and what in str(self.b.text().get("text") or ""):
                return
            time.sleep(0.4)
        raise self.fail(f"waited {seconds} s and the page never showed {what}")


# ---- one ticked change, end to end ------------------------------------------------------
def values(change: dict, base: dict | None, local: dict | None) -> tuple[dict, dict, list[str] | None]:
    """(what the change puts there, what the site has now, the fields it changes; None = all)."""
    sec = change["section"]
    if sec == "basics":
        name = change["item"]
        return {name: str((local or {}).get("basics", {}).get(name) or "")}, \
            {name: str((base or {}).get("basics", {}).get(name) or "")}, [name]
    if sec == "skills":
        return {}, {}, None
    li = next((i for i in (local or {}).get("items") or [] if i["id"] == change["item"]), None)
    bi = next((i for i in (base or {}).get("items") or [] if i["id"] == change["item"]), None)
    vals = dict((li or {}).get("fields") or {})
    site_vals = dict((bi or {}).get("fields") or {})
    changed = [f["name"] for f in change.get("fields") or []] if change["op"] == "update" else None
    return vals, site_vals, changed


def push(browser: Any, site: Site, st: dict, change: dict, dry: bool = False,
         step: Callable[[str], None] | None = None, judge: Callable[..., tuple[bool | None, str]] | None = None) -> str:
    """Make one change by its recipe(s), then read the section back and check the site says it. Returns what was
    checked; raises RecipeFailed. `dry`: fill everything in, stop before saving, and leave it open."""
    recipes = st.get("recipes") or {}
    keys = keys_of(change)
    missing = [k for k in keys if k not in recipes]
    if missing:
        raise RecipeFailed(f"no recipe is recorded for {', '.join(missing)}", kind="broken")
    base, local = st.get("base"), st.get("local")
    vals, site_vals, changed = values(change, base, local)
    runs: list[tuple[str, dict, dict, list]] = []
    if change["section"] == "skills":
        for k, xs in (("skills.add", change.get("add") or []), ("skills.remove", change.get("remove") or [])):
            for s in xs:
                v = {"skill": s}
                runs.append((k, v, v, plan(site, recipes[k], "add", v, v, None)))
    else:
        k = keys[0]
        runs.append((k, vals, site_vals, plan(site, recipes[k], change["op"], vals, site_vals, changed)))
    for k, v, sv, steps in runs:   # every value checked before the first step of the first one
        if step:
            step(k)
        r = _Run(browser, site, k, recipes[k], stop_before_save=dry, judge=judge)
        r.run(steps, v, sv)
    if dry:
        return "filled in, not saved"
    return verify(browser, site, st.get("route") or {}, change, vals, site_vals, changed, judge)


def verify(browser: Any, site: Site, rt: dict, change: dict, vals: dict, site_vals: dict, changed: list[str] | None,
           judge: Callable[..., tuple[bool | None, str]] | None = None) -> str:
    """Read the changed section again with the read route: the change counts only when the site now says it."""
    sec = change["section"]
    try:
        reading, _ = route.run(browser, site, rt, None, only=[sec], judge=judge)
    except route.RouteBroken as e:
        # signed out on the way: the recipe did its part, only the reading back waits for a sign-in
        raise RecipeFailed(f"saved, but reading {sec} back failed: {e}", kind="signed_out" if e.signed_out else "verify",
                           saved=True, what="read it back") from e
    fail = lambda msg: RecipeFailed(f"saved, but {site.name} now shows {msg}", kind="verify", saved=True, what="read it back")  # noqa: E731
    if sec == "basics":
        name = change["item"]
        got = str(reading["basics"].get(name) or "")
        if not same(name, vals[name], got):
            raise fail(f"{name} as “{got[:120]}”")
        return f"{site.name} shows the new {name}"
    if sec == "skills":
        have = {s.lower() for s in reading["skills"]}
        lack = [s for s in change.get("add") or [] if s.lower() not in have]
        left = [s for s in change.get("remove") or [] if s.lower() in have]
        if lack or left:
            raise fail(("no " + ", ".join(lack) if lack else "") + ("; still " + ", ".join(left) if left else ""))
        return f"{site.name} shows the skills as changed"
    items = [i["fields"] for i in reading["items"] if i["section"] == sec]
    read_keys = {k for it in items for k in it}

    def agrees(want: dict, got: dict) -> bool:
        return all(same(k, str(want.get(k) or ""), str(got.get(k) or "")) for k in want if k in read_keys)

    if change["op"] == "remove":
        if any(agrees(site_vals, it) for it in items):
            raise fail(f"“{change['label']}” still")
        return f"“{change['label']}” is gone from {site.name}"
    if any(agrees(vals, it) for it in items):
        return f"{site.name} shows “{change['label']}” as changed"
    # say what differs on the entry most like it
    near = max(items, key=lambda it: sum(same(k, str(vals.get(k) or ""), str(it.get(k) or "")) for k in vals), default=None)
    if near is None:
        raise fail(f"no {sec} at all")
    diff = [k for k in vals if k in read_keys and not same(k, str(vals.get(k) or ""), str(near.get(k) or ""))]
    raise fail("; ".join(f"{k} as “{str(near.get(k) or '')[:80]}”, not “{str(vals.get(k) or '')[:80]}”" for k in diff[:3])
               or f"no {sec} like “{change['label']}”")


def via(site: Site, st: dict, change: dict) -> str:
    """How a ticked change will go up: "recipe" (a recorded recipe, read back by the route: seconds, no AI) or "ai"."""
    recipes, broken = st.get("recipes") or {}, st.get("recipe_broken") or {}
    keys = keys_of(change)
    if not keys or any(k not in recipes or k in broken for k in keys):
        return "ai"
    return "recipe" if reads_for(keys[0]) in route.covers(st.get("route")) else "ai"


__all__ = ["RecipeFailed", "describe", "keys_of", "known_keys", "plan", "push", "render", "validate", "verify", "via"]
