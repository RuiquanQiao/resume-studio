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
All three are git-ignored, so pulling updates never touches them.

Needs Python 3.10+, `xelatex` (TeX Live / MiKTeX) and a logged-in Claude Code for the chat.
Everything is installed into this folder (`.venv`, `.cache`); nothing global.
The exe is a 16 KB launcher built from `launcher/Launcher.cs` by `scripts/build_launcher.py`
with the C# compiler that ships with Windows.

## What it does

- **Experience library** with a fact layer (`notes`, `evidence`) that templates never render —
  it exists so Claude knows the real story and never overclaims.
- **ALL and job targets**: `ALL` shows the full set. Each job target (AI agent engineer,
  HPC, management trainee…) is a subset: tick which entries go in, reorder, give it its own
  headline, hide contacts, pick template (Classic / Modern / Timeline), language, and every
  layout knob (font size, spacing, margins, colour, paper, font).
- **Live preview** as page images; in a job target a second page is flagged as overflow.
- **Chat with Claude Code** in the left sidebar: streamed replies, tool steps, resumable sessions.
  "Polish with Claude" on an entry sends the request there and shows a before/after diff with undo.
- **Safe co-editing**: Claude's edits refresh the form and preview; editing the same item from
  two places raises a conflict instead of overwriting; every save keeps a copy in `.studio/history`.
- **Export** PDF + `.tex` to `settings.export_dir`.

Data format and Claude's editing rules: [SKILL.md](SKILL.md). How changes are verified: [TESTING.md](TESTING.md).
