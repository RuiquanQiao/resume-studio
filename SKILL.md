---
name: resume-studio
description: Resume builder with its own window. An experience library with a private fact layer, per-job selections, LaTeX templates, a live one-page preview and PDF export, all in one resume.yaml that the user edits in the window while Claude reads and writes the same file. Resumes can be written in any number of languages; the interface speaks the user's language. Use when the user wants to make or edit a resume / CV, open the resume window, mentions resume studio or resume.yaml, the experience library, exporting a resume PDF, fitting a resume on one page, or polishing an entry. Triggers also on 简历, 改简历, 经历库, 履歴書, 이력서, Lebenslauf, CV.
---

# Resume Studio

A skill with a window: the user edits and previews in a local page, and Claude edits `resume.yaml`
directly. Both sides change the same file; the window watches it and refreshes and re-renders by itself.

`<skill>` below is the folder this SKILL.md is in.

## When invoked

- **`/resume-studio` with no arguments**, or the user asks to open the resume window: don't ask
  anything first. Open the window (next section), say it is open, and stop there.
- **`/resume-studio <task>`**, e.g. `/resume-studio polish Paper2Exam` or `/resume-studio fit JOB-001 on one
  page`: edit `resume.yaml` following the rules below. If the window is open it refreshes by itself;
  there is no need to open it.
- Data file: the one the user names (add `--data`), otherwise always `<skill>/resume.yaml` (git-ignored).
  Never edit a `resume.yaml` just because the current directory happens to have one.
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
- Without a desktop (or when the user asks for the browser) drop `--window`: it opens in the browser
  and prints `UI running at http://...`.
- Every dependency lives in `<skill>/.venv` and pip's cache in `<skill>/.cache`; the first run creates
  them and touches nothing global. LaTeX needs `xelatex` on the system (TeX Live or MiKTeX).
- The window's language follows the computer (English, 简体中文, 日本語, 한국어, Español, Français,
  Deutsch; others fall back to English). The user can pick another in Settings (the gear in the top
  bar, Ctrl+,). That choice belongs to the computer and lives in `.studio/prefs.json`, not in resume.yaml.

## The chat in the window is Claude Code

Every chat in the window's Chat tab is a real Claude Code session: `claude -p --resume <session>`, run
in the data file's folder, loading the user's CLAUDE.md and installed skills, with no replaced system
prompt and no forced reply language. The window only appends a short note with
`--append-system-prompt-file`: whether the user is looking at ALL or a job, which entry is open, and
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
python <skill>/scripts/render.py --data <path>                    # render ALL and every job, report pages
python <skill>/scripts/render.py JOB-001 --data <path>            # one job
python <skill>/scripts/render.py JOB-001 --lang de --data <path>  # same template and layout, another resume language
python <skill>/scripts/render.py JOB-001 --export                 # also copy the PDF and .tex to the export folder
python <skill>/scripts/render.py --check                          # validate the data only
```

- `OVER ONE PAGE` means more than one page.
- Every render lists the **bullets that take more than one line**: `EXP-002 "Paper2Exam" bullet 2:
  2 lines, text is 117% of one line`. This is measured from LaTeX's actual typesetting, not estimated from
  character counts; 117% means cutting about 15% of the text.
- `missing <lang> text` lists entries that lack text in that language and fall back to another one.

## Data format (resume.yaml)

The core idea is "full set → subsets": `entries` is the experience library (the full set: everything the
user really did); every item in `versions` except `ALL` is a **job** (AI Agent Engineer, HPC, graduate
programme…) that picks part of the library to make a resume for that direction.

```yaml
schema_version: 1
settings:
  languages: [en, de]        # resume languages, any number; the first is the default for new jobs
  export_dir: exports        # export folder, relative to resume.yaml
  project_roots: [E:/Forge]  # optional: where the user keeps their own projects
profile:
  name: {en: Alex Chen, de: Alex Chen}
  contacts:                  # every contact; a job can hide some of them
    - {label: email, value: a@b.com, url: 'mailto:a@b.com'}
  notes: ...                 # fact layer
sections:                    # the sections every resume shares; each version decides their order
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
versions:
  - id: ALL                  # reserved: the full-set preview, always first, can't be deleted or renamed
    label: All entries
    lang: en
    template: classic
    headline: ''             # no entries: always shows every entry (library order) and every contact, any length
    sections: [skills, experience, projects, education]   # ALL's section order; new jobs copy it
    layout: {}
  - id: JOB-001              # a job: refers to entries, never copies them
    label: AI Agent Engineer
    lang: en                 # the language this resume is previewed and exported in
    template: classic        # classic | modern | timeline (see resume/templates/latex)
    headline: {en: 'AI Agent Engineer · LLM tooling', de: 'KI-Agent-Engineer · LLM-Tooling'}
    entries: [EXP-001, EXP-002]   # the chosen entries; within a section they appear in this order
    sections: [education, projects]   # this job's own section order; unlisted sections go last
    hide_sections: [awards]  # optional
    hide_contacts: [phone]   # optional: hide contacts by label
    layout: {font_size: 10, margin_x: 1.4, margin_y: 1.2, line_spread: 1.0,
             section_sep: 6, entry_sep: 3, item_sep: 1, accent: 1F4E79, paper: a4,
             font: '', cjk_font: ''}   # keys left out use the defaults
```

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
   the next global `EXP-xxx`. When deleting, update every job's `entries`. IDs are for references only and
   the user never sees them: in replies, call entries by name (or "Projects #2", the number inside its
   section), never EXP-xxx.
5. **Full set and subsets**: new experiences go into the library only (`ALL` includes them by itself);
   whether a job gets them is the user's call. Never give `ALL` an `entries` list, and don't put the job
   target into `profile`: it belongs in each job's `headline`.
6. **One page** applies to jobs only: to fit a job on one page, first adjust its `layout` (font size,
   spacing, margins), then tighten the bullets' wording, and only then suggest picking fewer entries.
   Which entries go in is the user's decision: never drop entries from a job's `entries` yourself. `ALL`
   running to several pages is normal.
7. Tasks started from the window need no PDF compile from you: the window re-renders by itself. In the
   terminal, check pages with `render.py`.
8. **Every bullet on one line**: the user's hard rule. After writing or changing bullets, check with
   `render.py <job> --lang <code>` and keep shortening the bullets it lists until all fit. Order of
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
- **A new job**: append to `versions` with the next `JOB-xxx`, a `label` and `headline`, `lang` (usually
  the first of `settings.languages`), ALL's `sections` order, and the chosen entry IDs in `entries`.

## Layout of this folder

- `Resume Studio.exe`: double-click to open the window (source in `launcher/`, icon in `assets/`, rebuilt
  with `scripts/build_launcher.py`).
- `resume.yaml`, `.studio/`, `exports/`: the user's data, chats, history, preferences and exported PDFs;
  all git-ignored.
- `core/`: the resume-independent "skill + window" layer: YAML store, file watcher, SSE, HTTP server,
  Claude Code sessions (`chat.py`), interface language (`i18n.py`).
- `resume/`: the resume itself: data model, resume languages (`langs.py`), renderer (`render/`, LaTeX
  today, with room for Typst), templates (`templates/latex/*.tex.j2`), API, app assembly (`app.py`).
- `web/`: the build-free front end; `web/i18n/<lang>.json` are the interface translations (keys are the
  English text). `python scripts/i18n_keys.py` lists missing or broken translations.
- `scripts/`: `studio.py` (start; `--window` for the native window), `render.py` (command-line render).
- `tests/` and `TESTING.md`: automated tests and the manual checklist. Run them before handing over
  code changes.
