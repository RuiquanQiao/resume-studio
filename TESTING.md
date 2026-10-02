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
| Rendering | `tests/test_render.py` | LaTeX escaping, every template × both languages is one page, hostile characters compile, overflow is detected, export |
| API + chat | `tests/test_api_chat.py` | every route; chat is a resumed Claude Code session (`--resume`), no `--bare`/`--system-prompt`, runs in the data folder, user text passed untouched; errors, cancel, busy |
| UI (Edge) | `tests/e2e/test_ui.py` | load, autosave, external edits, conflicts, include/reorder, layout, templates, languages, entries, contacts, sections, versions, chat streaming + markdown + tools, polish diff + undo, export, overflow, pane resizing; **fails on any console error or 404/500** |

Notes
- Claude is replaced by `tests/fake_claude.py`, which speaks Claude Code's `stream-json`.
  Tests cost no quota and are deterministic.
- E2E drives the installed Microsoft Edge (`channel="msedge"`); no browser download.
- Everything temporary stays in the repo: `.cache/` (pytest temp, Edge profile, TMP) and
  `tests/artifacts/` (screenshots). Both are git-ignored.

## 2. Visual review

`tests/e2e` writes screenshots to `tests/artifacts/`:
editor, layout panel, empty chat and chat, each at 1280×800 and 1920×1080, light and dark.
Open them and check:

- [ ] nothing overlaps, wraps into a column of single characters, or is cut off
- [ ] the preview page fits its panel; the toolbar stays on one line
- [ ] inputs, textareas and buttons share one style in both themes
- [ ] empty states read well (no blank selects, no "undefined")

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
