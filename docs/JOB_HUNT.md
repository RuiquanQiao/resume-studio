# Job hunt: applications, companies and platforms

Status (2026-10-18): **step 1 is built** (the three tables, the data, `scripts/hunt.py`, the tracker choice
in Settings; see "Building it, in order"). On 2026-10-18 the user asked for the tracker to find things itself
(next section); steps A–C come first, then the old steps 2–5.

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

## 2026-10-18: the tracker finds things itself

The request, after Claude had researched a company and written only its About:

- The About text is wanted, but its facts must also be parsed into the fields.
- Ideally the user never gives the careers-site URL: they add a role, and Claude works out the company
  and everything behind it.
- Open roles are detected from the site, not typed in by hand.
- The Companies table has only a few fields: the name with the company's real logo, its industry, its
  strength (market cap, or whatever shows it if unlisted), and its roles as a Notion-style relation that
  links to each one. A company has no deadline of its own.
- The workflow: add a company's careers site; Claude signs in with its browser tools and reads the
  roles. One button scans every company, so each day shows what is new. Reuse career-ops' code where
  it fits.
- Far less for the user to manage by hand.

The design below was approved as proposed: write it into docs/JOB_HUNT.md first, then build company
auto-fill and the table, link parsing, and the open-roles scan, in that order.

### Why it was not agent enough

1. **Only the About.** Claude's instructions (`jobhunt/prompts.py`) said "research the company, save it
   with `about`", and `about` touches no field. Claude did what it was told; the instruction was wrong.
2. **Roles typed by hand.** The tracker was designed as a ledger of what the user applies to, not a source
   of what is open. Finding postings was in no step of the plan.
3. **A company deadline, no logo.** Deadlines belong to a batch or a role; a company is a company.
4. **The careers site asked of the user.** Claude can find it, and which hiring system it runs on.

### The design

**A company fills itself.** The user gives a name, a careers site or a posting link. Claude researches and
fills every field, each with its source, in one command (`hunt.py fill <company> --stdin`):

- official name and aliases;
- **logo**, downloaded once into `.studio/hunt/logos/` (from the site's icon or a reputable page), shown
  from there, never hot-linked;
- industry;
- **strength**: listed → market cap with the exchange, ticker and date; not listed → valuation, funding
  round or headcount, with the date;
- headquarters;
- careers sites (campus / social / intern) and the hiring system behind each (own site, Moka, 北森,
  飞书, Greenhouse, Lever, Ashby…);
- the About, as before.

Every field stays editable. A field the user edited is theirs: Claude's later fills leave it alone (it is
listed as "kept yours" in the reply), so the user never fights the agent.

**The companies table has four columns**:

1. logo + name;
2. industry;
3. strength;
4. **open roles**, as chips like a Notion relation: each opens that posting; the user's own applications
   stand out.

The deadline leaves the company: it lives on postings and applications. An existing company deadline moves
to that company's roles that have none. HQ, notes and the account hint stay on the company's page.

**Postings and applications are two layers.**

- **Open postings** (在招岗位): what the company's sites list now: title, category, location, batch
  (校招 / 实习 / 社招), deadline, link, first and last seen. Found by scanning, never typed.
- **Applications** (my roles): what the user goes for. "要投" on a posting makes it an application (linked
  to the posting); an application can still be added by hand.

Xinghe alone lists seven categories and dozens of roles, so they can't all be applications.

**Paste a link.** A posting link, pasted on the Companies tab or in the chat: Claude reads the page, finds
the company (creating and filling it when new), adds the posting with its details, and offers to apply.

**Scanning** (`hunt.py scan`, the **Scan all** button on the Companies tab, a Scan button per company):

- Greenhouse, Lever and Ashby sites are read through their public APIs, with code taken from career-ops'
  `scan.mjs` (MIT, credited in the file), no browser and no Claude.
- Any other site (Xinghe's own site, Moka, 北森…) is read in Resume Studio's browser by a **recorded route**,
  as profiles are (`autofill/route.py`): the first scan lets Claude find the job list, sign-in included (the
  user signs in with the row's button), and record read-only page scripts; later scans replay them with no
  Claude. A route that stops working asks Claude to record it again.
- Postings are matched across scans by a fingerprint (company, title, location, link; after career-ops'
  `listing-fingerprint.mjs`): new ones are marked **new**, gone ones **closed**, changed deadlines shown.
- **Today's changes**: after a scan the Companies tab says what is new, closed or changed, per company.
- **Daily**: when the window opens and the last scan is more than a day old, it scans by itself (only routes
  and APIs, never Claude). Settings can turn this off.

### Building it, in order (before steps 2–5 below)

- **A. Companies fill themselves**: the fields above, logos, `hunt.py fill`, the four-column table, the
  deadline moved to roles, user-edited fields kept.
- **B. Paste a link**: a posting link becomes a filled company + a posting.
- **C. Open postings**: the postings layer, scanning by API and by recorded route, Scan all, today's
  changes, the daily scan.

All three were built on 2026-10-18. Choices made on the way:

- *Built:* career-ops' providers are **called, not copied**. career-ops already reads Moka (`mokahr.mjs`), Feishu
  (ByteDance's jobs.bytedance.com and `*.jobs.feishu.cn`), Tencent, Meituan, Alibaba, Greenhouse, Lever, Ashby,
  Workday and about a hundred more. `jobs/co_scan.mjs` loads its `providers/` from its own install through its
  `_registry.mjs`, the way its `scan.mjs` does, so its updates keep coming (the swappable-provider rule this
  project keeps). Without career-ops installed, every site is read by a route. A live check read 2,000 Tencent
  postings in seconds; Xinghe's own site has no provider, so it gets a route.
- *Built:* **keywords** (career-ops' title filter): the roles the user looks for, on the Companies tab. Postings
  without one are kept but folded away, and don't count as news. Tencent alone lists thousands.
- *Built:* the **daily scan** never opens the browser by itself: sites read by a route wait for a scan the user
  starts (or are read when the browser is open already); providers run either way. It never starts Claude.
- *Built:* a site that needs Claude (no provider, no route yet) or a company with no known site is listed under
  "Today" with one button that asks Claude, for all of them at once.
- *Built:* adding a company in the window asks Claude to fill it in right away (in the open chat). A posting the
  user already applies to (same role name) is linked to that application, not offered again.
- *Built (2026-10-18 evening):* **the logo doesn't wait for Claude.** The user's Xinghe showed a grey "星" box:
  Xinghe was added before fills existed, nobody asked Claude, and the table stood a made-up initial in for the
  logo (user: "你他妈懂不懂什么叫公司logo？！"). Now a company with a site gets its logo from that site by
  itself, in the background (`jobhunt/logos.py`): the icon its pages declare (apple-touch-icon, then the
  largest icon), else `/favicon.ico`. It runs when a company is added, edited or filled, and once for every
  company when the window starts. Sites on a hiring system (Moka, Feishu, Greenhouse…) are skipped, since
  their icon is the vendor's. The logo's source is recorded, and Claude's `logo_url` or the user's own logo
  still replaces it. **A company without a logo shows no logo**, never an invented letter: the table keeps an
  empty slot so the names line up.

## 2026-10-19: tables that work like a Notion database

The request:

- Progress holds only roles the user can apply for. Putting every scanned posting there wastes
  attention.
- The Applying tables should edit like a Notion database: columns that move, and select fields such
  as status that switch with a click in the table itself. Compare with Notion's docs and match them.

Progress already holds only applications (roles the user chose with "想投" or added); scanned postings stay on
the company's page. That stays the rule. Compared with Notion's help pages (Table view; Views, filters, sorts &
groups; Database properties), the tables lacked: moving, resizing and hiding columns, editing a cell where it
is, header menus, filters, groups, several views, and a board. > A 和 B 一起做：就地编辑、拖列调宽隐藏，加上筛选、分组、多个视图标签和按状态的看板视图。

### The design

One component (`web/dbview.js`) draws all three tables (Progress, Companies, Platforms) from a list of
**properties**, each with a type: `title`, `text`, `select`, `date`, `relation`, or a read-only `custom`.

- **Editing in place**, as in Notion: a select cell (status, how it goes in) opens its options under the cell;
  a date cell opens a date field; a text cell becomes a text box (Enter or leaving it saves, Esc cancels).
  The title cell opens the row's page; read-only cells open it too. A status change made in the table is the
  same event as one made on the role's page (it goes into the timeline).
- **Columns**: drag a heading to move it, drag its right edge to resize it. Clicking a heading opens its menu:
  sort ascending / descending, filter by it, group by it (selects), wrap text, hide. A **Properties** menu
  lists every property with an eye to hide or show it, and they can be dragged into order there too.
- **Filters**: rules like "Status is Applied, Tests", "Company contains 星", "Deadline is before …", "is
  empty"; all rules must hold (AND). Shown as chips above the table; each opens to be changed or removed.
- **Sort**: one or more sorts, in order; none keeps the table's natural order (company, then 志愿).
- **Group**: by a select property; each group has a heading with its count and folds away.
- **Views**: each table has several views as tabs, each with its own layout, columns, filters, sort and group.
  A view can be added (Table or Board), renamed, duplicated or deleted (the last one stays). Progress starts
  with "Active" (open statuses), "All" and "Board"; the others with one table view.
- **Board**: one column per option of the group property (status by default), cards showing the title and the
  shown properties; dragging a card to another column changes that value.
- Views live with the data in `.studio/hunt/views.json` (like a Notion database's views, they belong to the
  data, not to one window), through `GET/PUT /api/hunt/views`.

Built the same day (`web/dbview.js`, `jobhunt/views.py`). Choices made on the way:

- *Built:* **a table at its defaults fits its pane.** Notion's columns keep their pixel widths and the table
  scrolls; here, until the user sizes a column, every column is scaled to the pane from its own width, none
  below a readable minimum for its type, and long text wraps. The first column the user drags to a new width
  turns the view's widths into its own (the others keep what they had on screen), and from then on the table
  may run wider than the pane, as in Notion. A board always runs sideways (a column per status).
- *Built:* the **title** cell (the role, the company, the platform) opens the row's page; read-only cells open it
  too; the others edit in place. "By" (the next step's date) is hidden by default and shown under the next step.
- *Built:* on a **board card**, a date says which date it is (no heading above it there).
- *Built:* only one floating panel is open at a time, so a choice inside a panel (a filter's condition, a sort's
  property) unfolds within the panel; there is still no native `<select>` anywhere.
- Not built (Notion has them; not asked for): calculations under a column, ticking several rows to edit them
  together, conditional colour, side peek, the user's own properties, AND/OR filter groups.

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
- The connector contract (`jobs/base.py`) gets a second implementation, so a posting's page in Resume
  works with either. (The Resume workspace's Applications tab was removed on 2026-10-17, see
  `docs/LAYOUT.md`.)

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
