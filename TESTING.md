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
| Data | `tests/test_store_model.py` | partial writes keep comments/style, conflicts, history, id renames, fact layer never reaches a view |
| Rendering | `tests/test_render.py` | LaTeX escaping, every template × both languages is one page, hostile characters compile, overflow is detected, export; **per-bullet line counts** (every template), `--lang` renders, `''` hides a bullet in one language, and measuring is **pixel-identical** to a plain `\item` |
| Evidence | `tests/test_evidence.py` | git and plain-folder change detection (commits, uncommitted edits, vendor dirs ignored, missing paths), git runs read-only (`.git/index` untouched), folder picker endpoint, "update from project" sends the commits and marks the folder synced after Claude succeeds, project roots reach Claude |
| API + chat | `tests/test_api_chat.py` | every route; chat is a resumed Claude Code session (`--resume`), no `--bare`/`--system-prompt`, runs in the data folder, user text passed untouched; Claude Code's own model / effort / command lists (`initialize`); per-chat model, effort and permission mode → `--model` / `--effort` / `--permission-mode`, unsafe values refused; permission cards (allow, always-allow changes the mode, deny with a reason), AskUserQuestion answers, plan approve / revise; messages typed while busy queue into the same process, a real `interrupt` on stop; images inline and files by path; fork at a message (`--fork-session --resume-session-at`); task list, context use, plan limits, `/context`; translate / fit / batch actions; errors |
| UI (Edge) | `tests/e2e/test_ui.py` | load, autosave, external edits, conflicts, include/reorder, layout, templates, languages, entries, contacts, sections, versions, chat streaming + markdown + tools, polish diff + undo, export, overflow, pane resizing; **no native `<select>`, colour input or `title` tooltip anywhere**, custom dropdown by mouse and keyboard, right-click menu, colour picker, line chips + issues strip, writing / hiding the other language, project folder pick → sync → new commit → update; chat composer: model / effort / mode chips (number keys, Shift+Tab, custom model id, a new chat keeps the last choice), permission / question / plan cards, queue while busy, stop, task list, fork, `/` commands, pasted and picked attachments, context ring + plan limits; **fails on any console error or 404/500** |

Notes
- Claude is replaced by `tests/fake_claude.py`, which speaks Claude Code's `stream-json` both ways
  (control requests, permission prompts, interrupts, several messages per process).
  Tests cost no quota and are deterministic.
- E2E drives the installed Microsoft Edge (`channel="msedge"`); no browser download.
- Everything temporary stays in the repo: `.cache/` (pytest temp, Edge profile, TMP) and
  `tests/artifacts/` (screenshots). Both are git-ignored.

## 2. Visual review

`tests/e2e` writes screenshots to `tests/artifacts/`:
editor (a job), ALL, layout panel, empty chat and chat, each at 1280×800 and 1920×1080, light and dark;
plus `new-*`: entry with line chips and a project folder, the Claude menu, Chinese editing with a
missing bullet, the job dropdown, the issues menu, the colour picker, the model and mode pickers, a permission card,
a question card, a plan card, the `/` menu with the task list and queue, and the context panel (light and dark).
Open them and check:

- [ ] nothing overlaps, wraps into a column of single characters, or is cut off
- [ ] the preview page fits its panel; the toolbar stays on one line
- [ ] inputs, textareas and buttons share one style in both themes
- [ ] empty states read well (no blank selects, no "undefined", no stray "0")
- [ ] every dropdown / menu / picker is the app's own (rounded panel, app font), opened fully, not mid-fade
- [ ] bullet text boxes use the full width (the tools float above them on hover)

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
