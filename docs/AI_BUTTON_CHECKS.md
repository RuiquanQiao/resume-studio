# AI buttons: does the program check what the AI did?

Audit, 2026-11-03. A button that asks the AI to do something sends one sentence to the chat. The procedure
behind it does not live in that sentence: every chat, whatever the agent (Claude Code, Codex, ACP agents), gets
the window's tools note as its system / developer instructions (`core/chat.py` → `core/drivers.py`), which is
`jobhunt/prompts.py` + `autofill/prompts.py`. So the open question per button is the last layer: once the
chat ends, does the program look at the data and say whether the job was done, or does it only say "done"?

Today every button ends the same way: `aiRunDone` (`web/chat.js`) stops the spinner and, when the chat is out
of sight, toasts "{agent} is done; see the chat". It never looks at the result.

| Button (where) | What it asks | Procedure in the note | Check after the chat ends |
|---|---|---|---|
| 让 AI 学会读它的官网 (company page, Open roles) | find open roles, record a scan route | yes, detailed ("done only once `scan-route` has run") | partial: the card keeps saying "no scan route yet", but the toast still says done |
| 让 AI 学会读这些官网 (companies table) | same, several companies | same | partial: the counter over the table stays; toast says done |
| Paste a posting link (Hunt) | read the posting, add company + role | yes ("add-company … add-role") | **none**: nothing checks a role was added |
| 让 AI 重新填写 / Fill in (company) | research and fill every field with sources | yes | **none**: no list of fields still empty or unsourced |
| Ask AI to write it in {lang} (company) | rewrite the description in its language | yes | yes: the language bar stays while it is still in the wrong language |
| Ask AI to read it (role, after its page failed) | read the JD, location, apply-by | yes | **wrong**: the "Its page didn't read" bar stays even after the AI filled the JD (the error lives in memory in `jobhunt/api.py` `reading`, cleared only by a later successful read by the program) |
| 让 AI 挑项目 (resume, no entries yet) | suggest projects, change nothing | n/a | not needed: advice only |
| Entry / subset actions (resume) | polish, translate, tailor… | fixed server prompts (`action`) | yes: snapshot before, diff + undo after (`finishAi`) |
| career-ops actions (Applying) | career-ops modes | career-ops' own | career-ops' own; out of scope (career-ops is being removed) |

## Fix (done 2026-11-03)

`aiRun(key, start, check)` takes the check; `aiCheck` (`web/chat.js`) runs it when the chat ends. The hunt
buttons' checks are in `web/hunt.js` (`askLearn`, `addFromLink`, `askFill`, `askRewrite`, `askRead`). Learning a
site is checked per address, never per company: what a company "needs" is counted over its current sites only
(`Scanner.live_state`), and each site's row says whether a scan reads it by itself (`Scanner.site_status`).

1. **One shared "did it work" step.** Each button names what it expects (route recorded for these companies;
   a role with this link exists; these fields filled with a source; JD filled). When the chat ends, `aiRunDone`
   reloads the data and checks it. Done: the toast says what was done ("路线已记录，读到 20 个岗位"). Not done:
   a warning toast and the page bar say what is missing, with a button to ask again in the same chat.
2. **Fix the stale read error**: a role whose JD got filled (by anyone) drops its read error.
3. Tests: each check gets a test that fails on today's code (chat ends, data unchanged → the window must not
   say done).
