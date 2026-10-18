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

Needs Python 3.10+, `xelatex` (TeX Live / MiKTeX) and a logged-in Claude Code for the chat; Job search
(career-ops) also needs git and Node.js 22+.
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
- **Versions of a subset**, like commits: keep one (with a note) whenever it goes out, and later see
  exactly what was sent. Each version freezes the subset and the wording of the shared entries it picks;
  compare any two (git-style word diff), preview or export one as it was, or go back to it. Going back
  keeps what it said before as a version too, and says which other subsets the shared entries also change.
- **The library is a document**: Profile and every entry, each a card edited in place, in the subset's order
  (entries the subset leaves out fold to one line). An **outline** beside it ticks, orders and adds entries
  and jumps to any card; it can fold into a strip above the document that opens on hover. The fact layer
  folds under each card. The preview can be hidden for room. ([docs/LAYOUT.md](docs/LAYOUT.md))
- **Live preview** as page images; in a subset a second page is flagged as overflow.
- **One chat with Claude Code** on the left, the same in every workspace (hide it with the Claude button, Ctrl+J):
  streamed replies, tool steps, resumable sessions. Claude is told what is on screen (the entry being edited,
  the company or platform open), and can edit the resume, update the tracker and drive the browser from any
  of them. "Polish with Claude" on an entry sends the request there and shows a before/after diff with undo.
- **Safe co-editing**: Claude's edits refresh the form and preview; editing the same item from
  two places raises a conflict instead of overwriting; every save keeps a copy in `.studio/history`.
- **Export** PDF + `.tex` to `settings.export_dir`.
- **Job search (Ctrl+2)**, with [career-ops](https://github.com/santifer/career-ops) (MIT, by santifer):
  - "Evaluate a posting" in its top bar: career-ops evaluates it against your resume (a fit score, what to
    stress, what's missing). A posting's page in Resume (from "Pick a resume" → "Open its page in Resume")
    shows the verdict next to the preview, and "Resume to send" ranks your subsets by how much of the
    posting each one already covers and links the one you pick: a resume is for a kind of role, never named
    after a company. Only when none fits does Claude adjust one or start a new one.
  - Mark a posting sent when you submit: its subset is kept as a version and pinned on it, so you always know
    what it got.
  - career-ops' targeting profile (roles, location, salary, deal-breakers) is filled by Claude from your
    profile's fact layer and your subsets, and the window offers an update when they change.
  - career-ops' own web interface fills the window: its pipeline table, discovery, scheduled
    scans, follow-ups, portals, analytics and config, exactly as career-ops ships them. Resume Studio adds what
    only it can: the chat on the left (a new chat there runs in career-ops' folder), "All of career-ops" (every
    mode it has, found in its own files, each started as a chat), and "Pick a resume" for the posting open in
    the frame.
  - You don't need a terminal: Resume Studio installs career-ops into `.local/providers/` on request, builds its
    web interface there the first time (about a minute; again only after an update), runs it on this computer
    only, and stops it when the window closes. It reads only the files career-ops documents as canonical and
    never changes career-ops' code. Updates go through career-ops' own updater; afterwards a check lists
    anything that changed in what Resume Studio reads. The connector sits behind a small contract
    (`jobs/base.py`), and hosting a tool's web interface is generic (`core/webhost.py`), so another tool can
    replace career-ops later without changing the window.
- **Applying (Ctrl+3)**: Resume Studio's own job-hunt tracker, in three tables:
  - **Progress**: every application (company and role), how it goes in (the company's own site, a platform, a
    referral), its status, the subset version it got, the latest event and the next step. Each has a timeline.
  - **Companies**: logo, industry, strength and open roles. Add one by its name or paste a posting's link:
    Claude fills in every field (with a source for each), finds its careers sites and logo. **Scan all** reads
    every company's sites for open roles (career-ops' providers for Moka, Feishu, Tencent, Greenhouse, Lever…;
    a route Claude records once for any other site) and says what is new today. On a company's page: its open
    roles ("Want to apply" adds one to your applications), the roles there in your order of preference
    (网申 / 志愿), its About and your notes. Mark the sign-up sent when you submit: the subset is kept as a
    version and pinned on every role.
  - **Platforms**: your online profiles (below).
  - Tell Claude in the chat on the left what happened ("Xinghe 一面过了") and it updates the tables through
    `scripts/hunt.py`. Settings → Applications tracker can also copy sent roles into career-ops' tracker.
    The design and where it is going: [docs/JOB_HUNT.md](docs/JOB_HUNT.md).
- **Platforms**: a local copy of each online profile (LinkedIn and BOSS直聘 come with notes for
  Claude; add any other site by its address). Edit the copy here or the profile on the site: "Check for
  updates" brings the site's changes in, your edits wait as git-style diffs until you push them, and a
  field changed on both sides asks which to keep. The first time, Claude reads the site and records a
  read route; after that checking takes seconds and needs no Claude, and when a site redesign breaks the
  route, Claude records it anew. There is no browser extension: Resume Studio opens its own Chrome or
  Edge with a profile of its own in `.local/browser/`, where you sign in once by hand. Pushing is
  Claude's, one ticked change at a time, each shown in the chat as a readable step; Claude never types a
  password. A site that refuses to sign in a driven browser (a verification code that never comes) gets a
  sign-in mode: the same browser profile, opened without remote control, until you switch back. The resume is a reference: where it words something differently, the copy offers it.
- **Your languages**: the window speaks your computer's language (English, 简体中文, 日本語, 한국어,
  Español, Français, Deutsch; pick another under the gear in the top bar). A resume can be written in any
  number of languages (Latin, Greek, Cyrillic, Chinese, Japanese, Korean), each with its own text,
  section titles, date format and fonts; a new file starts in your computer's language.

Data format and Claude's editing rules: [SKILL.md](SKILL.md). How changes are verified: [TESTING.md](TESTING.md).
Where the job-hunt side is going (applications, companies and platforms, a tracker of our own): [docs/JOB_HUNT.md](docs/JOB_HUNT.md).
