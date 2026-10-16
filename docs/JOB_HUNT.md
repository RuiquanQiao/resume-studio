# Job hunt: applications, companies and platforms

Status (2026-10-16): **step 1 is built** (the three tables, the data, `scripts/hunt.py`, the tracker choice
in Settings; see "Building it, in order"). Steps 2–5 are the plan.

## The vision (2026-10-16)

Recorded so it is never asked again. The rest of this file is the design that answers it.

- Build our own tracker for 网申 sign-ups and application progress, next to career-ops rather than
  instead of it, with every operation open to an agent like Claude.
- Applications are one table, companies another (what a company is, its prospects, and a way into
  updating its resume fields), recruiting platforms a third. The chat on the left updates progress and
  answers questions about companies.
- One page holds the tables, switched by tabs. A company's "fill in my resume" button opens a page that
  shows each field's change git-style.

And, the same day, about signing in:

- Signing in must not be a chat request every time, and a button must name the site it acts on (it said
  "Open LinkedIn" while the browser was signed in elsewhere). Platforms and companies' careers sites
  need one clear model.

Earlier decisions this builds on (same day, see also SKILL.md):

- Chinese 秋招网申 means filling a profile once on each company's own careers site, then applying to
  several roles with it, ordered like 高考志愿.
- A resume is a **subset** of the experience library, and a subset has git-like **versions**. Every
  application records which subset and which version went out.
- A company site is filled straight from the chosen subset version.
- career-ops stays. A tracker of our own is an alternative, chosen in Settings.

What the user wants, point by point:

1. **Our own tracker**, next to career-ops (not replacing it): applications per company and role, with
   their progress.
2. **Claude can do every operation** on it, the way career-ops' scripts let an agent work: from the chat
   on the left the user updates progress ("Xinghe 一面过了") and asks about companies.
3. **Three tables**, one page with tabs:
   - **Applications** (投递进度): one row per company + role.
   - **Companies** (公司): what the company is, its prospects, the user's notes, and a way into its
     careers site's resume fields.
   - **Platforms** (招聘平台): LinkedIn, BOSS直聘 and the like.
4. **Filling a company's resume**: a "填简历" button on a company opens a page that shows, git-style,
   how each field would change.
5. **Signing in is a button**, owned by the company or platform it belongs to, never a chat request.
6. The buttons always name the site they act on (the "Open LinkedIn" while on Xinghe must not happen).

## Why the button said "Open LinkedIn"

The Autofill page is organised around **the selected profile site**: LinkedIn and BOSS直聘 are its tabs,
and every button in the page acts on the selected one. Xinghe is not one of its sites, so nothing on the
page knows about it. The browser itself was on Xinghe (the dot shows Xinghe's page title), but "Open
LinkedIn" belongs to the LinkedIn tab, not to the browser. It is not a one-line bug: the page has no
place for a company's site. The design below gives every site a row of its own and puts the buttons
on that row.

## Design

### One idea under all three tables: a site where you have a profile

A platform (LinkedIn) and a company careers site (Xinghe) are the same kind of thing for Resume Studio:
an account the user signs in to, holding a profile that should say what a subset version says. The
Autofill engine that exists today already handles this: a local copy, a recorded read route, a
three-way sync, git-style diffs, Claude making one ticked change at a time. It just only knows
platforms. The design generalises it:

| | Platform | Company site |
|---|---|---|
| Examples | LinkedIn, BOSS直聘, 牛客, 实习僧 | Xinghe, ByteDance, Tencent |
| Profile | one, seen by many companies | one per company, its 网申 resume |
| Filled from | the subset the user picks for it | the subset version the sign-up uses |
| Applications through it | many companies | that company's roles (志愿) |

So `sites` gets a `kind` (`platform` | `company`). Everything else about a site — the sign-in state,
whether it refuses a driven browser, the read route, the local copy — is shared.

### The data (our own provider, in the data folder)

Plain files in `.studio/hunt/`, git-ignored with the rest of the data. Our own schema, so switching
trackers later loses nothing.

- **`companies.json`**: one per company.
  - Basics: name, aliases, careers site, industry, size, HQ.
  - **About** (written by Claude, every claim with its source link and the date it was read): what it
    does, business lines, how it is doing, prospects, culture, pay and hiring notes. Refreshed on request.
  - The user's own notes, deadlines and batches (提前批 / 正式批), account hint (which phone or email;
    never a password).
  - Its site: a `sites` entry of kind `company`, with the subset it is filled from.
- **`applications.json`**: one per company + role.
  - Role, link, channel (the company's site, a platform, a referral), 志愿 order within the company.
  - Status (below), applied date, next step and its due date.
  - **Sent**: subset id + version number, kept when it went out (exists today, `jobs/signups.py`).
- **`events.tsv`**: append-only, like career-ops' `status-log.tsv`: date, application, from, to, source
  (window / chat / import), note. Interviews, tests, emails and notes are events too. The application's
  status is the last status event; the log is never edited, corrections are new lines.
- **Platforms** are the existing Autofill sites (`.studio/autofill/sites.json`), now `kind: platform`.

**Statuses**, one fixed list (career-ops' `states.yml` idea), shown in the user's language, mapped to
career-ops' ids for export:

`saved` 想投 → `applied` 已投递 → `assessment` 测评/笔试 → `interview` 面试 → `offer` Offer;
ending in `rejected` 未通过, `withdrawn` 放弃 or `hired` 已入职.
Rounds (一面, 二面, HR 面) are interview events, not states, so the table stays readable.

### Claude does every operation: `scripts/hunt.py`

Like `scripts/browser.py`: standard library only, talks to the open window (one writer, so the tables
refresh live and nothing races). Every command prints what changed.

```
hunt.py list [--status interview] [--company Xinghe]       # the applications table, as text
hunt.py show Xinghe                                        # a company: about, roles, site, events
hunt.py add-company "星河科技" --site https://careers.xinghe.example/campus
hunt.py add-role Xinghe "服务端研发工程师" --url ... [--order 2]
hunt.py status Xinghe/服务端研发工程师 interview --note "一面 周三 14:00"
hunt.py event  Xinghe/服务端研发工程师 "笔试已交"
hunt.py next   Xinghe/服务端研发工程师 "等二面通知" --due 2026-10-25
hunt.py about  Xinghe --stdin   # Claude's research, as JSON with a source and date for each claim
hunt.py sent   Xinghe           # mark the sign-up sent: keeps the subset version, pins it on every role
```

The chat on the left of this workspace gets these in its prompt, so "Xinghe 一面过了，下周二二面" becomes a
status and an event, and "星河科技的发展前景怎么样" reads `show Xinghe`, researches with web search, and saves
what it found (with sources) to the company's About. Claude asks before anything it can't undo.

### The page: one workspace, three tabs

The Autofill workspace (Ctrl+3) becomes **求职 / Job hunt**. A tab bar switches tables; each table
sorts, filters and searches; a row opens its page on the right.

1. **投递 Applications**: company, role, channel, status, subset·vN sent, last event, next step and due
   date. Grouped by company with the 志愿 order shown, or flat. Filters by status. A row's page: the
   timeline of events, the version that went out (preview it as it was), next step.
2. **公司 Companies**: name, industry, roles and best status, deadline, sign-in state, the profile copy's
   state ("in step" / "3 changes to push"). A company's page:
   - **About**: Claude's summary with sources and when it was read; "Refresh" asks Claude again.
   - **Roles**: its applications in 志愿 order; add, reorder, open.
   - **The site**: "Sign in", "Open", "Check for updates", and **"填简历"**, which opens the field-by-field
     git diff (today's Autofill copy view): what the subset version says vs. what the site says, ticked
     changes pushed by Claude one at a time.
3. **平台 Platforms**: LinkedIn, BOSS直聘 and any added: sign-in state, profile copy state, how many
   applications went through it. A row's page is today's Autofill copy view.

The browser bar at the top names **the site the browser is on** (matched by address against companies
and platforms), and its buttons act on that site. On a page no table knows, it offers "Add this
company".

### Signing in is a button

- Every company and platform row shows its sign-in state, learned from its last read (signed in /
  signed out / not checked), and a **Sign in** button.
- Sign in opens the site in Resume Studio's browser. A site that refused a driven browser once is
  remembered (`plain_signin: true`), and its Sign in opens **sign-in mode** right away (the same profile
  with no remote control; built 2026-10-16). Switching back is the bar's button.
- Claude never signs in. When a read finds the site signed out, the row says so and the button is there;
  the chat only points at it.

### career-ops stays

- **Settings → Applications tracker: Resume Studio | career-ops.** Resume Studio's is the default for new
  data files; existing ones keep career-ops until the user switches.
- The Job search workspace (career-ops' own web UI) is unchanged and still there either way.
- With Resume Studio's tracker chosen, sign-ups and applications stay in `.studio/hunt/`, and "Copy to
  career-ops" writes rows through its own `merge-tracker.mjs` when the user wants its analytics.
- One-time **import** from career-ops' `data/applications.md` (statuses mapped through `states.yml`).
- The connector contract (`jobs/base.py`) gets a second implementation, so the Applications tab in the
  Resume workspace works with either.

## Building it, in order

Each step ends usable and tested, and is committed on its own.

1. **Tables and data** (built 2026-10-16): `.studio/hunt/`, `scripts/hunt.py`, the workspace with its three
   tabs, statuses, the events timeline, the tracker choice in Settings. Today's sign-ups
   (`.studio/applications.json`) moved into companies + applications on first start. Built as designed,
   with these choices made on the way:
   - The workspace is called **Applying / 网申** (求职 is already career-ops' Job search, 投递 the Applications tab).
   - A company's page already has an About card (empty until Claude researches it) and "Ask Claude about it";
     step 2 polishes it. Its roles, site, subset and "Mark as sent" replace the old sign-up page.
   - "Sign in to it in Resume Studio's browser" sits in a company's ⋯ menu until step 3 makes it a row button.
2. **Companies' pages**: About with Claude's research and sources; roles in 志愿 order; the browser bar
   naming the site it is on.
3. **Sign in as a button**: sign-in state per site, the `plain_signin` memory, sign-in mode from the row.
4. **填简历 for a company site**: company sites become Autofill sites of kind `company`, filled from the
   sign-up's subset version with the git-style field diff. Xinghe first: its 简历 page has 基本信息, 个人证书,
   教育经历, 语言能力, 自我评价, 社交账号, 上传简历 (the subset version's PDF) and 作品集; no internship or
   project section, so the PDF carries those. ID numbers, home address and the like stay the user's to type.
5. **career-ops bridge**: import, "Copy to career-ops".

## What stays the user's

- Passwords, verification codes, captchas, ID numbers: always typed by the user.
- Submitting an application: the user presses the site's submit button. Resume Studio fills, the user sends.
- Nothing on a site changes without a ticked change; nothing in the tracker is deleted without asking.
