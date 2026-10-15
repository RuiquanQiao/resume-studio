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

- `{b} open <url>` / `back` / `tabs` / `tab <n>`
- `{b} snapshot` — the whole page as lines, including what is off screen. Controls carry a ref:
  `[12] button "Edit"`, `[13] textbox "Title*" = "current value" maxlength=100`, `[14] select ... options: ...`.
  `snapshot --dialog` shows only the open dialog; `snapshot --in <ref>` one part of the page.
  Refs stay valid while the element is on the page; after a click that changes the page, snapshot again.
- `{b} click <ref>` · `type <ref> --text "..."` (replaces the field's text; for long or multi-line
  text use `type <ref> --stdin` with a quoted heredoc) · `select <ref> "<option>"` (native selects only;
  custom pickers: click, then click the option) · `press Enter|Escape|Tab|ArrowDown|ctrl+a` · `scroll [<ref>]`
- `{b} screenshot` saves a PNG and prints its path: open it with Read when the lines aren't enough.
- `{b} text` — the page's plain text (long lists, when snapshots are cut off).

Work like a careful person: one change at a time, read the dialog before saving, check the value after
typing (`type` prints what the field now holds), and never click anything you weren't asked to.
If the site asks for a sign-in, a verification code or a captcha, stop and ask the user to do it in the
browser window (AskUserQuestion with a "Done" option), then continue. Never type passwords or codes."""


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
   This site is new to Resume Studio, so also add what it has, at the top level of the JSON:
   `"site": {{"sections": [...], "basics": [...], "limits": {{"experience.description": 2000}}, "lacks": {{"projects": ["role"]}}}}`
   - `sections`: which of experience, education, projects, awards, skills the profile has a place for
     (map the site's own names: 实习经历/工作经历 = experience, 项目经历 = projects, 获奖/荣誉 = awards).
   - `basics`: which of headline (a one-line title under the name) and about (a summary / 个人优势) it has.
   - `limits`: maximum characters you saw (a `maxlength=` in the snapshot or a counter like 0/500), as
     "section.field" or "basics.headline"; leave out what you didn't see.
   - `lacks`: per section, which of its fields the site has no input for (that text then goes in the
     description).{known}
   Items and fields then use only what you listed."""


def read_context(site: Site, data_path: str, want: dict, lang: str) -> str:
    keys = [{"key": i["key"], "section": i["section"], "label": i["label"]} for i in want["items"]]
    covers = ", ".join(site.basics + site.sections) if site.preset or site.learned else \
        "whatever it has of: headline, about, " + ", ".join(site.sections)
    return f"""\
# Resume Studio · {site.name} profile

The user wants their {site.name} profile to match a resume version. Your part now is only to READ the
profile and report what it says; change nothing on the site.

## {site.name}
{guide(site)}

## What to do
1. `open {site.home}`. If it lands on a sign-in page, run `{cli(data_path)} signed-out {site.id}` and
   ask the user to sign in in the browser window; continue once they have.
2. Read every section the plan covers: {covers}. Open each section's full
   list; read each item's whole text (descriptions included; a "see more" may need a click).
3. Write what you found as JSON in this shape (only these fields; dates as YYYY-MM or "present"):

```
{CURRENT_SHAPE}
```
   The fields each section has here: {json.dumps(site.public()["fields"], ensure_ascii=False)}.{_shape_step(site)}
   For each item on the site, set `key` to the resume item it corresponds to (same organisation and
   role, even if worded differently), or null. The resume items:
```
{json.dumps(keys, ensure_ascii=False, indent=1)}
```
4. Drafts: {_drafts(site, lang)}
5. Send it: `{cli(data_path)} profile {site.id} --stdin <<'JSON'` … `JSON` (or `--file <path>`). It
   answers with the plan the user will see, or with what is wrong with the JSON; fix and send again.
6. Reply to the user in two or three sentences: what you read, and that the window now lists the changes
   for them to tick. Don't list the changes yourself; the window shows them."""


def _drafts(site: Site, lang: str) -> str:
    if "about" not in site.basics:
        return "none for this site."
    name = rp.lang_name(lang)
    called = {"boss": "个人优势", "linkedin": "About"}.get(site.preset, "the summary / about field")
    return (f"resume.yaml has no text for the profile's \"about\" ({called}). If the site has one, "
            f"draft it in {name} from the resume version's visible layer only (headline, entries, bullets; "
            f"the data file's `notes` are private and must not appear), the way a strong {site.name} profile reads, "
            f"within {site.limits.get('basics.about', 'the site’s')} characters, and put it in `drafts.about`. "
            "The user edits it before anything is changed.")


def read_message(site: Site, version_label: str, lang: str) -> str:
    return t("Read my {site} profile in the browser and compare it with my resume “{version}” ({lang}). Don't change anything on {site} yet.",
             site=site.name, version=version_label, lang=rp.lang_name(lang))


def apply_context(site: Site, data_path: str, items: list[dict], current_url: str) -> str:
    plan = [{"id": i["id"], "op": i["op"], "section": i["section"], "now_on_site": i.get("site_label") or (i["label"] if i["op"] != "add" else None),
             "set": {f["name"]: f["to"] for f in i["fields"]} if i["op"] != "remove" else None}
            for i in items]
    return f"""\
# Resume Studio · update the {site.name} profile

The user ticked these changes in the window and asked you to make them on {site.name}. Make exactly
these, nothing else.

## {site.name}
{guide(site)}

## The approved changes
`op`: add = a new item with the `set` fields; update = change only the fields in `set` on the item that
is `now_on_site`; remove = delete that item. Text goes in as written (it is already in the right
language; the user may have edited it). Dates are YYYY-MM or "present" (= currently there, no end date).
A field the site doesn't have: leave it out and say so in the report note.

```
{json.dumps(plan, ensure_ascii=False, indent=1)}
```

## How
- The profile was read at {current_url or site.home}.
- Before each change: `{cli(data_path)} report {site.id} <id> doing`. After it is saved and you checked
  the page shows it: `report {site.id} <id> done`. If it can't be done: `report {site.id} <id> failed
  --note "why"` and go on with the next one. The window shows each one's state live.
- Removing an item: confirm the site's own "delete" dialog only for items with op remove.
- Stop and ask the user (AskUserQuestion) if the site shows anything unexpected: a sign-in, a captcha,
  a warning that the account is limited, a prompt to pay. Never type a password or a code.
- At the end, one short reply: how many were done, and any that failed and why."""


def apply_message(site: Site, n: int) -> str:
    return t("Make the changes I ticked on my {site} profile ({n}), one by one, in the browser.", n=n, site=site.name)
