# Resume Studio

A Claude Code skill with its own window. You edit and preview your resume in a real
UI; the chat inside it *is* Claude Code — same brain, same skills, same CLAUDE.md —
and both sides edit one `resume.yaml`.

It is also a first attempt at "skills with a UI": `core/` knows nothing about resumes
(file store, watcher, events, HTTP, Claude Code sessions) and is meant to be reused.

## Use

- **Double-click `Resume Studio.exe`** (right-click → Pin to Start, if you like). The first
  run builds `.venv` (about a minute, with a small progress note).
- Or, inside Claude Code: `/resume-studio`.
- Or from a shell: `python scripts/studio.py [--window] [--data other/resume.yaml]`.

Your data lives in this folder: `resume.yaml`, plus `.studio/` (chats, history) and `exports/`.
All three are git-ignored, so pulling updates never touches them. Settings (the gear, Ctrl+,) shows
the data file and can open or start another one anywhere; the window remembers it in `.local/prefs.json`.

Needs Python 3.10+, `xelatex` (TeX Live / MiKTeX) and a logged-in Claude Code for the chat; the
Applications tab also needs git and Node.js 22+.
Everything is installed into this folder (`.venv`, `.cache`, `.local/providers`); nothing global.
The exe is a 16 KB launcher built from `launcher/Launcher.cs` by `scripts/build_launcher.py`
with the C# compiler that ships with Windows.

## What it does

- **Experience library** with a fact layer (`notes`, `evidence`) that templates never render —
  it exists so Claude knows the real story and never overclaims.
- **ALL and subsets**: `ALL` shows the full set. Each subset is the resume for one kind of role
  (AI agent engineer, HPC, management trainee…): tick which entries go in, reorder, give it its own
  headline, hide contacts, pick template (Classic / Modern / Timeline), language, and every
  layout knob (font size, spacing, margins, colour, paper, font).
- **Live preview** as page images; in a subset a second page is flagged as overflow.
- **Chat with Claude Code** in the left sidebar: streamed replies, tool steps, resumable sessions.
  "Polish with Claude" on an entry sends the request there and shows a before/after diff with undo.
- **Safe co-editing**: Claude's edits refresh the form and preview; editing the same item from
  two places raises a conflict instead of overwriting; every save keeps a copy in `.studio/history`.
- **Export** PDF + `.tex` to `settings.export_dir`.
- **Two workspaces, switched in the top bar (Ctrl+1 / Ctrl+2)**:
  - **Resume**: the library, the subsets and the preview, plus an **Applications** tab: paste a posting and
    [career-ops](https://github.com/santifer/career-ops) (MIT, by santifer) evaluates it against your resume (a
    fit score, what to stress, what's missing). "Resume to send" ranks your subsets by how much of the
    posting each one already covers and links the one you pick: a resume is for a kind of role, never named
    after a company. Only when none fits does Claude adjust one or start a new one.
  - career-ops' targeting profile (roles, location, salary, deal-breakers) is filled by Claude from your
    profile's fact layer and your subsets, and the window offers an update when they change.
  - **Job search**: career-ops' own web interface fills the window: its pipeline table, discovery, scheduled
    scans, follow-ups, portals, analytics and config, exactly as career-ops ships them. Resume Studio adds what
    only it can: Claude's chat on the left (those chats run in career-ops' folder), "All of career-ops" (every
    mode it has, found in its own files, each started as a chat), and "Pick a resume" for the posting open in
    the frame.
  - You don't need a terminal: Resume Studio installs career-ops into `.local/providers/` on request, builds its
    web interface there the first time (about a minute; again only after an update), runs it on this computer
    only, and stops it when the window closes. It reads only the files career-ops documents as canonical and
    never changes career-ops' code. Updates go through career-ops' own updater; afterwards a check lists
    anything that changed in what Resume Studio reads. The connector sits behind a small contract
    (`jobs/base.py`), and hosting a tool's web interface is generic (`core/webhost.py`), so another tool can
    replace career-ops later without changing the window.
- **Autofill (Ctrl+3)**: a local copy of each online profile (LinkedIn and BOSS直聘 come with notes for
  Claude; add any other site by its address). Edit the copy here or the profile on the site: "Check for
  updates" brings the site's changes in, your edits wait as git-style diffs until you push them, and a
  field changed on both sides asks which to keep. The first time, Claude reads the site and records a
  read route; after that checking takes seconds and needs no Claude, and when a site redesign breaks the
  route, Claude records it anew. There is no browser extension: Resume Studio opens its own Chrome or
  Edge with a profile of its own in `.local/browser/`, where you sign in once by hand. Pushing is
  Claude's, one ticked change at a time, each shown in the chat as a readable step; Claude never types a
  password. The resume is a reference: where it words something differently, the copy offers it.
- **Your languages**: the window speaks your computer's language (English, 简体中文, 日本語, 한국어,
  Español, Français, Deutsch; pick another under the gear in the top bar). A resume can be written in any
  number of languages (Latin, Greek, Cyrillic, Chinese, Japanese, Korean), each with its own text,
  section titles, date format and fonts; a new file starts in your computer's language.

Data format and Claude's editing rules: [SKILL.md](SKILL.md). How changes are verified: [TESTING.md](TESTING.md).
