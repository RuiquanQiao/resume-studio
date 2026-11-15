"""What Claude is told when it reads or updates a profile in the browser.

The chat shows a short message in the user's language (t); the procedure goes in the window's note
(`--append-system-prompt-file`), in English like the rest of that note."""
from __future__ import annotations

import json

from core.i18n import t
from resume import prompts as rp

from .profile import CURRENT_SHAPE
from .sites import Site

BROWSER = rp.SKILL_ROOT / "scripts" / "browser.py"


def cli(data_path: str) -> str:
    return f'python "{BROWSER}" --data "{data_path}"'


def tools_note(data_path: str) -> str:
    b = cli(data_path)
    return f"""\
## The browser

Resume Studio drives a real browser (its own Chrome or Edge window, with the user's sign-ins for
these sites). You work in it only through this command (Bash); every call prints what happened:

- `{b} open <url>` / `back` / `tabs` / `tab <n>`. This chat has a tab of its own: other chats and the window's
  checks work in other tabs at the same time, so stay in yours (`tab <n>` refuses one that other work is using).
- `{b} snapshot` — the whole page as lines, including what is off screen. Controls carry a ref:
  `[12] button "Edit"`, `[13] textbox "Title*" = "current value" maxlength=100`, `[14] select ... options: ...`.
  `snapshot --dialog` shows only the open dialog; `snapshot --in <ref>` one part of the page.
  Refs stay valid while the element is on the page; after a click that changes the page, snapshot again.
- `{b} click <ref>` · `type <ref> --text "..."` (replaces the field's text; for long or multi-line
  text use `type <ref> --stdin` with a quoted heredoc) · `select <ref> "<option>"` (native selects only;
  custom pickers: click, then click the option) · `press Enter|Escape|Tab|ArrowDown|ctrl+a` · `scroll [<ref>]`
  · `hover <ref>` (menus that open on hover). Buttons a site shows only while the mouse is over their row
  appear in the snapshot marked `(shows on hover)`: `click` them as they are, it moves the mouse there first.
- `{b} screenshot` saves a PNG and prints its path: open it with Read when the lines aren't enough.
- `{b} text` — the page's plain text (long lists, when snapshots are cut off).
- A page that shows nothing (a snapshot with no lines, or a note saying so) is still loading or drawing itself,
  not unreadable: snapshot again after a few seconds and take a screenshot. Never tell the user a page can't be
  read until a screenshot shows it blank too, and then say what you tried.
- The browser is shared: other chats and the window's daily scan read pages in their own tabs at the same time,
  and sites far from this computer (in China, from abroad, or the other way) can take 30 seconds or more to load.
  A command that is slow, prints nothing, times out, or says it "first waited" is the browser or the network
  being busy, never a sign the site can't be read and never something wrong with Resume Studio: wait 30
  seconds and run it again, up to 3 times, and take a screenshot in between. Only a site whose screenshot still
  shows no content after that is unreadable; then tell the user each step you tried and the error each gave.

Work like a careful person: one change at a time, read the dialog before saving, check the value after
typing (`type` prints what the field now holds), and never click anything you weren't asked to.
If the site asks for a sign-in, a verification code or a captcha, ask the user in one line to do it in the
browser window, then run `{b} wait-signed-in <site>` (the site's id when it is on the list; without one for any
other site, such as a company's careers site). That hands the tab to the user: your commands on it are refused
until they choose "I've signed in" (on the bar at the bottom of the page, or in the chat), or the site's recorded
sign-in check says they are signed in. Clicking a sign-in button yourself is not signing in. While it says "not
signed in yet", run it again at once. The chat shows the user that you are waiting, like a prompt in a terminal:
continue in the same reply, and never end it to ask the user to tell you. Never type passwords or codes.
When it returns and says no sign-in check is recorded for the site, record one as it says (`{b} requests`, then
`{b} sign-in-check --stdin`): from then on Resume Studio knows by itself, from the site's own server, whether the
user is signed in there.
If the sign-in itself fails here (a code that never arrives, a sign-in that bounces back), the site is refusing
a driven browser: tell the user in one line that they can choose "No code arriving?" in the window, and keep
waiting. Only the user switches the browser to that mode; never try to.
The user can take over your tab at any moment (the bar's "Take over", or just clicking or typing in the page).
Your next command then says "paused": run `{b} wait`, which returns once they hand it back, then take a new
snapshot before anything else, since they may have changed the page. The user can also stop your reply from
the bar."""


def guide(site: Site) -> str:
    """What Claude is told about the site: its guide, and what the user wrote about it."""
    out = site.guide
    if site.notes:
        out += f"\n- The user's own notes about {site.name} (follow them):\n  " + site.notes.replace("\n", "\n  ")
    return out


def _shape_step(site: Site) -> str:
    """For a site the user added: the JSON also says what the site has, so the plan fits it."""
    if site.preset:
        return ""
    shape = json.dumps({'sections': site.sections, 'basics': site.basics, 'limits': site.limits, 'lacks': site.lacks}, ensure_ascii=False)
    known = "" if not site.learned else (
        f" Another company's careers site on {site.system} had: {shape}. Sites on one system usually share it: check"
        " it against this one and correct what differs." if site.learned_from else
        f" Last time it had: {shape} (correct it if the site changed).")
    return f"""
   This site is new to Resume Studio, so the route JSON also says what the site has, at its top level:
   `"site": {{"sections": [...], "basics": [...], "limits": {{"experience.description": 2000}}, "lacks": {{"projects": ["role"]}}}}`
   - `sections`: which of experience, education, projects, awards, skills the profile has a place for
     (map the site's own names: 实习经历/工作经历 = experience, 项目经历 = projects, 获奖/荣誉 = awards).
   - `basics`: which of headline (a one-line title under the name) and about (a summary / 个人优势) it has.
   - `limits`: maximum characters you saw (a `maxlength=` in the snapshot or a counter like 0/500), as
     "section.field" or "basics.headline"; leave out what you didn't see.
   - `lacks`: per section, which of its fields the site has no input for (that text then goes in the
     description).{known}
   Items and fields then use only what you listed."""


def learn_context(site: Site, data_path: str, old_route: dict | None, broken: str, synced: bool) -> str:
    b = cli(data_path)
    why = (f"The recorded route stopped working ({broken}): the site probably changed. Record it anew."
           if broken else "Record it anew (the user asked)." if old_route else
           "Nothing is recorded for this site yet.")
    old = ("\nThe route recorded before (fix what broke, keep what still works):\n```\n"
           + json.dumps(old_route, ensure_ascii=False, indent=1)[:12000] + "\n```") if old_route else ""
    return f"""\
# Resume Studio · read {site.name} and record how

Resume Studio keeps a local copy of the user's {site.name} profile. "Check for updates" reads the site
by a recorded **route**: per page, a short read-only JavaScript function you write and test now, which
Resume Studio then runs by itself every time, without you. {why} Change nothing on {site.name}.

## {site.name}
{guide(site)}

## What to do
1. `{b} open {site.home}`. If it lands on a sign-in page, run `{b} signed-out {site.id}`, tell the user in one
   line to sign in in the browser window, and run `{b} wait-signed-in {site.id}`: it returns once they have
   (run it again while it says "not signed in yet"), and you go on in the same reply. Don't end the reply to
   ask them to tell you. Note the real profile address it ends on.
2. Find the pages that show every section the copy keeps: {", ".join(site.basics + site.sections)} (the full lists,
   not the cards that show two or three items). Use `snapshot` and `text` to see what is there.
3. For each page write a script: `async (h) => ({{basics, items, skills}})`, returning only what that page
   shows, in this shape (dates as YYYY-MM or "present"; only these fields):
```
{CURRENT_SHAPE}
```
   The fields each section has here: {json.dumps(site.public()["fields"], ensure_ascii=False)}. Items need only
   `section` and `fields` (no `key`).
   `h` has: `h.text(el)` (an element's visible text), `h.all(sel, root)`, `h.one(sel, root)`,
   `h.section(heading)` (the part of the page under a heading: a string or a RegExp), `h.waitFor(sel)`,
   `h.scrollToEnd()` (lazy lists), `h.sleep(ms)`.
   - Find things by headings, ARIA roles and labels, `aria-*` attributes, element structure and visible
     text, **never by generated class names** (like `pvs-1x7a`): those change every release.
   - Read only. The script may scroll and click a "see more" / "…more" toggle to show the whole text,
     and nothing else.
   - Test it on the page: `{b} extract --stdin` (a quoted heredoc; no file) prints what
     it returns. Fix it until it matches what you see, every item and the whole text.
4. Save the route: `{b} route {site.id} --stdin` with
   `{{"pages": [{{"url": "<the page's full address>", "reads": ["experience"], "script": "async (h) => ..."}}]}}`
   (`reads`: what that page gives, from basics, {", ".join(site.sections)}). It is run once at once and prints
   what it read: compare it with the pages; send it again until it is right.{_shape_step(site)}
5. Then `{b} sync {site.id}`: Resume Studio reads the site by the route and updates the local copy
   {"(changes from the site come in; the user's own local edits stay)" if synced else "(the first copy of the profile)"}.
6. If a part really can't be read by a script, record the route for the rest and send that part's
   content once with `{b} profile {site.id} --stdin` (the whole profile, in the shape above), and say so.
7. Reply in two or three sentences: what the route reads, and what the sync brought in. The window shows
   the copy; don't list it.{old}"""


def learn_message(site: Site, recorded: bool, broken: str) -> str:
    if broken:
        return t("Reading {site} by the recorded route stopped working. Read it again in the browser and record the route anew. Don't change anything on {site}.", site=site.name)
    if recorded:
        return t("Read my {site} profile in the browser again and record the route anew. Don't change anything on {site}.", site=site.name)
    return t("Read my {site} profile in the browser and record how to read it, so Resume Studio can check it for updates by itself. Don't change anything on {site}.", site=site.name)


def apply_context(site: Site, data_path: str, items: list[dict], current_url: str, st: dict | None = None,
                  redo: list[str] | tuple = ()) -> str:
    def one(i: dict) -> dict:
        if i["section"] == "skills":
            return {"id": i["id"], "op": "update", "section": "skills", "add": i.get("add") or [], "remove": i.get("remove") or []}
        return {"id": i["id"], "op": i["op"], "section": i["section"],
                "now_on_site": i.get("site_label") or (i["label"] if i["op"] != "add" else None),
                "set": {f["name"]: f["to"] for f in i["fields"]} if i["op"] != "remove" else None}
    plan = [one(i) for i in items]
    return f"""\
# Resume Studio · update the {site.name} profile

The user ticked these changes in the window and asked you to make them on {site.name}. Make exactly
these, nothing else.

## {site.name}
{guide(site)}

## The approved changes
`op`: add = a new item with the `set` fields; update = change only the fields in `set` on the item that
is `now_on_site` (a field set to "" is emptied); remove = delete that item; skills: add the `add` ones,
remove the `remove` ones. Text goes in as written (it is already in the right
language; the user may have edited it). Dates are YYYY-MM or "present" (= currently there, no end date).
A field the site doesn't have: leave it out and say so in the report note.

```
{json.dumps(plan, ensure_ascii=False, indent=1)}
```

## How
- The profile was read at {current_url or site.home}: open that page directly.
- Before each change: `{cli(data_path)} report {site.id} <id> doing`. After it is saved and you checked
  the page shows it: `report {site.id} <id> done`. If it can't be done: `report {site.id} <id> failed
  --note "why"` and go on with the next one. The window shows each one's state live.
- Removing an item: confirm the site's own "delete" dialog only for items with op remove.
- A sign-in, code or captcha: ask the user in one line to do it in the browser and run
  `{cli(data_path)} wait-signed-in {site.id}`, then go on in the same reply. Stop and ask the user
  (AskUserQuestion) if the site shows anything else unexpected: a warning that the account is limited, a
  prompt to pay. Never type a password or a code.
- At the end, one short reply: how many were done, and any that failed and why.{recording(site, data_path, items, st or {}, list(redo))}"""


def apply_message(site: Site, n: int) -> str:
    return t("Make the changes I ticked on my {site} profile ({n}), one by one, in the browser.", n=n, site=site.name)


def recording(site: Site, data_path: str, items: list[dict], st: dict, redo: list[str]) -> str:
    """What the apply chat is told about write recipes: record one for each kind of change it makes, so the next
    push of that kind goes up without AI (autofill/recipe.py)."""
    from . import recipe, route
    if not route.covers(st.get("route")):
        return ""   # no read route: a recipe's change couldn't be read back, so none is recorded yet
    have = st.get("recipes") or {}
    broken = st.get("recipe_broken") or {}
    want: dict[str, list[str]] = {}
    for i in items:
        for k in recipe.keys_of(i):
            if k not in have or k in broken or k in redo:
                want.setdefault(k, []).append(i["id"])
    if not want:
        return ""
    b = cli(data_path)
    lines = []
    for k, ids in want.items():
        why = (f" (the recorded one stopped at step {int(broken[k].get('step') or 0) + 1}: {broken[k].get('error')})" if k in broken
               else " (the user asked for it to be recorded anew)" if k in have else "")
        lines.append(f"- `{k}` for {', '.join(ids)}{why}")
    old = {k: have[k] for k in want if k in have}
    old_text = ("\nThe recipes recorded before (fix what broke, keep what still works):\n```\n"
                + json.dumps(old, ensure_ascii=False, indent=1)[:8000] + "\n```") if old else ""
    fields = json.dumps(site.public()["fields"], ensure_ascii=False)
    return f"""

## Record a recipe as you go
Resume Studio replays a **recipe** for each kind of change, without AI, the next time the user pushes one.
Record one for each of these kinds, by making its change through the recipe:
{chr(10).join(lines)}

1. Find the way on the page first (`snapshot`, `snapshot --dialog`): open the editor, look, close it without saving.
2. Write the recipe: `{{"cancel": "<the dialog's cancel button>", "steps": [...]}}`. Steps, in order:
   - `{{"do": "open", "url": "<the page, in full>"}}`: always the first step.
   - `{{"do": "click", "kinds": ["button"], "name": "Edit", "near": ["{{site.title}}", "{{site.org}}"]}}`: a control by its
     kind (button, link, clickable, menuitem, option, tab, checkbox, combobox…) and its name exactly as the snapshot
     shows it (or "/regex/"). `near`: the item's own texts, to find the item (needed to change or remove one).
     `"in": "dialog"` looks only in the open dialog.
   - `{{"do": "type", "field": "Title", "value": "{{title}}"}}`: a text field by its label; real typing.
   - `{{"do": "select", "field": "Start month", "value": "{{start.month_name}}", "options": [...]}}`: a native select.
   - `{{"do": "pick", "field": "Degree", "value": "{{degree}}", "options": ["Bachelor", "Master", ...]}}`: a custom
     dropdown whose options never change (click it, click the option). Copy every option it shows into
     `options`: a value not in the list then fails before anything is touched.
   - `{{"do": "search", "field": "School", "value": "{{org}}"}}`: a searchable or async picker (school, company,
     city): types the value, then takes only the suggestion that reads exactly that (`"input": "<label>"` when
     clicking it opens a separate search box; `"wait": 8` seconds for slow suggestions).
   - `{{"do": "date", "field": "Start date", "value": "{{start}}", "parts": ["{{start.year}}", "{{start.month}}月"],
     "present": "至今"}}`: a date picker (click it, click each part); or `"text": "{{start.year}}-{{start.mm}}"` for a
     typed date field. `present`: the choice for "present".
   - `{{"do": "check", "field": "I currently work here", "on": "end=present"}}`: a checkbox ticked when that holds.
   - `{{"do": "save", "name": "Save"}}`: saves and waits for the dialog to close. A remove: click the item's
     delete (`near` it), then `{{"do": "confirm", "name": "<the site's own confirm button>"}}`.
   - Any step can take `"if": "end!=present"` (or `"link"`, `"!link"`).
   Values: `{{field}}` is what the change puts there, `{{site.field}}` what the site has now; dates have parts
   `.year`, `.month` (1-12), `.mm` (01-12), `.mon` (Jan), `.month_name` (January). The fields here: {fields}.
   Basics use `{{headline}}` / `{{about}}`; skills.add / skills.remove use `{{skill}}` (one skill per run).
   Changing an item runs only the steps for the fields the change sets, so give every field its own step.
   Never by generated class names; never a password, a code, or anything that applies, messages or changes a status.
3. `{b} recipe {site.id} <kind> --stdin` (a quoted heredoc) saves it after checking it.
4. `{b} push {site.id} <id> --dry` fills the change in by the recipe and stops before saving: `snapshot --dialog`
   to check it, then close the dialog without saving. Fix the recipe until the dry run is right.
5. `{b} push {site.id} <id>` makes the change by the recipe, reads the section back with the read route, and
   reports it done or failed itself (no `report` needed). If it failed after saving, look at the page before
   anything else. If a kind really can't be scripted, make that change by hand and report it as before.{old_text}"""
