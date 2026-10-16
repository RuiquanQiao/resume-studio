---
name: resume-studio
description: Resume builder with its own window. An experience library with a private fact layer, subsets of it per kind of role (each with its own version history), LaTeX templates, a live one-page preview and PDF export, all in one resume.yaml that the user edits in the window while Claude reads and writes the same file. Resumes can be written in any number of languages; the interface speaks the user's language. Use when the user wants to make or edit a resume / CV, open the resume window, mentions resume studio or resume.yaml, the experience library, exporting a resume PDF, fitting a resume on one page, polishing an entry, or updating their LinkedIn / BOSS直聘 or any other job-site profile from the resume (the Autofill workspace drives its own browser). Triggers also on 简历, 改简历, 经历库, 履歴書, 이력서, Lebenslauf, CV.
---

# Resume Studio

A skill with a window: the user edits and previews in a local page, and Claude edits `resume.yaml`
directly. Both sides change the same file; the window watches it and refreshes and re-renders by itself.

`<skill>` below is the folder this SKILL.md is in.

## When invoked

- **`/resume-studio` with no arguments**, or the user asks to open the resume window: don't ask
  anything first. Open the window (next section), say it is open, and stop there.
- **`/resume-studio <task>`**, e.g. `/resume-studio polish Paper2Exam` or `/resume-studio fit SUB-001 on one
  page`: edit `resume.yaml` following the rules below. If the window is open it refreshes by itself;
  there is no need to open it.
- Data file: the one the user names (add `--data`); otherwise the one last opened in the window
  (`"data"` in `<skill>/.local/prefs.json`); otherwise `<skill>/resume.yaml` (git-ignored). The
  scripts resolve this the same way when `--data` is left out. Never edit a `resume.yaml` just
  because the current directory happens to have one.
- Reply in the language the user writes in.

## Opening the window

Users normally double-click `<skill>/Resume Studio.exe` (or its Start pin). To open it for them, run
this with the Bash tool and `run_in_background: true`:

```bash
python "<skill>/scripts/studio.py" --window
```

- The window is its own process: it stays open when the session ends or the user switches accounts.
- If a window for the same data file is already open, the script only brings it to the front. Calling
  it again is safe.
- A missing data file is created as an empty skeleton in the computer's language (resume language and
  default section titles). For another file add `--data <absolute path>`; it applies to this run only.
  In the window, Settings → Data file shows the file and opens or starts another one (remembered).
- Without a desktop (or when the user asks for the browser) drop `--window`: it opens in the browser
  and prints `UI running at http://...`.
- Every dependency lives in `<skill>/.venv` and pip's cache in `<skill>/.cache`; the first run creates
  them and touches nothing global. LaTeX needs `xelatex` on the system (TeX Live or MiKTeX).
- The window's language follows the computer (English, 简体中文, 日本語, 한국어, Español, Français,
  Deutsch; others fall back to English). The user can pick another in Settings (the gear in the top
  bar, Ctrl+,). That choice belongs to the computer and lives in `<skill>/.local/prefs.json`, not in resume.yaml.

## The chat in the window is Claude Code

Every chat in the window's Chat tab is a real Claude Code session: `claude -p --resume <session>`, run
in the data file's folder, loading the user's CLAUDE.md and installed skills, with no replaced system
prompt and no forced reply language. The window only appends a short note with
`--append-system-prompt-file`: whether the user is looking at ALL or a subset, which entry is open, and
where the data file and this SKILL.md are. So when called from the window, just follow the rules below.

The window talks to Claude Code over the SDK's two-way stream-json channel (`--input-format
stream-json --permission-prompt-tool stdio`), so everything the terminal has is there: permission
prompts appear as allow / deny cards; AskUserQuestion shows option cards; in plan mode, ExitPlanMode
shows approve / keep-refining cards; the user can stop at any time (a real interrupt), send images and
files, call commands and skills with `/`, and pick the model, effort and permission mode. When the user
has to decide something, just use AskUserQuestion rather than listing options in text.

The editor's buttons (polish, write another language, fit on one line, update from the project) send
their request as a message in the user's interface language; follow it like any other message.

## Checking results without the window

```bash
python <skill>/scripts/render.py --data <path>                    # render ALL and every subset, report pages
python <skill>/scripts/render.py SUB-001 --data <path>            # one subset
python <skill>/scripts/render.py SUB-001 --lang de --data <path>  # same template and layout, another resume language
python <skill>/scripts/render.py SUB-001 --export                 # also copy the PDF and .tex to the export folder
python <skill>/scripts/render.py --check                          # validate the data only
```

- `OVER ONE PAGE` means more than one page.
- Every render lists the **bullets that take more than one line**: `EXP-002 "Paper2Exam" bullet 2:
  2 lines, text is 117% of one line`. This is measured from LaTeX's actual typesetting, not estimated from
  character counts; 117% means cutting about 15% of the text.
- `missing <lang> text` lists entries that lack text in that language and fall back to another one.

## Data format (resume.yaml)

The core idea is "full set → subsets": `entries` is the experience library (the full set: everything the
user really did); every item in `subsets` except `ALL` is a **subset**: the resume for one kind of role
(AI Agent Engineer, HPC, graduate programme…), made by picking part of the library. Files before
schema 2 called them `versions`; the window renames the key by itself when it opens such a file.

```yaml
schema_version: 2
settings:
  languages: [en, de]        # resume languages, any number; the first is the default for new subsets
  export_dir: exports        # export folder, relative to resume.yaml
  project_roots: [E:/Forge]  # optional: where the user keeps their own projects
profile:
  name: {en: Alex Chen, de: Alex Chen}
  contacts:                  # every contact; a subset can hide some of them
    - {label: email, value: a@b.com, url: 'mailto:a@b.com'}
  notes: ...                 # fact layer
sections:                    # the sections every resume shares; each subset decides their order
  - {id: projects, title: {en: Projects, de: Projekte}, kind: timeline}   # timeline | list
entries:                     # the library: every real experience, never limited to one page
  - id: EXP-002              # internal id: stable, independent of sections, never shown in the window
    section: projects
    title: ...               # company / school / project; in a list section, the category
    subtitle: ...            # position / degree / role
    location: ...
    start: 2025-08           # YYYY-MM; the end may be "present"
    end: present
    date: ...                # optional free text, overrides start/end
    link: https://...
    tech: [Next.js, Prisma]  # in a list section, the items
    bullets:
      - {en: 'Built a **multi-stage** pipeline ...', de: 'Mehrstufige Pipeline **aufgebaut** ...'}
      - {en: 'Wrote the onboarding docs', de: ''}    # '' = left off the German resume
    notes: |                 # fact layer: what really happened, never rendered
      ...
    evidence: [E:/Forge/Paper2Exam]   # fact layer: local paths to read (the user adds them with "Choose project folder")
subsets:
  - id: ALL                  # reserved: the full-set preview, always first, can't be deleted or renamed
    label: All entries
    lang: en
    template: classic
    headline: ''             # no entries: always shows every entry (library order) and every contact, any length
    sections: [skills, experience, projects, education]   # ALL's section order; new subsets copy it
    layout: {}
  - id: SUB-001              # a subset: refers to entries, never copies them (older files: JOB-001)
    label: AI Agent Engineer
    lang: en                 # the language this resume is previewed and exported in
    template: classic        # classic | modern | timeline (see resume/templates/latex)
    headline: {en: 'AI Agent Engineer · LLM tooling', de: 'KI-Agent-Engineer · LLM-Tooling'}
    entries: [EXP-001, EXP-002]   # the chosen entries; within a section they appear in this order
    sections: [education, projects]   # this subset's own section order; unlisted sections go last
    hide_sections: [awards]  # optional
    hide_contacts: [phone]   # optional: hide contacts by label
    postings: [{tool: career-ops, id: '12'}]   # optional: the evaluated postings this resume is sent to
                                               # (older files may have one `posting: {…}`; same meaning)
    layout: {font_size: 10, margin_x: 1.4, margin_y: 1.2, line_spread: 1.0,
             section_sep: 6, entry_sep: 3, item_sep: 1, accent: 1F4E79, paper: a4,
             font: '', cjk_font: ''}   # keys left out use the defaults
```

- **A subset's versions** are like commits: the user keeps one in the window ("定稿" / Keep as v2)
  whenever the subset goes out, so it is known later exactly what an employer got. A version freezes
  what the subset renders: its own settings plus the visible layer of the entries it picks (the library
  is shared, so editing an entry changes every subset that uses it). The fact layer is never kept in a
  version. They live in `.studio/subsets/<subset id>.json` next to the data file; don't edit them. To
  see what was sent, read that file (each version's `content` is a small data file of its own); going
  back to one, previewing and exporting it are done in the window (Versions page).
- **Resume languages** are codes from `resume/langs.py`: Latin-script languages (en, de, fr, es, it, pt,
  nl, the Nordic and Central European languages, tr, vi, id, ms…), Greek, Cyrillic (ru, uk, bg), and
  Chinese (zh, zh-TW), Japanese (ja) and Korean (ko). Right-to-left scripts are not supported by the
  templates. Each language gets a fitting font, date format ("Aug 2025", "08/2025", "2025.08") and word
  for an ongoing end date by itself; CJK resumes use that language's own CJK font.
- **Text fields** are a plain string (shared by every language) or a map like `{en: ..., de: ...}`.
  Change only the language you were asked to; keep the others as they are.
- **Bullets** line up by position; each one is a map with one key per language.
  - A missing key (e.g. only `en`) = not written yet: that language's resume shows another language there
    for now, and the window marks it as missing.
  - A key with an empty string (`de: ''`) = this bullet is **not on** the German resume. So languages can
    have different numbers of bullets.
- `settings.project_roots`: the folders where the user keeps their projects (e.g. `E:/Forge`). Look there
  when the user says "look at my projects".
- **Inline markup**: `**bold**`, `*italic*`, `[text](link)`. Everything else is literal; the renderer does
  the LaTeX escaping, so never write LaTeX commands in the YAML.

## Editing rules (Claude must follow these)

1. **Edit resume.yaml directly** and only where the task needs it. Keep the other entries, comments, key
   order and style (inline `{}` or block). Before every save the previous version goes to
   `.studio/history/` next to the data file; look there to roll back.
2. **Fact boundary**: every fact, number, responsibility or scale in the visible layer (title, subtitle,
   bullets…) must be backed by `notes` or `evidence`. When unsure, leave it out or phrase it
   conservatively, and tell the user what information is missing. The notes are what the user would tell
   their lawyer: present them well, never invent.
3. **Don't edit the fact layer**: leave `notes` and `evidence` alone unless the user asks, and never copy
   notes verbatim into bullets.
4. **Stable IDs**: never change an existing entry's `id`, not even when it moves section. New entries take
   the next global `EXP-xxx`. When deleting, update every subset's `entries`. IDs are for references only and
   the user never sees them: in replies, call entries by name (or "Projects #2", the number inside its
   section), never EXP-xxx.
5. **Full set and subsets**: new experiences go into the library only (`ALL` includes them by itself);
   whether a subset gets them is the user's call. Never give `ALL` an `entries` list, and don't put the job
   target into `profile`: it belongs in each subset's `headline`.
6. **One page** applies to subsets only: to fit a subset on one page, first adjust its `layout` (font size,
   spacing, margins), then tighten the bullets' wording, and only then suggest picking fewer entries.
   Which entries go in is the user's decision: never drop entries from a subset's `entries` yourself. `ALL`
   running to several pages is normal.
7. Tasks started from the window need no PDF compile from you: the window re-renders by itself. In the
   terminal, check pages with `render.py`.
8. **Every bullet on one line**: the user's hard rule. After writing or changing bullets, check with
   `render.py <subset> --lang <code>` and keep shortening the bullets it lists until all fit. Order of
   cuts: drop modifiers and repetition → shorter phrasing → drop secondary details. Never change facts.
9. **Languages are rewritten, not translated**: write each language the way a native resume in it reads
   (English: strong past-tense verbs, no pronouns, quantify where the facts allow; Chinese: start with a
   verb, never "我", no articles or clauses, technical terms and numbers kept, dense; other languages:
   the verbs, tense, formality and length local recruiters expect). Languages may have different numbers
   of bullets (see `''` above). Change only the target language.
10. **Adding a resume language**: append its code to `settings.languages`; the window then fills in the
    default sections' titles in that language. Write entries in it only when the user asks.

## Common tasks

- **Add a new experience**: interview one question at a time, write the real story into `notes` first,
  then draft bullets from the notes. The interviewing and writing approaches of installed skills such as
  offer-toolkit-skill or resume-tailoring are good references.
- **Polish one entry**: read its notes and evidence (and the repository and README when needed), then
  rewrite the bullets: start with a verb, put the user's own decisions and results first, one line each
  (check with render.py).
- **A project changed**: the window records each evidence folder's state when Claude last read it (git
  HEAD + uncommitted changes, or a file list, in `.studio/evidence.json`) and tells the user when it
  changes. "Update from project progress" sends the new commits and changed files; read them, put only
  what is worth it into the visible layer, and update every resume language.
- **Write another language**: rule 9. The window's buttons send the request together with the check
  command.
- **A new subset**: append to `subsets` with the next `SUB-xxx`, a `label` and `headline`, `lang` (usually
  the first of `settings.languages`), ALL's `sections` order, and the chosen entry IDs in `entries`.

## Job postings (Job search and the Applications tab, career-ops)

The window connects a job-posting tool. Today that is
[career-ops](https://github.com/santifer/career-ops) (MIT, by santifer), installed in
`<skill>/.local/providers/career-ops` or a folder the user picked. Resume Studio never copies its logic:

- **Two workspaces.** *Resume* is the editor, with an Applications tab (evaluations next to the preview).
  *Job search* shows career-ops' own web interface (its `web/` app, built and run by the window on loopback,
  through a proxy that only lets the window's frame in). The user sees everything career-ops' interface
  shows there; what it lacks runs as chats.
- **Its chats run in its folder.** Evaluating a posting, setup, any of its modes ("All of career-ops" lists
  every one, read from `modes/`), interview prep and the like are Claude Code chats whose working directory
  is the career-ops checkout. Its own CLAUDE.md / AGENTS.md and modes apply there, and the window adds a
  short note. Those chats may still edit resume.yaml (the data folder is an `--add-dir`); the rules above
  apply.
- **`cv.md` and `article-digest.md` in career-ops come from resume.yaml.** Before each message they are
  written from the library (ALL, in one resume language; the entries' notes become the proof points). They
  are never written over edits made on career-ops' side: those show as a conflict the user resolves. To
  change the CV, edit resume.yaml, not those files.
- **career-ops' targeting profile comes from the fact layer.** Its setup chat fills `config/profile.yml`,
  `modes/_profile.md` and `portals.yml`'s title filters from `profile` (name, contacts), `profile.notes`
  (the job-search background, constraints and preferences) and the subsets' labels and headlines (the kinds
  of role applied for), asking only what is missing. `profile.notes` is private: use it to target and score,
  never copy it into anything an employer reads. When it or the subsets change, the window offers an update.
- **A subset is a resume for a kind of role, never for one company.** For a posting the user picks one of
  the existing subsets (the window ranks them by how much of the posting they cover) and it is linked in that
  subset's `postings`; one posting is linked to one subset. Only when none fits does a chat adjust a subset or
  start a new one: then the user has asked you to pick its `entries` (an exception to rule 6), write its `headline` for
  that kind of role and keep it to one page. Never put a company's name, or anything only true of one
  posting, into a resume. Library bullets stay as they are (every subset shares them); propose rewordings and
  let the user decide.
- **Statuses go through career-ops' own writer** (`set-status.mjs`); the tracker and reports are read as
  career-ops documents them.
- **Sign-ups (网申) and what went out** are Resume Studio's own records, in `.studio/applications.json`
  next to the data file (kept apart from the tool, so they survive switching tools; don't edit it):
  - A **sign-up** is one account on one company's careers site: the profile is filled there once and
    several roles are applied to from it, in an order of preference (志愿). Each sign-up has a company,
    the site, the subset it is filled from, and its roles.
  - When the user marks it **sent**, the subset is kept as a version (unless it already is one), the roles
    become career-ops rows through its own path (`reserve-report-num.mjs`, a headed TSV per row in
    `batch/tracker-additions/`, `merge-tracker.mjs`) as Applied, and that version is pinned on each row. An
    ordinary posting is marked sent the same way, with its linked subset.
  - So "which resume did X get" is answered by the pin `{subset, n}`: read that version in
    `.studio/subsets/<subset>.json`.

## Online profiles (the Autofill workspace, Ctrl+3)

The window keeps a **local copy** of each of the user's online profiles, on the sites the user lists
there: LinkedIn and BOSS直聘 come with a guide, and any other site can be added by its address (with
the user's own notes for you, which every read and push passes on). The user edits the copy in the
window, or the profile on the site; the two are kept in sync like a working copy with its remote.
There is no browser extension: Resume Studio starts **its own browser** (Google Chrome, else Microsoft
Edge) with a profile of its own in `<skill>/.local/browser/` and drives it over the DevTools protocol
(`core/browser.py`). The user signs in to each site there once, by hand; it stays signed in.

- **Three copies per site** (`autofill/sync.py`): what the site said at the last sync (`base`), the
  user's copy (`local`), and a fresh read. "Check for updates" merges a read in: what changed only on
  the site comes into the copy, what changed only in the copy waits to be pushed, and a field changed
  on both sides is a conflict the user settles in the window. Dates are kept as `YYYY-MM` / `present`.
- **The read route** (`autofill/route.py`): the first time (or when the site has changed), a chat reads
  the site and **records how to read it**: per page, a short read-only script `async (h) => ({basics,
  items, skills})` that finds things by headings, roles, labels and text, never by generated class
  names. Test a script on the open page with `browser.py extract`, save the route with `browser.py
  route <site>` (it runs once and prints what it read), then `browser.py sync <site>`. After that,
  "Check for updates" runs the route by itself, without you. A route that fails, or reads a section
  that had items as empty, counts as broken, and the window offers a chat to record it anew. Only when
  a part can't be scripted, send that reading once with `browser.py profile <site>`. For a site the user
  added, the route (or the reading) also says what the site has (`"site": {sections, basics, limits,
  lacks}`); the window keeps it.
- **Pushing** ("Make these changes"): a chat gets exactly the ticked changes between the copy and the
  site, and makes them one at a time, reporting each (`browser.py report <site> <id> doing|done|failed`).
  A change reported done counts as on the site; the route then checks the site again. Never touch
  anything else: no messages, connections, posts, applications, or 求职状态.
- **The resume is a reference, not the source.** The window shows, next to the copy, where the chosen
  resume subset words a field differently and which of its entries the copy lacks; the user takes
  them with a click. A profile may say things the resume doesn't.
- Sign-ins, verification codes and captchas are always the user's: stop and ask (AskUserQuestion) and
  never type a password. `browser.py signed-out <site>` makes the window show its sign-in banner.
- **The browser command** (standard library only; the window must be open):

```bash
python <skill>/scripts/browser.py open <url>        # also: back, tabs, tab <n>, front
python <skill>/scripts/browser.py snapshot          # the whole page, a [ref] on every control; --dialog, --in <ref>
python <skill>/scripts/browser.py click <ref>       # real mouse / keyboard input, the way a person's arrives
python <skill>/scripts/browser.py type <ref> --text "..."   # or --stdin with a quoted heredoc for long text
python <skill>/scripts/browser.py select <ref> "<option>"   # native selects; custom pickers: click, then click
python <skill>/scripts/browser.py press Enter       # Escape, Tab, ArrowDown, ctrl+a…
python <skill>/scripts/browser.py screenshot        # prints a PNG path to open with Read
python <skill>/scripts/browser.py upload <ref> <file>       # only files inside the data folder (exported PDFs)
python <skill>/scripts/browser.py extract --file read.js    # run a read script on the open page, print its JSON
python <skill>/scripts/browser.py route <site> --file route.json   # save the read route and run it once
python <skill>/scripts/browser.py sync <site>       # check for updates now, by the route
```

- Every message in an Autofill chat carries a note on how to use the browser, so a user's own request
  there ("add my newest project to LinkedIn") works the same way: one change, checked, then report.
- State per data file is in `.studio/autofill/`: `sites.json` (the user's list, in their order) and
  `<site>.json` per site (the copy, the last sync, the route, ticks, how pushing went; it stays when a
  site is taken off the list). The presets and their guides are in `autofill/sites.py`.

## Layout of this folder

- `Resume Studio.exe`: double-click to open the window (source in `launcher/`, icon in `assets/`, rebuilt
  with `scripts/build_launcher.py`).
- `resume.yaml`, `.studio/`, `exports/`: the user's data, chats, history, preferences and exported PDFs;
  all git-ignored.
- `core/`: the resume-independent "skill + window" layer: YAML store, file watcher, SSE, HTTP server,
  Claude Code sessions (`chat.py`), interface language (`i18n.py`), hosting a skill's own web interface in
  the window (`webhost.py`), and the browser it drives (`browser.py` over `cdp.py`, pages read by `snapshot.js`).
- `resume/`: the resume itself: data model, resume languages (`langs.py`), renderer (`render/`, LaTeX
  today, with room for Typst), templates (`templates/latex/*.tex.j2`), API, app assembly (`app.py`).
- `jobs/`: the job-posting tool behind Job search and the Applications tab: the contract any tool implements (`base.py`),
  the career-ops connector (`career_ops.py`), resume.yaml as the Markdown CV such tools read
  (`cv_markdown.py`), and its routes (`api.py`).
- `autofill/`: local copies of online profiles: the sites (`sites.py`), the profile shape and what a resume subset
  suggests (`profile.py`), the sync (`sync.py`), the recorded read routes (`route.py`), per-file state (`service.py`),
  what Claude is told (`prompts.py`) and the routes (`api.py`).
- `web/`: the build-free front end; `web/i18n/<lang>.json` are the interface translations (keys are the
  English text). `python scripts/i18n_keys.py` lists missing or broken translations.
- `scripts/`: `studio.py` (start; `--window` for the native window), `render.py` (command-line render),
  `browser.py` (Claude's hands in the Autofill browser).
- `tests/` and `TESTING.md`: automated tests and the manual checklist. Run them before handing over
  code changes.
