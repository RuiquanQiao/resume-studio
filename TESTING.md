# Testing

Every change is checked in three layers before it is handed over. Run all of them;
report which ran and what they caught.

## 1. Automated tests

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt   # once
.venv/Scripts/python -m pytest                                 # everything (~3 min)
.venv/Scripts/python -m pytest tests/e2e                       # only the UI
```

| Layer | File | Covers |
| --- | --- | --- |
| Data | `tests/test_store_model.py` | partial writes keep comments/style, conflicts, history, id renames, fact layer never reaches a view; a file from before schema 2 (`versions:`) is read as `subsets` and rewritten at startup in place, comments kept, numbering going on from its `JOB-` ids |
| Versions | `tests/test_versions.py` | a version freezes what a subset renders (shared entries included) and nothing private; its name, postings and the fact layer don't count as a change; an edit to a shared entry changes exactly the subsets that use it; compare per entry (and between two versions), render and export a version as it was (`<id>-v<n>.pdf`); going back keeps now as a version first (unless it is one already), says which other subsets change, keeps the name and the fact layer, brings deleted entries back; history follows a renamed subset, outlives a deleted one (which can come back), and its id is never reused; the HTTP routes |
| Rendering | `tests/test_render.py` | LaTeX escaping, every template × both languages is one page, hostile characters compile, overflow is detected, export; **per-bullet line counts** (every template), `--lang` renders, `''` hides a bullet in one language, and measuring is **pixel-identical** to a plain `\item` |
| Languages | `tests/test_i18n.py` | every interface catalog (`web/i18n/*.json`) has every key with its placeholders and plural forms; `t()` and plurals; computer locale → interface and resume language; a new file starts in the computer's language (sections, ALL, new subsets); dates per language; adding a resume language fills the default section titles; switching the app language (`/api/prefs`, `/i18n.js`, API errors); Japanese, Korean, Russian and Greek resumes compile with fitting fonts |
| Data file | `tests/test_data_file.py` | Settings → open another data file or start a new one: state, edits, chats and `/api/instance` all follow it, the old file is untouched, "new" never overwrites, cancel / same file / missing / unreadable files change nothing; the choice is remembered in the app's own prefs and the next launch opens it (`--data` still wins) |
| Evidence | `tests/test_evidence.py` | git and plain-folder change detection (commits, uncommitted edits, vendor dirs ignored, missing paths), git runs read-only (`.git/index` untouched), folder picker endpoint, "update from project" sends the commits and marks the folder synced after Claude succeeds, project roots reach Claude |
| API + chat | `tests/test_api_chat.py` | every route; chat is a resumed Claude Code session (`--resume`), no `--bare`/`--system-prompt`, runs in the data folder, user text passed untouched; Claude Code's own model / effort / command lists (`initialize`); per-chat model, effort and permission mode → `--model` / `--effort` / `--permission-mode`, unsafe values refused; permission cards (allow, always-allow changes the mode, deny with a reason), AskUserQuestion answers, plan approve / revise; messages typed while busy queue into the same process, a real `interrupt` on stop; images inline and files by path; fork at a message (`--fork-session --resume-session-at`); task list, context use, plan limits, `/context`; translate / fit / batch actions; errors |
| Applications | `tests/test_jobs.py` | career-ops connector against `tests/fixtures/career-ops` (its documented formats + fake scripts): tracker columns by alias, report `## Machine Summary`, reports without one, the data-folder marker / env, the checks name exactly what changed; `cv.md` / `article-digest.md` from resume.yaml (career-ops' headings, no fact layer in the CV, the profile's notes never in the proof points), never written over edits made in career-ops (conflict, diff, force), language / notes / off; API: not connected, list + detail, status through `set-status.mjs`, evaluate / actions / setup run in the career-ops folder (cwd, `--add-dir` data folder, `Bash(node *)`, npm cache env), the subsets ranked for a posting (requirements by importance + keywords), one posting linked to one subset and one subset to many postings, the older single `posting` still read and migrated, "which resume" and "new / adjusted subset" chats (named after the role, never the company), setup fills career-ops' profile from `profile.notes` and the subsets and turns stale when they change, a CV conflict resolved in a chat that doesn't overwrite, settings, update check, fork keeps the folder, a disconnected tool says so |
| Sign-ups | `tests/test_signups.py` | rows go into career-ops only through its own path (reserve numbers, a headed TSV per row, `merge-tracker.mjs`; tabs can't split a cell; reservations released; all merged), a row career-ops folds into another (same URL) is reported, never guessed; a sign-up keeps its roles in order (no repeats or blanks), nothing reaches the tracker before it is sent; sending keeps the subset as a version, adds the roles as Applied, pins the version on each and links the subset; a role added later is the only one added on the next send, with no second copy of the version; deleting a sign-up leaves the tracker alone; it needs a company, a subset and roles; an ordinary posting sent with its subset (Applied, pinned); after going back to v1, a send goes out as v1; what was sent follows a renamed subset |
| Hosted web UI | `tests/test_webhost.py` | `core/webhost.py` against `tests/fake_web.py` (which guards like career-ops' web app): the window's frame is let in and handed on as same-origin, the app's own requests pass untouched, other sites and other local ports get 403, the bridge script lands right after `<head>`, prepare / exit failures become an error with the log, restart keeps the address, **the server dies with Resume Studio even when it is killed** (Windows job object); career-ops' `web()` installs and builds only when its lockfile or source changed, runs its own `server.mjs` with the data root, npm's cache in this folder; every mode from `modes/README.md` plus mode files without a row (and `interview/*`), each started as a chat in its folder |
| Autofill | `tests/test_autofill.py` | **a real browser over CDP** (`core/browser.py`, headless Edge, against `tests/fake_sites`): the whole page is read, below the fold too; clicks and typing arrive as trusted input that a React-style page accepts; dialogs, selects, checkboxes, screenshots; the profile lives in the app's own folder; uploads only from the data folder. What a subset suggests for LinkedIn / BOSS直聘 (sections, plain bullets, dates, no fact layer, degree-first resumes, fields a site lacks). **The sync**: a read brings site-only changes in, keeps the user's own, makes a conflict of a field changed on both sides (kept until settled), removals and additions on either side, a read that covers only some sections, the same school worded differently; pending changes, push reports moving the site's side on, undo. **The read route**: Claude (fake) opens the page, tests a script with `extract`, saves the route, `sync`s; then "Check for updates" reads a change made on the site by the route with no Claude call; the push gets only the ticked change and the route checks after it; a renamed heading breaks the route and the window offers a chat to record it anew. The user's own site list (add a preset or any site by its address, rename, notes for Claude, reorder, remove and re-add with the copy back, a site's first read saying what it has); over-the-limit blocks pushing; signed-out by address |
| UI (Edge) | `tests/e2e/test_ui.py` | load, autosave, external edits, conflicts, include/reorder, layout, templates, languages, entries, contacts, sections, subsets, **subset versions** (keep v1 with a note, an edit elsewhere shows as changed, the git-style diff since v1, keep v2 from the page, preview v1 as it was and back to now, go back to v1), chat streaming + markdown + tools, polish diff + undo, export, overflow, pane resizing; **no native `<select>`, colour input or `title` tooltip anywhere**, custom dropdown by mouse and keyboard, right-click menu, colour picker, line chips + issues strip, writing / hiding the other language, project folder pick → sync → new commit → update; chat composer: model / effort / mode chips (number keys, Shift+Tab, custom model id, a new chat keeps the last choice), permission / question / plan cards, queue while busy, stop, task list, fork, `/` commands, pasted and picked attachments, context ring + plan limits; Applications: connect card, list + filter, a sign-up (网申: company, site, two roles reordered, listed but not in the tracker → mark as sent → both in career-ops' tracker as Applied with v1 pinned → what went out, previewed as it was), posting view, status through career-ops, "Resume to send" → use a subset → new subset for this kind of role in the preview, evaluate → a chat in the career-ops folder, CV conflict banner → use my library, Settings checks + choices saved on Done; Job search: the tool's interface fills the window, the frame reports its page (navigation and pushState) → "Pick a resume" for that posting (ranked subsets, linked, named in the top bar) → its preview in Resume, Ctrl+1 / Ctrl+2 keep the frame's page, the launcher lists every mode (search, Enter) and starts it as a chat in the tool's folder, each workspace keeps its own open chat; **fails on any console error or 404/500** |

Notes
- Claude is replaced by `tests/fake_claude.py`, which speaks Claude Code's `stream-json` both ways
  (control requests, permission prompts, interrupts, several messages per process).
  Tests cost no quota and are deterministic.
- E2E drives the installed Microsoft Edge (`channel="msedge"`); no browser download.
- The suite pins the interface to Chinese (`RESUME_STUDIO_LANG=zh-CN` in `conftest.py`) so it runs the
  same on every computer; `test_i18n.py` covers the other languages.
- After adding or changing interface text, run `python scripts/i18n_keys.py`: it lists keys that a
  catalog lacks, stale ones, and translations whose `{placeholders}` don't match.
- Everything temporary stays in the repo: `.cache/` (pytest temp, Edge profile, TMP) and
  `tests/artifacts/` (screenshots). Both are git-ignored.

## 2. Visual review

`tests/e2e` writes screenshots to `tests/artifacts/`:
editor (a subset), ALL, layout panel, empty chat and chat, each at 1280×800 and 1920×1080, light and dark;
plus `new-*`: entry with line chips and a project folder, the Claude menu, Chinese editing with a
missing bullet, the subset dropdown, the issues menu, the colour picker, the model and mode pickers, a permission card,
a question card, a plan card, the `/` menu with the task list and queue, and the context panel (light and dark).
Open them and check:

- [ ] nothing overlaps, wraps into a column of single characters, or is cut off
- [ ] the preview page fits its panel; the toolbar stays on one line
- [ ] inputs, textareas and buttons share one style in both themes
- [ ] empty states read well (no blank selects, no "undefined", no stray "0")
- [ ] every dropdown / menu / picker is the app's own (rounded panel, app font), opened fully, not mid-fade
- [ ] bullet text boxes use the full width (the tools float above them on hover)
- [ ] text boxes grow with their text: no scrollbar inside the page's own scrollbar
- [ ] every setting sits where one looks for it first; nothing hides behind a long button that jumps elsewhere

Then repeat on a copy of a real, long `resume.yaml` (the sample is short): copy it to `.cache/review/`,
start the `review` entry of `.claude/launch.json`, and screenshot every screen (ALL and a subset: profile,
sections, subset settings, versions (with a diff open and a version previewed), a project entry with bullets, menus, Settings, layout, chat) at 1280×800 and
1600×900, light and dark, and once with the interface in German (longest labels). Nothing may be cut
off, wrap a heading into two lines, squeeze an input to a sliver, or push a button out of the window.

## 3. Hands-on trial (real Claude Code)

Automation cannot judge answer quality or the native window. Do these by hand on a
copy of `examples/resume.sample.yaml` (never on real data):

- [ ] `python scripts/studio.py --data .cache/trial/resume.yaml --no-browser` starts from the
      system Python and re-runs itself inside `.venv`
- [ ] ask in Chinese about the open entry → the reply is in Chinese, natural, reads the
      data file and `SKILL.md` with tools, and respects "don't change anything yet"
- [ ] follow up ("按你说的改") → same session (it remembers), edits the file, form and
      preview refresh by themselves; `git diff --no-index` shows only the intended line
- [ ] launch `Resume Studio.exe` → a window titled "Resume Studio" with the app icon
      appears and renders `<skill>/resume.yaml`; launching again does not open a second
      window; closing it (`CloseMainWindow`) frees the port and removes `.studio/server.json`
- [ ] first run: copy the repo without `.venv` into `.cache/fresh/` and launch its exe →
      the setup note shows, `.venv` is built there, then the window opens
- [ ] launcher errors: an exe copied away from the repo explains where it belongs
- [ ] in the window, 「选择项目文件夹」 opens the modern Windows folder picker on top of the app, starting
      next to the last project; cancelling changes nothing
- [ ] right-click in a text field shows the app's menu; 粘贴 pastes what was copied in another program
- [ ] commit something in a linked project, switch back to the window → the entry gets the blue dot and
      「1 个新提交」 within a second, without clicking anything
- [ ] 「写中文版」 on a real entry (real Claude): the Chinese reads like a Chinese resume, not a translation,
      and every Chinese bullet shows 「1 行」 afterwards; nothing in `en` changed (`git diff --no-index`)
- [ ] dark mode: the title bar follows the system theme
- [ ] 「每次询问」 mode, ask Claude to write a file in the trial folder → the card shows the path and content;
      允许 writes it, 拒绝并说明 makes Claude answer the reason
- [ ] model chip: the list matches Claude Code's own menu; Haiku greys out effort; `/context` answers without
      using quota; the context ring and 5-hour / weekly limits fill in after one reply
- [ ] paste a screenshot (Ctrl+V) into the chat → Claude describes it
- [ ] the gear in the top bar (or Ctrl+,) → App language → each language: the whole window switches after the reload,
      nothing is cut off or overlaps (German and French run longest); "Follow the computer" goes back
- [ ] Settings → Data file → 「打开其他文件…」 picks another `resume.yaml` in the native dialog: the window
      switches to it (form, preview, chat list), and the next launch of the exe opens it; 「新建…」 starts an
      empty one and never overwrites an existing file; the original file is untouched
- [ ] stop the server while the window is open → after a moment a red strip says the connection is lost;
      start it again → the strip disappears
- [ ] add Deutsch as a resume language (the "+" after the language buttons above the preview) → the section titles appear in German, DE shows above the preview,
      the German preview renders with German dates; remove it again → its text stays in resume.yaml
- [ ] Applications on a trial copy (set `CAREER_OPS_DATA_DIR` to a scratch folder so a real career-ops keeps its
      own data): 安装 career-ops clones into `.local/providers/career-ops` and nothing lands in the user profile
      (`npm_config_cache` and `PLAYWRIGHT_BROWSERS_PATH` stay in this folder); 让 Claude 来设置 runs career-ops'
      onboarding with question cards; paste a real posting → 评估 → a report and a tracker row appear in the list
      by themselves; 要投的简历 → 用这份 links a subset, 为这类职位新建子集 → a subset named after the role with picked entries and a headline in the preview (no company name anywhere); 检查更新 → career-ops'
      own updater answers
- [ ] 求职 (Ctrl+2) on the same trial: the first start installs and builds `web/` inside the career-ops folder
      (steps shown, about a minute), later starts take seconds; career-ops' Pipeline table, Explore, Follow-ups,
      Analytics and Config all work in the frame and follow the dark theme; open a posting → 选一份简历 in the
      top bar ranks your subsets; 从我的简历填写 fills career-ops' profile from the fact layer (check config/profile.yml); 「career-ops 全部功能」 → a mode → a chat on the left in the career-ops folder; close the window →
      no `node` from `career-ops\web` is left running (Task Manager)
- [ ] 自动填写 (Ctrl+3) on a trial copy: 打开 LinkedIn starts Chrome (or Edge) with its own profile in
      `.local/browser/` (no automation banner; your everyday Chrome untouched); sign in by hand; close and reopen
      Resume Studio → still signed in. 读取 LinkedIn (real Claude) reads the full lists (Show all), records a
      route (scripts by headings and labels, not class names) and syncs: the copy matches the profile. Edit a
      position on linkedin.com, then 检查更新 → it comes in within seconds and the chat list gets no new chat.
      Edit the headline here and the same headline on the site → a conflict; settle it. Edit a description here
      → 待推送 shows it as a diff; 在 LinkedIn 上做这些修改 → Claude changes only that, "share with network" stays
      off, and the check after it finds nothing left. Same for BOSS直聘 in Chinese (Claude finds 在线简历 itself).
      "+" → add a site of your own (e.g. 猎聘 or SEEK) with a note for Claude. Close the window → the browser closes too
