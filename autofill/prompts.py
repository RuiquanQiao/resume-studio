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

Work like a careful person: one change at a time, read the dialog before saving, check the value after
typing (`type` prints what the field now holds), and never click anything you weren't asked to.
If the site asks for a sign-in, a verification code or a captcha, stop and ask the user to do it in the
browser window (AskUserQuestion with a "Done" option), then continue. Never type passwords or codes.
If the sign-in itself fails here (a code that never arrives, a sign-in that bounces back), the site is refusing
a driven browser: run `{b} sign-in <the sign-in page's address>`, which reopens the same browser without
remote control, and ask the user to sign in there and press "switch back" in the window."""


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
    known = "" if not site.learned else (
        f" Last time it had: {json.dumps({'sections': site.sections, 'basics': site.basics, 'limits': site.limits, 'lacks': site.lacks}, ensure_ascii=False)}"
        " (correct it if the site changed).")
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
1. `{b} open {site.home}`. If it lands on a sign-in page, run `{b} signed-out {site.id}` and ask the user to
   sign in in the browser window; continue once they have. Note the real profile address it ends on.
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


def apply_context(site: Site, data_path: str, items: list[dict], current_url: str) -> str:
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
- Stop and ask the user (AskUserQuestion) if the site shows anything unexpected: a sign-in, a captcha,
  a warning that the account is limited, a prompt to pay. Never type a password or a code.
- At the end, one short reply: how many were done, and any that failed and why."""


def apply_message(site: Site, n: int) -> str:
    return t("Make the changes I ticked on my {site} profile ({n}), one by one, in the browser.", n=n, site=site.name)
