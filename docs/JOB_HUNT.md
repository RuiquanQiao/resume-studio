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
- *Fixed (2026-10-22):* **a logo changed from outside shows without a restart.** The panel's Claude drew an SVG
  "logo" and then wrote an official PNG next to it with its own script, around `hunt.py`: the record still named
  the SVG, and the picture's address was versioned by the record's `updated`, so the window kept its cached copy
  until it was reopened, and then showed the drawn one. Now the address is versioned by the picture file itself,
  the logos folder is watched (a change there refreshes the window), and both prompts say a logo is a real
  picture sent as `logo_url`, never drawn and never written into `.studio` directly.
- *Fixed (2026-10-23):* **a logo is looked at before it is kept.** New companies often came in with a blank
  logo. Two causes: the panel's prompt told Claude it could skip the logo once a company had a careers site
  (the site's icon would come by itself), and nothing ever looked at a picture before keeping it. A careers
  site's icon is often blank, 16×16, or the hiring system's own under the company's domain (ByteDance's system
  on `campus.dewu.com`). Now:
  - every `fill` sends a `logo_url` (main site, press kit, Wikimedia Commons' direct upload link, else a web
    search), and the prompt says never to leave a company without one;
  - before a picture is kept, the window draws it at 128 px (`checkLogo` in `web/hunt.js`, any format: png,
    ico, svg, jpg) and reports how much of it shows on a white page; `logos.verdict` refuses a blank one (under
    1.5 % shows), a plain block of one colour with no mark (F1 Solutions' site icon is a red square), or a raster
    under 32 px, and the logo the company had stays. The background icon finder passes
    such icons over for the next candidate. With no window open, only the size a png or ico states is checked;
  - once a logo is saved, `fill` writes the window's drawing to `.cache/logo-look/<id>.png`; Claude looks at it
    and deletes it (older ones go after an hour). `hunt.py logo <company>` gives the same look at a logo already
    there;
  - a company's square mark (its site or app icon) is preferred to its wordmark: the tables show the logo in a
    26 px square, where a wide wordmark (Trip.com Group, Optiver, Metro Finance) shrank to a faint line. A
    wordmark is kept only where the company has no square mark (Infosys, F1 Solutions, xFusion).
- *Fixed (2026-10-23):* **a change shows while the user types.** The Applying workspace skipped every repaint
  while the cursor was in any of its boxes and never caught up, so a change from Claude waited for a switch of
  views. Now every change repaints at once (`repaintKeeping` in `web/ui.js`): the box being typed in comes back
  in the new page with its text, caret and focus. The rebuilt page's own box is kept (its buttons read it), and
  the typed text is copied into it. Only while an IME is mid-word does the repaint wait for the finished word.

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
  too; the others edit in place.
- *Built:* on a **board card**, a date says which date it is (no heading above it there).
- *Built:* only one floating panel is open at a time, so a choice inside a panel (a filter's condition, a sort's
  property) unfolds within the panel; there is still no native `<select>` anywhere.
- Not built (Notion has them; not asked for): calculations under a column, ticking several rows to edit them
  together, conditional colour, side peek, the user's own properties, AND/OR filter groups.

## 2026-10-21: what is booked, on a calendar and a Gantt chart

The request: drop the next step and its date; instead, a role keeps **events**, added with a "+ Add" like the
timeline's, because interviews come in rounds (一面, 二面, 三面…) and each has its time. The usual rounds are offered
as choices. Progress gets a **Gantt** view and a **calendar** view, and the events can be exported (.ics) into the
user's own calendar app, such as Outlook. In the same pass: every page in Applying uses the whole width of its pane
(a role's page had been capped narrower than the table).

- **Data**: `schedule` on the application: `[{id: "E1", title, at: "YYYY-MM-DD[ HH:MM]", minutes, where, note}]`,
  kept in date order. Unlike the ledger it is edited and deleted freely (an interview that moves just moves).
  The old `next` / `due` fields move in once: with a date they become one of these, without one a line in the notes.
- **The role's page**: an Events card on top. Chips for the usual ones (笔试, 测评, 一面, 二面, 三面, HR 面, 终面, then
  any title the user has used), a date and an optional time, Add. Each event edits in place (how long, where,
  notes), can be deleted, and has its own "Add to my calendar".
- **The table**: an Events column with the next one coming (and how many more).
- **Calendar** (a view layout): a month, Monday first, with the events and the deadlines on their days; a click
  opens the role. **Gantt** (a view layout): a bar per role from when it went in (or was added) to today, or to
  its last event once it is over, with its events and deadline on it; it opens with today in sight. Both keep
  the view's filters; neither has columns or groups. Any table whose rows have dates can have them (`o.dated`).
- **.ics** (`jobhunt/ics.py`): every event and deadline, one role's, or one event, written to the export folder
  and opened, which on Windows hands it to the calendar app's import. Times are floating (no time zone): a 14:00
  interview stays at 14:00. Each entry keeps its UID, so importing again updates instead of duplicating.
  A subscribed live feed was not built: Outlook only subscribes to addresses it can reach, not this computer's.
- **Claude**: `hunt.py book / rebook / unbook / ics`; told to book a round whenever the user mentions one with a
  date or time.

## 2026-10-21: where a role is

The request: the location had been written into the role's name ("算法工程师（上海）"). It gets a field of
its own, with several values (one posting is often open in many cities), and remote as a value. Remote often has a
region limit, so a remote one must say which region.

- **Data**: `places` on the application: `[{kind: "onsite" | "hybrid" | "remote", area}]`. `area` is the city, or
  for remote the region it is open to (中国大陆, APAC…). A remote place without a region is refused; "不限" says
  anywhere. Hybrid was added next to the two asked for because postings say it as often as remote.
- **The window**: a Location column of chips (city with a pin, remote with a globe, hybrid with a house); a click
  opens the editor over the cell, saved when it closes. The same editor is on the role's page: the kind
  (on site / hybrid / remote), a box (several cities at once: 南京、上海), Add, and the places the user's other
  roles have, one click away. Search and filters ("Location contains 南京") read them.
- **Postings**: applying to a scanned posting turns its location into places ("北京/深圳" is two); one that can't be
  read as places (a bare "Remote", with no region) stays in the note as before.
- **Claude**: `add-role / edit-role --place 南京 --place "remote: 中国大陆" --place "hybrid: 悉尼"` (on edit-role the
  flags are the whole list; `--no-places` clears it), and is told never to put the place in the role's name.

## 2026-10-21: the world map

The request: Progress gets a world-map view of where its roles are; Companies gets one too, which needs Claude to
find out, when it fills a company in, which cities it hires in.

- **Companies hire in places**: `places` on a company, the same shape as a role's. It is one of the fields Claude
  fills (with a source, and told to always look it up); edited in the window it becomes the user's, like the others.
  A "Hires in" column and a field on the company's page, with the same editor as a role's.
- **Where a name is** (`jobhunt/geo.py`), worked out on this computer, never by an online service: GeoNames'
  list of every place of 15,000 people or more (CC BY 4.0), with its Chinese, Japanese and Korean names, built
  into `jobhunt/data/cities.tsv.gz` (1.1 MB) by `scripts/build_geo.py`. "杭州·余杭区" is found by its parts,
  "北京市海淀区" by its start; of two places with one name, the bigger wins. `GET /api/hunt` carries `geo`:
  every area in the tracker → `{lat, lon, name, country}` or null.
- **Pinning**: what the list doesn't know (a park, a campus), or puts wrong, the user places by a click on the map
  (from "Not found on the map", or a pin's "wrong place?") and Claude with `hunt.py pin "<area>" <lat> <lon>`.
  Pins live in `.studio/hunt/geo.json` and win over the list. `hunt.py where` shows where everything landed.
- **The map** (a view layout, `o.located` on a table, like `o.dated` for the calendar): Natural Earth's land
  (public domain, 50m) drawn as one SVG path in the Miller projection, `web/geo/land.txt` (285 KB, fetched when a
  map first shows). Coastlines only, no country borders: borders are disputed, and the user's question is "where
  are my roles", which cities answer. A pin per city with how many rows are there; pins that would overlap join
  ("上海 +2") and a click on a join zooms in; a click on a pin lists its rows. Wheel zooms where the pointer is,
  dragging pans, "Show every place" fits them all. A company's headquarters is a hollow pin when it doesn't hire
  there too. Beside the map: remote rows by region, places not found, and rows with no place yet. Filters apply;
  sort, group and properties don't show.

## 2026-10-21: the deadline is an event; the batch

(Superseded in part by "no deadline column; the next step" below: no `rolling` flag, no Deadline column.)

The request: a role's deadline was a field of its own, set once and never changed, and many roles have none
(招满即止: they close when filled). Roles already keep events with times, so the deadline becomes one of them,
which every new role starts with. A date field that can say "rolling" was asked for too. And a **batch** (27秋招,
27寒假实习, 27春招) to manage roles together, without being specific to China: abroad the same thing is a 2027
Graduate Program or a Summer Internship intake.

- **The deadline** is the event of kind `apply` in `schedule`: `{id, kind: "apply", title: "投递截止", at, rolling}`.
  `at` is when applications close, or empty; `rolling: true` says it closes when filled, with no date. Rolling is
  a flag beside the date, not a value inside it: everything that reads a date (sorting, the calendar, .ics,
  "late") stays a date, and only the screens show the two as one field. No start date: it was proposed and turned
  down (the day a round opens is worth a note at most).
  - Every new role gets one, its date unknown. It can be deleted ("Add a deadline" brings it back); there is at
    most one per role. Without a date it is on neither the calendar nor the .ics; with one it keeps the UID the
    field had, so importing again updates it.
  - `deadline` stays the word the window and Claude send to set it: a date, `rolling` (or 招满即止 / 滚动招聘),
    or "" to clear. The old field moves into the event once (`Hunt.migrate`).
  - The table's Deadline column reads it: a date (red once past while the role is open), or "滚动招聘 · 招满即止".
    Its cell has a date box, a "rolling" choice and Clear. On the role's page it is the first row of Events; its edit
    has the same rolling choice, and a date ends it.
- **The batch** is `batch` on the application, the user's own words. Nothing is built in, so no country's rounds are
  forced on another. The column is a select whose options are the batches the user's roles already have: type to
  find one, or type a new one and Enter. Group by it, or filter by it ("Batch is 27秋招"), to work a round as a
  whole. On the role's page it is a box offering the same names.
- **Claude**: `add-role / edit-role --deadline YYYY-MM-DD|rolling --batch 27秋招`, told to reuse the batch names the
  user has rather than invent a scheme.

## 2026-10-21: no deadline column; the next step

The complaint: the deadline field was meant to be gone, events alone. The data had no field any more, but the table
still had a Deadline column (it read the 投递 event), so on screen it was still a field of the role. And "no date
yet" was a third state beside a date and 招满即止, where the user meant two: a date read from the posting, or none,
and none is 招满即止.

- **One column for one thing to do: 下一步 (Next step)**, what CRMs call the next activity (Pipedrive, HubSpot): what
  and when in one cell, like an entry in a calendar, so a date never reads as a field of the role. It shows the
  soonest thing still ahead: the 投递 event while the role is not applied to yet (with "还有 N 天", or "已过 N 天" in
  red), then each round to come. "+N more" when there are others; the full list is on the role's page, which a
  click on the cell opens at its events. Sorting by it is sorting by date, 招满即止 last. The Deadline column is gone,
  and the old Events column became this one; saved views put it where the Deadline column was (`views.clean_view`).
- **The 投递 event has a date or none**: none is 招满即止. The `rolling` flag is dropped (`Hunt.migrate`), and so is
  "no date yet". Its edit has "不设日期（招满即止）", which clears the date.
- **Claude** sets it with `--apply-by YYYY-MM-DD` (the field is `apply_by`), only when the posting gives a date.
  A scanned posting's closing date goes into the new role's 投递 event the same way.

## 2026-10-21: booking a round moves the status

The question: are the status and the events linked? They weren't: setting Interviewing booked nothing, and booking
一面 left the status at Applied. They mean the same thing, so now:

- **Booking moves the status forward** (`Hunt._advance`). An interview (a title with 面, or "interview")
  moves the role to Interviewing; a test (笔试, 测评, 机试, OA, assessment…) moves it to Tests. It only goes forward
  along Want to apply → Applied → Tests → Interviewing, never back, and never off an outcome (offer, rejected…).
  A title it can't read (宣讲会) changes nothing; the deadline never does. A renamed event counts too (笔试 → 一面).
  The ledger gets the status line ("约了一面", by window or chat). The window says "状态已改为面试中" with Undo.
- **A status doesn't book anything.** An undated 一面 would be noise (it may not be arranged yet, or it is already
  the second round). Instead the Add row on the role's page has the next round ready: in interviews the first of
  一面 / 二面 / 三面 / 终面 not booked yet, in tests 笔试 or 测评. Only a date is missing.
- **No automatic outcome**: a round whose time has passed says nothing about how it went, so it changes nothing.
- **"Late" is for roles not applied to yet**: a deadline past on a role already applied to no longer shows red.
- **Claude** is told booking does the status, so `status` is for outcomes or when nothing was booked.

## 2026-10-21: a mock interview on every entry

Interviewers ask about what the resume says, so the preparation lives with the entry, under its bullets:
`interview: [{q, a, follow_ups: [{q, a, follow_ups…}]}]`. It is private like `notes`: never rendered, never in a
subset's versions, kept when a version is restored.

- **Predict the questions** (`mock`): Claude reads the entry's bullets, notes and project folders and adds about 8
  questions, most likely first, each with an answer and one or two follow-ups. An optional line says the role or
  JD to aim at. It adds; it never rewrites questions already there or answers the user wrote.
- **Answers stay inside the fact boundary**: first person, about a minute, STAR for behavioural questions, and
  where the facts run out a 【to fill in: …】 says what the user must supply. A made-up number is worse in an
  interview than on paper, because the follow-up finds it.
- **Follow up** (`probe`) on any answered question: the 2–3 things the interviewer asks after hearing that answer.
  **Answer it** (`answer`) on a question the user wrote.
- **Hide answers** to practise: each answer opens on its own; the choice is remembered in this window.
- Claude's change shows in the usual before/after card (the questions as an outline) with Undo.
- The questions are written in the resume language being edited, so an English and a Chinese set can sit side
  by side.

## 2026-10-21: a role reads its own page; links leave the window; what Claude fills, the user can change

Found in use: "Want to apply" added a role with an empty JD; clicking a careers site's link blanked the whole
window and loaded the site inside it; the careers sites Claude found could not be changed; one company's
Feishu site scanned "OK" with 0 roles while it had 37 open.

- **A role with a link reads its page** (`jobhunt/reader.py`, no Claude): whenever a role gets a link and has
  no JD (Want to apply, a link typed in, `add-role --url`), its page is opened in Resume Studio's browser, in a
  tab of its own in a window off the screen (not minimized: a minimized window stops painting and script-drawn
  sites never draw the posting), and closed again. The JD is the smallest part of the page holding the most
  section headings (岗位职责, 任职要求, Responsibilities…); without headings, the longest run of text, and only a
  long one (a short notice must never replace a JD). The location and the apply-by date are taken from labelled
  lines, and only where the role has none. The JD card says "read from its page" with the date, "Read its page
  again" replaces it after asking, a JD edited by hand drops the mark, and a page that didn't read says so and
  offers Claude. Checked on Moka, 北森 (zhiye), Feishu and Workday postings.
- **Links open in the computer's own browser.** One handler in `web/ui.js` catches every link to another site
  (left or middle click), anywhere in the window. Before, only the chat and the plan caught theirs; any other
  link replaced Resume Studio with the site. "Open its site" uses the everyday browser too; Resume Studio's
  browser is in the menu, for signing in and for Claude's work.
- **The careers sites are the user's to change**: each row's kind and address, removed, or one added; an edit
  makes them the user's (`kept`), so Claude's next fill leaves them. The field below is now "Where you sign up".
  The same for the logo: change it (a picture from the computer) or remove it, and it stays so.
- **Feishu portals**: a Feishu tenant has a portal per kind of hiring, named by the first part of its address
  (`/123456` campus, `/index` experienced), and its search API answers for the portal in a `website-path`
  header, with nothing at all without it. career-ops' provider sends none; `jobs/co_scan.mjs` adds it from the
  site's address (career-ops' file is left alone, so its updates keep flowing) and points each role's link at
  that portal. An address that is no portal now reports an error instead of an empty "OK".
- **Nothing silent**: a scan that read fine and found nothing says so and where to look; a role already added
  shows a tick and its status, so it never reads like the "+ Want to apply" button next to it.

## 2026-10-22: a company added twice

A company the user forgot they had added came in a second time when its name was written another way: the
check compared the new name with the names on the list only, never with their aliases (where Claude puts the
official and English names), and never caught 字节 next to 字节跳动有限公司.

- **The same company** (its name or one of its aliases, any case and spacing) is refused: in the window, in
  "Add an application" (there the role goes to that company), from Claude (`add-company`), and as a new name
  for another company. The form stays open with what was typed and names the company, with the date it was
  added and a button that opens it.
- **A close name** (one inside the other: 字节 / 字节跳动有限公司, ByteDance / ByteDance Ltd) asks, never refuses:
  the company it may be, a button to open (or use) it, and "It's another company: add it". Claude adds such a
  company with `--different` only when it really is another one. That button sits on the left: the notice opens
  where the Add button was, and a second click must not land on it.
- **A company's arm in a country is another company**: its own careers site, its own hiring. When what the
  longer name adds starts or ends with a country or region (McDonald's Australia Pty Ltd, Siemens Deutschland,
  日本マクドナルド, 麦当劳（中国）有限公司, McDonald's APAC), nothing is asked. The names are every country,
  territory and region in 29 languages from Unicode CLDR, plus the regions companies are run by (APAC, EMEA,
  DACH, 大中华区…), built by `scripts/build_regions.py` into `jobhunt/data/regions.txt` and used offline. Short
  names that are parts of ordinary words (US in campus, タイ in タイヤ) are left out, and a name in the middle
  of what was added doesn't count.

## 2026-10-26: no closing date made up; built-in events follow the language

Every new role got an apply event with no date, and no date was shown as "滚动招聘 · 招满即止": a whole column
said the roles close once filled when the posting had only not said when it closes. The empty Add row under it
also had today's date filled in, which read like a deadline someone had set. And an event's name was saved in
the language of the day it was made, so the same list said 投递截止 on some roles and "Apply by" on others.

- **A closing date only from what the posting says.** A role gets an apply event when the posting (a scan,
  Claude reading its page, or the user) gives a date, or says it closes once filled: then `rolling: true`.
  Not knowing is no event, and the Next step cell stays empty. `apply_by: ""` drops the event. Claude is told
  never to guess a date or call a role 招满即止 (`jobhunt/prompts.py`).
- **招满即止 is its own state again**, set by the posting's words (rolling, 招满即止, 滚动招聘…) or by the user's
  "不设日期（招满即止）" toggle in the event's edit. The 2026-10-21 rule "no date is 招满即止" is gone.
- **The Add row's date starts empty**: the user picks it.
- **Built-in events read in the interface's language**: the apply event and the usual rounds (笔试, 测评, 一面,
  二面, 三面, HR 面, 终面) are saved by `kind` with no title, and shown by name in whichever language the window
  is in (as Gmail shows Inbox in the user's language while the user's own labels stay as written). A title
  the user types, or renames one to, is theirs and stays as written. The chips are the built-in names in the
  interface's language, then the user's own titles.
- **Once, on old data** (`Hunt._builtin_events`): titles that name a built-in event in any interface language
  become that kind, and the dateless apply events made for every role are dropped, unless the user put a note,
  a place or their own name on one.

## 2026-10-26: a long table scrolls with its page; the heading row stays

To keep the heading row on screen, the table had been made a box that scrolls on its own and takes "the height
the page has left". That only worked where a toolbar alone stands above the table. On the Companies tab the
Today card took most of the window: the table became a 345px slot (275px, two companies, in a 700px window),
the page no longer scrolled, and in short windows the frame's foot was cut off and two scrollbars were nested.

- **The page scrolls, as everywhere else**, and the table is as long as its rows.
- **The heading row is a box of its own** (`.dbv-head`) that sticks to the top of the page while the table
  passes under it. A sticky box sticks to the nearest box that scrolls, and the rows' box must scroll sideways,
  so the heading row and the rows are two tables with the same columns (as in AG Grid and Notion's inline tables).
- **Sideways**: the rows' box scrolls, with frozen columns kept at its left. The heading row and a scrollbar
  that stays at the foot of the window (`.dbv-hbar`) move with it. Resizing a column resizes both tables.
- **A repaint keeps the table where it was**, down the page and sideways (`DBV.scrollX`, per view).
- Checked by `test_a_long_table_scrolls_with_its_page_under_a_heading_row_that_stays` (every tab, a 650px
  window) and two layout states (scrolled both ways; the Companies tab scrolled to its end).

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
  - Status (below), applied date, its 内推码 (`referral`, empty: none), what is booked (`schedule`, below).
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
hunt.py book   Xinghe/服务端研发工程师 二面 --at "2026-10-25 14:00"
hunt.py about  Xinghe --stdin   # Claude's research, as JSON with a source and date for each claim
hunt.py sent   Xinghe           # mark the sign-up sent: keeps the subset version, pins it on every role
```

The chat on the left of this workspace gets these in its prompt, so "Xinghe 一面过了，下周二二面" becomes a
status and an event, and "星河科技的发展前景怎么样" reads `show Xinghe`, researches with web search, and saves
what it found (with sources) to the company's About. Claude asks before anything it can't undo.

### The page: one workspace, three tabs

The Autofill workspace (Ctrl+3) becomes **求职 / Job hunt**. A tab bar switches tables; each table
sorts, filters and searches; a row opens its page on the right.

1. **投递 Applications**: company, role, channel, status, subset·vN sent, last event, the next thing booked
   date. Grouped by company with the 志愿 order shown, or flat. Filters by status. A row's page: the
   timeline of events, the version that went out (preview it as it was), what is booked.
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
