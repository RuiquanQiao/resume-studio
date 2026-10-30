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
| API + chat | `tests/test_api_chat.py` | every route; chat is a resumed Claude Code session (`--resume`), no `--bare`/`--system-prompt`, runs in the data folder, user text passed untouched; **one chat**: a chat started anywhere gets the tracker's and the browser's commands and the line saying what is on screen; Claude Code's own model / effort / command lists (`initialize`); per-chat model, effort and permission mode → `--model` / `--effort` / `--permission-mode`, unsafe values refused; a new chat's model / effort / mode kept in the app's prefs (`chat_defaults`), never in Claude Code's settings; permission cards (allow, always-allow changes the mode, deny with a reason), AskUserQuestion answers, plan approve / revise; messages typed while busy queue into the same process, a real `interrupt` on stop; images inline and files by path; fork at a message (`--fork-session --resume-session-at`); task list, context use, plan limits, `/context`; translate / fit / batch actions; errors |
| Other agents | `tests/test_agents.py` | every agent listed with whether it is installed and signed in (from the tool's own files) and how to install / sign in; a chat picks its agent and keeps it once it has begun (an empty one can switch, dropping the other agent's model and mode), new chats start on the last pick; **Codex** (`codex app-server`): models / efforts / account / usage windows from `model/list` and `account/*`, `thread/start` with the folder, the window's note as `developerInstructions`, model, effort, approval policy and sandbox per mode, the next message `thread/resume`s the thread, an approval card (allow for this chat → `acceptForSession`; deny with a reason → `decline` plus `turn/steer`), a question card, stop → `turn/interrupt`, a failed turn, the plan as the task list, fork at a message → `thread/fork` with `lastTurnId`; **ACP** (Qwen Code `--acp`, DeepSeek Harness `--profile acp`): `session/new` then `session/load` (the replayed history not shown again), the note in the prompt, modes / models / commands learned from the session, `session/set_model` / `set_mode`, a permission card with the agent's own options, stop → `session/cancel`, errors, no fork; **Pi** (`--mode rpc`): `--session-id` then `--session`, model and thinking level, the note via `--append-system-prompt`, tool steps, context use, errors, its model list |
| Applications | `tests/test_jobs.py` | career-ops connector against `tests/fixtures/career-ops` (its documented formats + fake scripts): tracker columns by alias, report `## Machine Summary`, reports without one, the data-folder marker / env, the checks name exactly what changed; `cv.md` / `article-digest.md` from resume.yaml (career-ops' headings, no fact layer in the CV, the profile's notes never in the proof points), never written over edits made in career-ops (conflict, diff, force), language / notes / off; API: not connected, list + detail, status through `set-status.mjs`, evaluate / actions / setup run in the career-ops folder (cwd, `--add-dir` data folder, `Bash(node *)`, npm cache env), the subsets ranked for a posting (requirements by importance + keywords), one posting linked to one subset and one subset to many postings, the older single `posting` still read and migrated, "which resume" and "new / adjusted subset" chats (named after the role, never the company), setup fills career-ops' profile from `profile.notes` and the subsets and turns stale when they change, a CV conflict resolved in a chat that doesn't overwrite, settings, update check, fork keeps the folder, a disconnected tool says so |
| Sent postings | `tests/test_signups.py` | rows go into career-ops only through its own path (reserve numbers, a headed TSV per row, `merge-tracker.mjs`; tabs can't split a cell; reservations released; all merged), a row career-ops folds into another (same URL) is reported, never guessed; an ordinary posting sent with its subset (Applied, pinned); after going back to v1, a send goes out as v1; what was sent follows a renamed subset |
| Job-hunt tracker | `tests/test_hunt.py` | companies by name, alias or id; roles in the 志愿 order asked, reordered on delete, ids never reused, a unique part of a role's name is enough and two matches ask which; statuses from the user's words (一面, 笔试, 挂, OA); the ledger: every change a line, a dated note keeps its date, tabs never split a cell, lines survive, the same status twice adds nothing; bad dates and channels refused; the old sign-ups move in once (order, versions sent, career-ops rows); Claude's command (`scripts/hunt.py`): add, status, next, list, show, about with sources, errors; a role's JD (`jd --stdin`) kept word for word apart from its notes, shown by `show`, cleared from the window; a role goes out with the subset and version picked (an older one makes no new version; one that doesn't exist is refused); a company's sign-up goes out with a kept version (refused without a subset or with nothing new), a role added later goes out alone as the same v1; with career-ops as the tracker the sent roles get rows there, with Resume Studio's they don't; edits and deletes over HTTP |
| The tracker finds things | `tests/test_hunt_agent.py` | a company fills itself (every field at once, sources kept only for links, the official name becomes an alias, the campus site leads, fields the user edited are left and named, handed back on request); logos kept in the data folder (the user's stays, only pictures); a company deadline moves to its roles once; `hunt.py fill` with a downloaded logo, served to the window; open postings matched across scans by link (new, closed, changed deadline; a partial read closes nothing), keywords fold away the rest, "Want to apply" makes an application once, a role added by hand is linked (by its link or its name); **a role follows its posting**: gone from the site, one at Want to apply becomes Posting closed, one further on gets a timeline line, back on the site it is Want to apply again, a withdrawn one stays; a site read by its recorded route (needs Claude before one exists, signed out and empty reads never close anything); **career-ops' providers through `jobs/co_scan.mjs`** (a stand-in registry and provider; a site without one still needs Claude); the window and Claude scan, record a route, apply, set keywords, read postings themselves; the daily scan only when on and stale; Claude is told to fill, scan and read links |
| Hosted web UI | `tests/test_webhost.py` | `core/webhost.py` against `tests/fake_web.py` (which guards like career-ops' web app): the window's frame is let in and handed on as same-origin, the app's own requests pass untouched, other sites and other local ports get 403, the bridge script lands right after `<head>`, prepare / exit failures become an error with the log, restart keeps the address, **the server dies with Resume Studio even when it is killed** (Windows job object); career-ops' `web()` installs and builds only when its lockfile or source changed, runs its own `server.mjs` with the data root, npm's cache in this folder; every mode from `modes/README.md` plus mode files without a row (and `interview/*`), each started as a chat in its folder |
| Autofill | `tests/test_autofill.py` | **a real browser over CDP** (`core/browser.py`, headless Edge, against `tests/fake_sites`): the whole page is read, below the fold too; clicks and typing arrive as trusted input that a React-style page accepts; dialogs, selects, checkboxes, screenshots; the profile lives in the app's own folder; uploads only from the data folder. What a subset suggests for LinkedIn / BOSS直聘 (sections, plain bullets, dates, no fact layer, degree-first resumes, fields a site lacks). **The sync**: a read brings site-only changes in, keeps the user's own, makes a conflict of a field changed on both sides (kept until settled), removals and additions on either side, a read that covers only some sections, the same school worded differently; pending changes, push reports moving the site's side on, undo. **The read route**: Claude (fake) opens the page, tests a script with `extract`, saves the route, `sync`s; then "Check for updates" reads a change made on the site by the route with no Claude call; the push gets only the ticked change and the route checks after it; a renamed heading breaks the route and the window offers a chat to record it anew. The user's own site list (add a preset or any site by its address, rename, notes for Claude, reorder, remove and re-add with the copy back, a site's first read saying what it has); over-the-limit blocks pushing; signed-out by address. **Sign-in mode**: the same profile reopened with no debugging port, so a page sees `navigator.webdriver` false (true when driven); commands are refused meanwhile; switching back drives it again at the page signed in to; the user closing that window turns the browser off. **Closing itself**: a browser a scan, a check or Claude started closes once nothing holds it and no chat is replying (the wait counts from the end of the work), says `closing` first so the window can keep it open; one the user opened or kept stays |
| Layout | `tests/e2e/test_layout.py` | every state in its catalogue (Resume, the chat, Job search, Applying: notices, banners, conflicts, warnings, empty lists, long names, folded and hidden panes, pages, dialogs) at 1280 / 1440 / 1920 wide in Chinese, English and German, looked at by `layout_check.js`: squeezed text, compact labels on two lines, content cut off or sticking out, sideways scrolling, overlapping controls; and every conditional piece of UI in `web/*.js` is in the catalogue (see **Layout checks** below) |
| UI (Edge) | `tests/e2e/test_ui.py` | load, autosave, external edits, conflicts, **the document** (Profile and every entry as cards: typing in one card then another saves both, the card typed in is the one being edited, an entry left out of the subset folds to a line and opens as a card, the fact layer folded under each card, the outline folds into a strip that opens on hover and pins again, the preview hides and comes back), include/reorder, layout, templates, languages, entries, contacts, sections, subsets, **subset versions** (keep v1 with a note, an edit elsewhere shows as changed, the git-style diff since v1, keep v2 from the page, preview v1 as it was and back to now, go back to v1), chat streaming + markdown + tools, polish diff + undo, export, overflow, pane resizing; **no native `<select>`, colour input or `title` tooltip anywhere**, custom dropdown by mouse and keyboard, right-click menu, colour picker, line chips + issues strip, writing / hiding the other language, project folder pick → sync → new commit → update; chat composer: model / effort / mode chips (number keys, Shift+Tab, custom model id, a new chat keeps the last choice), permission / question / plan cards, queue while busy, stop, task list, fork, `/` commands, pasted and picked attachments, context ring + plan limits; career-ops: no Applications tab in Resume, the connect card in Job search, a posting's page opened from Job search's resume picker (the chat names the posting), status through career-ops, "Resume to send" → use a subset → new subset for this kind of role in the preview, "Evaluate a posting" in Job search's top bar → a chat in the career-ops folder, CV conflict banner above career-ops' interface → use my library, Settings checks + choices saved on Done; Job search: the tool's interface fills the window, the frame reports its page (navigation and pushState) → "Pick a resume" for that posting (ranked subsets, linked, named in the top bar) → its preview in Resume, Ctrl+1 / Ctrl+2 keep the frame's page, the launcher lists every mode (search, Enter) and starts it as a chat in the tool's folder, **one chat across the workspaces** (the open chat stays open in Job search and Applying and is told what is on screen; a new chat in Job search runs in career-ops' folder and is listed apart; the chat pane hides and shows in any workspace); Applying: a company with its site → its subset → two roles, reordered → mark as sent (v1) → both Applied with v1 in the progress table, in the company's order → one application: status to interviewing, a note dated back, the timeline → what went out, previewed as it was → its JD typed into its own box and saved → the subset's v1 lists the roles it went to, and one opens that application → a role's Resume button opens its subset; Platforms tab → a platform's copy (the Autofill tests below go through it); **fails on any console error or 404/500** |

Notes
- Claude is replaced by `tests/fake_claude.py`, which speaks Claude Code's `stream-json` both ways
  (control requests, permission prompts, interrupts, several messages per process); Codex, Qwen Code, Pi
  and DeepSeek Harness by `tests/fake_agents.py` (app-server JSON-RPC, ACP, Pi's RPC), never the real ones
  even when installed (`conftest.py` points `STUDIO_<AGENT>_CMD` at it). Tests cost no quota and are deterministic.
- E2E drives the installed Microsoft Edge (`channel="msedge"`); no browser download.
- The suite pins the interface to Chinese (`RESUME_STUDIO_LANG=zh-CN` in `conftest.py`) so it runs the
  same on every computer; `test_i18n.py` covers the other languages.
- After adding or changing interface text, run `python scripts/i18n_keys.py`: it lists keys that a
  catalog lacks, stale ones, and translations whose `{placeholders}` don't match.
- Everything temporary stays in the repo: `.cache/` (pytest temp, Edge profile, TMP) and
  `tests/artifacts/` (screenshots). Both are git-ignored.
- Runs side by side each pass `--basetemp=.cache/pytest/<name>` — never a new folder straight under
  `.cache/`. A run empties its own basetemp and TMP (`<basetemp>.tmp`) at the start, and removes what
  other runs left in `.cache/pytest/` more than a day ago. One-off scripts and copies made while
  checking something go in the session's scratchpad, not in `.cache/`; delete them when done.

## Layout checks (how a squeezed notice can't come back)

Added 2026-10-17, after the user found "6 projects have new progress" squeezed into a column one word
wide in the outline rail. The cause: the notice was a row (icon, text, button) built for the old 320px
Library; in the 212px rail the button kept its width and the text was given none. The screenshots had not
shown it because the review copy never had a project with new progress, so the notice never appeared.
Both halves of that are now covered by tests, not by memory:

1. **A checker that looks at the screen** (`tests/e2e/layout_check.js`). It flags:
   - text pushed into a column a few letters wide (three or more lines in a box under 6em, including a box squeezed to 0px);
   - a compact button, tab or chip whose label broke onto two lines (a label meant to wrap carries `data-wrap`);
   - content cut off by its box without an ellipsis;
   - an element sticking out of the box it belongs to;
   - a pane, or the window, scrolling sideways;
   - text running past its own box over its neighbours (nothing clips it);
   - two controls on top of each other.
2. **A catalogue of states** (`STATES` in `tests/e2e/test_layout.py`). Each state brings onto the screen something
   that shows only in some situations (a notice, a banner, a conflict, a warning, an empty list, long names,
   a folded or hidden pane, a page, a dialog), names the selectors that prove it is there, and is checked
   at each window size and language in `LOOKS`.
3. **A guard on the catalogue** (`test_every_notice_is_in_the_catalogue`). It reads `web/*.js`, and any class
   that marks a conditional piece of UI (banner, warn, update, conflict, empty, issue, error, strip…)
   must be shown by some state, or listed in `NOT_SHOWN` with the reason it needs none.

Rules for every change to the window:

- **New conditional UI gets a state.** A new notice, banner, warning, empty state or badge is not done until
  a state in `STATES` shows it. The guard fails otherwise, so this can't be forgotten.
- **Run the layout checks after any layout or CSS change**, and after moving a piece into a different
  container (a pane, the rail, a card, a popover): `.venv/Scripts/python -m pytest tests/e2e/test_layout.py`
  (about 2½ minutes). Each finding names the element and saves a screenshot to `tests/artifacts/layout/`
  (the list is in `findings.txt` there). Look at the screenshot before fixing.
- **A row of text and buttons wraps; it never squeezes.** Notice text uses `.nb-text` (it keeps at least
  200px, or 120px in the rail, and pushes the buttons below it). Never give text `flex: 1` with no basis next to
  a button that can't shrink.
- **A piece that can live in a narrow box adapts to the box, not the window.** Use wrapping, `minmax()`,
  `min()` or a container query (as the layout panel does) rather than a fixed column width.
- **Never hide the important part to save room.** When something must be shortened, shorten the
  repeated or secondary part (a role's page wraps its heading rather than cutting the role).
- **Skipping is the last resort.** `SKIP` in `test_layout.py` lists places the checker can't judge (the
  preview's page images, career-ops' own interface), each with its reason. Fix the layout first; only a
  piece that breaks a rule on purpose goes there.
- **A box that scrolls sideways on purpose says so with `data-scrolls`** (a board's columns, a table whose
  columns the user sized or that has more columns than room; added 2026-10-19 with the database views). The
  checker then doesn't call its scrolling, or what runs past it, a fault. A table at its defaults must fit
  its pane: `web/dbview.js` fits the columns to the pane, none below its type's minimum, and only sets
  `data-scrolls` when even the minimums don't fit.
- **A panel's screenshot waits for it to appear.** Floating panels fade in; a review script waits for
  `.float.in` and a moment more, or the screenshot shows a half-transparent panel.
- **The review copy needs these states too.** For a hands-on review on `.cache/review/`, bring up the same
  states (a project with new progress, a conflict, a warning) rather than reviewing only what the data
  happens to show.

## 2. Visual review

`tests/e2e` writes screenshots to `tests/artifacts/`:
editor (a subset), ALL, layout panel, empty chat and chat, each at 1280×800 and 1920×1080, light and dark;
plus `new-*`: entry with line chips and a project folder, the Claude menu, Chinese editing with a
missing bullet, the subset dropdown, the issues menu, the colour picker, the model and mode pickers, a permission card,
a question card, a plan card, the `/` menu with the task list and queue, and the context panel (light and dark).
Open them and check:

- [ ] nothing overlaps, wraps into a column of single characters, or is cut off
- [ ] the preview page fits its panel; its toolbar wraps onto a second line rather than sticking out
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
      window; closing it (`CloseMainWindow`) frees the port and removes `resume-studio-data/.cache/server.json`
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
- [ ] the document (docs/LAYOUT.md) on the real data: type in one card, click straight into another and type →
      both are saved; the outline marks the card in view while scrolling; 收起大纲 → the strip opens it on hover,
      固定 pins it again; 预览 hides the preview and the document widens; the chat stays the same chat in 简历 /
      求职 / 网申, and its line above the composer names what is open there
- [ ] stop the server while the window is open → after a moment a red strip says the connection is lost;
      start it again → the strip disappears
- [ ] add Deutsch as a resume language (the "+" after the language buttons above the preview) → the section titles appear in German, DE shows above the preview,
      the German preview renders with German dates; remove it again → its text stays in resume.yaml
- [ ] career-ops on a trial copy (set `CAREER_OPS_DATA_DIR` to a scratch folder so a real career-ops keeps its
      own data): in 求职, 安装 career-ops clones into `.local/providers/career-ops` and nothing lands in the user profile
      (`npm_config_cache` and `PLAYWRIGHT_BROWSERS_PATH` stay in this folder); 让 Claude 来设置 runs career-ops'
      onboarding with question cards; 评估一个岗位 → paste a real posting → a report and a tracker row appear in its
      pipeline by themselves; on that posting, 选一份简历 → 在简历里打开它的页面 → 要投的简历 → 用这份 links a subset, 为这类职位新建子集 → a subset named after the role with picked entries and a headline in the preview (no company name anywhere); 检查更新 → career-ops'
      own updater answers
- [ ] 求职 (Ctrl+2) on the same trial: the first start installs and builds `web/` inside the career-ops folder
      (steps shown, about a minute), later starts take seconds; career-ops' Pipeline table, Explore, Follow-ups,
      Analytics and Config all work in the frame and follow the dark theme; open a posting → 选一份简历 in the
      top bar ranks your subsets; 从我的简历填写 fills career-ops' profile from the fact layer (check config/profile.yml); 「career-ops 全部功能」 → a mode → a chat on the left in the career-ops folder; close the window →
      no `node` from `career-ops\web` is left running (Task Manager)
- [ ] 网申 (Ctrl+3) on a trial copy: 公司 → 添加公司 → its page (roles, 官网, subset, 标记为已提交) → 投递进度 shows the roles; tell Claude
      on the left "Xinghe 一面过了，下周二二面" → the status and a note change in the table without a reload; 让 Claude 查一查 →
      关于 filled with sources. 招聘平台 → LinkedIn: 打开 LinkedIn starts Chrome (or Edge) with its own profile in
      `.local/browser/` (no automation banner; your everyday Chrome untouched); sign in by hand; close and reopen
      Resume Studio → still signed in. A site that refuses (Xinghe's 发送验证码 never arrives): ⋯ → 不带远程控制地在这里登录 (or a
      sign-up's ⋯ → 在 Resume Studio 的浏览器里登录) → a plain window, the bar shows in every workspace; sign in;
      我已登录，切回来 → the controlled browser is back on that page, still signed in. 读取 LinkedIn (real Claude) reads the full lists (Show all), records a
      route (scripts by headings and labels, not class names) and syncs: the copy matches the profile. Edit a
      position on linkedin.com, then 检查更新 → it comes in within seconds and the chat list gets no new chat.
      Edit the headline here and the same headline on the site → a conflict; settle it. Edit a description here
      → 待推送 shows it as a diff; 在 LinkedIn 上做这些修改 → Claude changes only that, "share with network" stays
      off, and the check after it finds nothing left. Same for BOSS直聘 in Chinese (Claude finds 在线简历 itself).
      "+" → add a site of your own (e.g. 猎聘 or SEEK) with a note for Claude. Close the window → the browser closes too
