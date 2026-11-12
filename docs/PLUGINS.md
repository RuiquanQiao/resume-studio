# Plugins and the plugin market

Status: phase 1 built (2026-11-11). This file is the design every session works from: read it before touching
`plugins/`, `web/plugins.js` or `scripts/plugin.py`.

## Why

The app grew around one kind of applying: companies, their roles, their careers sites (the Applying workspace,
docs/JOB_HUNT.md). Other kinds of applying need other records:

| Kind of applying | What is tracked | Why the company table doesn't fit |
|---|---|---|
| China's talent programmes (人才引进) | programmes of a city, province, university or employer: who qualifies, what they give (安家费, housing, research funds, 编制), when to apply | the "employer" is a policy, not a company with roles |
| Civil service and public institutions (考公考编) | positions from a position table (职位表): exam type, unit, position code, quotas, requirements, exam dates | one announcement, thousands of positions filtered by degree and major; the stages are exams, not interviews |
| PhD applications | six linked tables (2026-11-12): universities, programmes and positions, supervisors and contact with them (套磁), scholarships, referees, documents | the target is a person or a programme, funding is often a separate competition, the materials are a research proposal and references |

So the core stays the same (the experience library, resumes, the AI chat, the browser, the calendar) and each kind
of applying is a **plugin**: its own records, described as data, plus a guide for the AI. Anyone can make one and
publish it in the **plugin market**.

## What a plugin is

A folder, **data only**: no Python, no JavaScript. A plugin can't run code on the user's computer, so one from the
market is as safe to install as a document. Everything it does goes through the app's own table, page and AI tools.

```
<plugin id>/
  plugin.json      the manifest (below)
  GUIDE.md         what the AI reads before working on this plugin's records: sources, calendars, how to check
  icon.svg         optional, 24×24
```

Where plugins live:

- **bundled** (made with the app): `plugins/bundled/<id>/` in this repository;
- **installed from the market**: `<skill>/.local/plugins/<id>/` (per computer, git-ignored), like other app
  preferences. Which plugins are on is in `.local/prefs.json` → `plugins: {id: true|false}`. Bundled ones start off;
  the user turns them on in Settings → Plugins.
- **records**: `resume-studio-data/plugins/<id>.json`, next to the user's other data, so an export, a move or another
  data file carries them (core/store.py `data_dirs`). Views (columns, filters, sorts, layouts) go with the other
  tables' views in `resume-studio-data/hunt/views.json`, under the table name `p.<plugin>.<collection>`.

### plugin.json

```json
{
  "id": "civil-service",                       // lowercase, digits and "-", unique in the market
  "version": "1.0.0",                          // semver; the market offers an update when it is higher
  "app": ">=1",                                // the plugin format version it needs (plugins/manifest.py FORMAT)
  "name": {"en": "Civil service", "zh": "考公考编", ...},
  "description": {"en": "...", "zh": "..."},
  "author": "Resume Studio",
  "homepage": "https://github.com/...",
  "collections": [{
    "id": "positions",
    "name": {"en": "Positions", "zh": "职位"},
    "item": {"en": "position", "zh": "职位"},   // one record, for "New position" and the chat's line
    "properties": [
      {"key": "title", "type": "title", "label": {...}},
      {"key": "exam", "type": "select", "label": {...}, "options": [{"value": "national", "label": {...}, "color": "blue"}]},
      {"key": "written", "type": "date", "label": {...}},
      {"key": "notice", "type": "url", "label": {...}},
      {"key": "requirements", "type": "longtext", "label": {...}},
      ...
    ],
    "status": "stage",                         // optional: a select whose options are the stages (a board groups by it)
    "dates": ["apply_by", "written", "interview"],   // date properties shown on the calendar and Gantt views
    "views": [{"id": "v1", "name": {...}}, {"id": "v2", "name": {...}, "layout": "board", "group": "stage"}]
  }],
  "suggestions": [{"en": "...", "zh": "..."}],  // the empty chat's suggested first messages in this plugin's workspace
  "guide": "GUIDE.md"
}
```

Property types: `title` (exactly one, the record's name), `text` (one line), `longtext` (wraps, several lines),
`number`, `url`, `date` (YYYY-MM-DD), `select` (one of `options`; `"free": true` lets the user type new ones),
`relation` (2026-11-12: a link to records of another collection of the same plugin, `"to": "<collection id>"`;
`"multiple": true` for several). A relation is kept as the linked record's id (`"R3"`, or a list of them); the AI may
give a record's exact title instead. In the window a single link is a select of the other collection's records (it
groups, filters and sorts), several are chips; both are picked in a list that finds by title and can add a new record
to the other collection. A record's page lists the records that link to it (a university's programmes and
supervisors), with a button that adds one already linked. Deleting a record unlinks it everywhere.
Every label is a `{lang: text}` map; a language it doesn't have falls back to `en`, then to any. A plugin should
carry every interface language of the app (en, zh, ja, ko, de, fr, es): the tests check bundled ones do.

Validation (plugins/manifest.py) refuses a manifest with unknown keys or types, a collection without exactly one
title, duplicate keys, a select without options, a `status` that isn't a select, `dates` that aren't dates, an id
that isn't `[a-z0-9-]`, or files other than `plugin.json`, `*.md`, `icon.svg`/`icon.png` — so a plugin from the
market can't smuggle anything in. Sizes: manifest ≤ 256 KB, the whole plugin ≤ 2 MB.

### Records

`resume-studio-data/plugins/<id>.json`:

```json
{"next": 4, "positions": [{"id": "R1", "title": "...", "exam": "national", "written": "2026-11-30",
                           "created": "2026-11-11T10:00:00", "updated": "...", "by": "you|claude"}]}
```

Ids are `R1, R2…` per plugin. A value is checked against its property (a select's value must be one of its
options unless `free`; a date must be a real date; a url must start with http(s)://); a key the manifest doesn't
have is refused, so the AI can't scatter fields the table never shows. Turning a plugin off or uninstalling it
**keeps its records**: turning it on again brings them back.

## In the window

- Each plugin that is on is **a workspace of its own**, with a button in the top bar after Plan (`W.ws` is
  `p.<plugin>`; Ctrl+5…9 open the first five). A plugin is a kind of applying beside Applying, not part of its
  company table, so it sits next to the other workspaces. Its collections are tabs inside it when it has more than
  one; each is the usual table (web/dbview.js: views, filters, sorts, board, calendar, Gantt when the collection
  has dates, stored as `p.<plugin>.<collection>`), with a "New <item>" button.
- **The top bar is the window's title bar**, so it always keeps at least 120 px empty to drag the window by.
  Plugin buttons that don't fit go into a "more" menu at the end of the row (the priority+ pattern); the plugin on
  screen always keeps its button. The menu also opens Settings → Plugins. Where the labels give way to icons, a
  plugin's button shows the first character of its name. (tests/e2e/test_layout.py
  `test_many_plugins_keep_room_to_drag_the_window` turns fifteen on.)
- A record's **page** (clicking its title): every property as a field (a select as a picker, a date as a date box,
  longtext as a wrapping box), the plugin's name above it, Delete at the bottom. Typing saves like everywhere else.
- **The chat** knows the tab or the record on screen (chat.js `screenLine`: "the civil-service positions" / "the
  position “…”"), and an empty chat there offers the plugin's `suggestions`.
- **Settings → Plugins**: the plugins on this computer, each with a switch, its version, author and source
  (bundled / market), Uninstall for a market one, and **Browse the plugin market**.
- **The plugin market** (a dialog): the market's list, each with name, description, author, version, whether it is
  installed, and Install / Update. Installing downloads the zip, checks its checksum and its manifest, unpacks it into
  `.local/plugins/<id>/` and turns it on.

## For the AI

Every chat in the data folder is told (plugins/prompts.py, a `chats.notes` entry like the plan's):

- which plugins are on, their collections and properties (key, type, options), and how many records each has;
- the command: `python scripts/plugin.py --data <file> list|show|add|edit|delete …` (JSON on stdin for add/edit,
  a change is checked like the window's);
- **before working on a plugin's records, read its GUIDE.md** (once per chat). A guide brings the domain knowledge
  (where the official notices are, the yearly calendar, what to check). It is marked as the plugin's own text: it
  can't change how the AI treats the user, their data or the rest of the app (a guide from the market is
  third-party content).

Facts in records follow the app's rule: dated, with a source link, never invented (a deadline from memory is
worse than an empty field).

## The plugin market

Like Obsidian's community plugins: the market is **a list in a public repository**, and a plugin is published by
a pull request that adds it.

- The list: `plugins/market.json` in this repository: `{"plugins": [{id, name, description, author, version,
  homepage, download, sha256, size, bundled?}]}`. `download` is an https zip of the plugin folder (a GitHub release
  asset, typically); `sha256` its checksum. The bundled plugins are listed too (`"bundled": true`, nothing to
  download).
- The app reads it from GitHub first and from jsDelivr when GitHub can't be reached (mainland China blocks
  raw.githubusercontent.com often): `RESUME_STUDIO_MARKET` overrides the address (tests, a mirror). Nothing goes
  out until the user opens the market.
- Publishing: the developer makes the plugin folder, zips it, uploads the zip to a release of their own
  repository, and opens a pull request adding one entry to `plugins/market.json`. Review checks the manifest
  passes `python scripts/plugin.py check <folder>`, the zip's checksum, and reads GUIDE.md for instructions that
  would turn the AI against the user (exfiltrating data, sending messages, ignoring the user). The repository's CI
  runs the same check on every entry.
- Updates: a higher `version` in the list shows "Update" next to the installed one.

### Paid plugins (later, not built)

Code in a public repository can't be locked: any switch in it can be flipped. What can be sold is what the app
doesn't hold — a service on a server. So paid plugins come with the shared data service (below): the plugin is
free; its records are kept fresh from the service (announcements parsed, position tables split, programmes
updated), which needs an account. Selling in mainland China needs a business licence for WeChat Pay / Alipay;
abroad, a merchant of record (Paddle, Lemon Squeezy). The manifest's reserved key `"service"` will name the
service a plugin uses; today it is refused.

## Later phases (designed, not built)

1. **A company's careers-site profile as a platform** (docs/JOB_HUNT.md's Platforms table). A platform row is
   "a site where the user keeps a profile": general ones (LinkedIn, BOSS直聘) and a company's own careers site
   (网申), linked to its company and shown on the company's page. Same pipeline (the AI reads the form's fields,
   Resume Studio keeps a copy, pushes changes, notices changes on the site). Chinese careers sites mostly run on a
   few shared systems (北森, Moka, 飞书, HotJob, 阿里人才, jobhunt/systems.py); a field map learned on one
   company's site is reused on every company on the same system.
2. **Shared company facts and scan routes.** One public repository of what is public and costs AI to find: a
   company's facts with sources, its careers sites, which recruiting system each runs on, and how to scan it. The
   app pulls it (GitHub, with a jsDelivr / Gitee mirror for mainland China) and offers to contribute what the user's
   AI found. Rules: nothing private ever leaves (notes, statuses, accounts, which companies the user applies to is
   private too: contributions are facts about companies, sent only when the user says so); a scan route that runs
   script in a browser is never taken from the shared repository as code — shared routes are declarative (an API
   address and the paths to the fields) or reviewed and run in a browser profile with no sign-ins.
3. Paid service, as above.

## Files

| File | What |
|---|---|
| plugins/manifest.py | the manifest's schema and checks, localized labels |
| plugins/registry.py | which plugins are on this computer, on or off; install and uninstall |
| plugins/records.py | a plugin's records in the data folder: read, add, edit, delete, checked |
| plugins/market.py | reading the market list, downloading and checking a plugin |
| plugins/api.py | the window's routes (/api/plugins…) |
| plugins/prompts.py | what each chat is told |
| plugins/bundled/*/ | the bundled plugins: talent-programs, civil-service, phd-applications |
| plugins/market.json | the market's list |
| scripts/plugin.py | the AI's command (and `check` for plugin developers) |
| web/plugins.js | the plugins' workspaces and top-bar buttons, tables, record pages, Settings → Plugins, the market dialog |
