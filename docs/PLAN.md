# The plan: one document that lasts, and its versions

## Why (2026-10-19)

- Talking to the chat helps only for a moment. What lasts is a persistent document: knowing where you
  stand and what you want tells you what to do next.
- Progress rarely keeps pace with the first plan, so the plan will change every so often. Its versions
  matter as much as the plan.
- Plan is its own workspace, after Applying, level with Resume and Job search.
- No fixed method or template. A new user sees one line (tell Claude what worries you; it helps sort it
  out here), and the page can be little more than a well-set Markdown document. It covers, without
  forced headings: where you stand (short of time, of experience…), the easiest way forward, and
  progress against what you committed to.

Two drafts were turned down before this one:
- The first was built around applications (counts of sent, tests, interviews) and placed inside Applying.
- The second fixed the structure: an ability map, ratings, set sections.
Both missed the point. The plan has no fixed shape.

## Design

- **A fourth workspace, Plan (Ctrl+4)**, after Applying. The shared chat is on the left as everywhere else.
- **Empty**, the page says one line: "有什么焦虑，就跟 Claude 说：它会帮你在这里理清。" There are no buttons,
  templates or suggestions. The empty chat beside it has no suggestions either; the user says it in their
  own words.
- **Written**, it is a Markdown document set for reading: a 720px measure, 15px text with 1.75 leading, and
  headings that step clearly. Tables are allowed, and code wraps.
  - `- [ ]` items are checkboxes that tick with one click, or with Space or Enter.
  - That is the only thing the page itself does.
- **The top bar** holds Edit and Versions.
  - Edit opens the raw Markdown with a "why it changed (optional)" field. Save is the button or Ctrl+S, and
    Esc cancels.
  - If Claude changed the plan meanwhile, a note says so. Saving keeps what Claude wrote in the versions.
- **What goes in is Claude's to shape with the user.** The three directions are guidance in Claude's
  instructions, not headings:
  - where they stand (time, missing skills and experience, constraints);
  - the easiest way forward (one direction, what to do first, what to leave, and why);
  - progress (the commitments they set, and what they did, by date).
  At the start of a chat about the plan or learning, Claude reads the plan first. If the user is behind on
  their own commitment, Claude says so plainly and kindly, once, and asks whether to adjust.
- **Versions**, because the plan will change every so often:
  - A rewrite keeps a version with its reason. That covers Claude's `write --why` and the user's own edit,
    where the reason is optional.
  - A ticked box or a line of progress goes into the latest version, so the list shows only the times the
    plan changed course.
  - The Versions panel lists Now and every version with its date, who made it and why. Opening a version
    shows it as it was (its boxes don't tick) or as its changes since (the line and word diff the subset
    versions use).
  - "Go back to this version" keeps it as a new version, so nothing is ever deleted.
  - An edit made in another editor is kept as its own version ("Edited outside the window") before the
    next rewrite.
- **Open, not tied to Claude**: the plan is `.studio/plan/plan.md` in the data folder, plain Markdown that
  opens in any editor. Its versions are in `.studio/plan/versions.json`. The window watches both files and
  follows any change.

## Claude's command: `scripts/plan.py`

It needs no window: it writes the file, and an open window follows by itself.

```bash
python scripts/plan.py show [--version 2]
python scripts/plan.py write --stdin --why "<why it changed>"         # the whole plan, kept as a version
python scripts/plan.py note "<what happened>" [--under <heading>] [--on YYYY-MM-DD]
python scripts/plan.py done "<words of a checkbox item>" [--undo]
python scripts/plan.py versions
```

`note` adds a dated line at the end of a section. By default that is the last section whose heading is about
progress (进展, 记录, 日志, progress, log…). If there is none, it starts one at the end.

Every chat in the data folder is told about it (`studyplan/prompts.py`), including how the plan stands now:
empty, or when it last changed and its latest version.

## Built (2026-10-19)

- `studyplan/store.py` (standard library only), `studyplan/api.py`, `studyplan/prompts.py`, `scripts/plan.py`.
- `web/plan.js`, the Plan tab in `web/index.html`, and Ctrl+4. `md()` in `web/app.js` learned `- [ ]` and `---`.
- Tests: `tests/test_plan.py`; `test_the_plan_is_one_document_with_versions` in `tests/e2e/test_ui.py`; four
  layout states (empty, a long document, editing while Claude changed it, versions and a comparison).

## How Claude knows how to plan: `skills/planning/SKILL.md` (2026-10-19)

The user asked how Plan knows what a good plan is, and agreed to split it:

- **The note every chat gets** (`studyplan/prompts.py`) keeps what belongs to the window: how the plan
  stands, the commands, the hard rules. It also tells Claude to read the planning skill before working out
  or reworking a plan, by its full path, so it is always read and not left to chance.
- **The method is a skill**, `skills/planning/SKILL.md`: an open Agent Skill in plain Markdown. The user can
  edit it, and any agent can use it outside this window.

The skill should give Claude the common knowledge students miss, such as China's 春招 and 秋招 rhythm
and Australia's summer internships, so that no key date goes unnoticed.

So the skill starts with today's date against the hiring calendars:
- China: 届 is set by the degree's date, and 暑期实习, 提前批, 秋招, 补录 and 春招 each have their months.
- Australia: summer internships and graduate programs close months ahead, and work rights decide who can
  apply.

After that come the tests themselves, measuring where the student stands by doing rather than by rating,
the easiest path back from the nearest live deadline, small commitments, tracking and replanning, and tone.

It teaches the method. It doesn't impose a shape on the document. Dates in it are examples with sources;
Claude checks the employer's own page each cycle.

## What stays the user's

- Every word they write stays. Claude rewrites with them, and the old wording stays in the versions.
- Claude ticks a box only when the user says it is done.
- The raw Markdown is always one click away (Edit), and the file is theirs to open anywhere.
