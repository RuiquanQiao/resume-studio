# Applying, reviewed from a first-time user's chair (2026-10-19)

## The problem

The Applying workspace felt intimidating: it was hard to tell what a click would do, so it was easier
not to click at all.

How it was reviewed:
- A sample tracker went into `.cache/review-applying`: one company, one role, and
  the LinkedIn and BOSS直聘 platforms.
- Every tab, page, menu and dialog was opened at 1440 and 1280 wide, light and dark. The order a first-time
  user would follow was kept, and each step's buttons were logged.
- The code behind every button was read, to see what a click really does.
- Nothing was sent to Claude, no site was signed into, and the real data wasn't touched.

## The short answer: why it feels unsafe to click

The fear is reasonable. Four things cause it, roughly in this order:

1. **You can't tell what a click will set off.** Seven buttons start Claude: it researches on the web, may
   drive the browser, and spends usage. The button doesn't say so, and neither does the dialog in front of
   it. A message goes into **whichever chat is open on the left**, even one about something else.
2. **The page sometimes contradicts itself**, so you can't trust what it shows. Two of these were bugs
   (fixed below). Others come from data written by older versions (blank fields next to a full About).
3. **There is no path.** A company's page has nine buttons of equal weight across five cards and about
   fifteen ideas: subset, version, mark as sent, careers site, account, filled from, open roles, scan,
   recorded route, sign-in mode… Nothing says what to do first, or what is already done.
4. **Clicks change data, and nothing undoes them.** In a table, clicking any cell but the title edits it,
   as in Notion. A status set by mistake, a cleared date or an overwritten note has no undo. Only deletes
   ask first.

## Fixed in this pass (bugs, no design choice involved)

| What | Cause | Fix |
|---|---|---|
| **The window sometimes does nothing when clicked.** In 4 of 6 test loads, some of the page's scripts never arrived (`ERR_CONNECTION_REFUSED`), and every button on the page was dead. This is probably also what made the UI tests time out "at setup" now and then. | Python's `ThreadingHTTPServer` has a backlog of 5 waiting connections. The page asks for about a dozen scripts at once, and on Windows the connections past 5 are refused. | `request_queue_size = 128` in `core/server.py`, and in `core/webhost.py` for career-ops' frame. After the fix, 12 of 12 loads worked. |
| **A platform said two opposite things.** The Platforms table said LinkedIn was "Not read yet". Its page said "✓ Nothing new on LinkedIn" and "Your copy says what LinkedIn says". | The copy was read by an older version, which stored `read_at`. The table only looks at `synced_at`. The page then said "nothing new" about a check that never happened. | `autofill/service.py` falls back to `read_at`. With no check yet, the page says "Claude read LinkedIn", which is true. |
| **A native drop-down** in "Add an application" (the Company field). | It used a `<datalist>`, which breaks the rule against native pickers. | `formModal` (`web/hunt.js`) suggests the known companies in its own list as you type. Arrow keys and Enter work, Esc closes the list first and then the dialog. Checked in light and dark. |

## Seen, not changed: each needs your decision

Every item below adds information or control. None takes a feature or a freedom away.

### A. Say what a click sets off, before it happens (cause 1)

The buttons that start Claude:

| Where | Button | What it sends |
|---|---|---|
| Companies tab | **Add a company** → Add | "Find out about X and fill in all its fields…": silent, right after the dialog closes |
| Progress tab | **Add an application** (with a new company) | the same, silent |
| Progress, Companies | **Add from a link** | "Read this posting and add it" |
| Company page | **Ask Claude to fill it in (again)** | the research request |
| Companies tab, company page | **Let Claude learn them / its site**, **Ask Claude to record it again** | Claude opens the careers sites in Resume Studio's browser and records a scan route |
| Platform page | **Read LinkedIn**, **Check for updates** (the first time), **Record the route anew** | Claude reads the profile in the browser |

Proposal:
- **The dialogs say it, and let you choose.** "Add a company" gets a ticked box: "Let Claude find out about
  it now (in the chat on the left; it reads public pages and changes nothing on any site)". Untick it and
  the company is only added. "Add an application" gets the same box when the company is new.
- **Each Claude button says where it goes.** The tooltip reads, for example, "Starts in the chat on the
  left: Claude opens 星河科技's careers site in Resume Studio's browser and reads its open roles. Nothing is
  submitted." The sparkle icon stays the one sign that Claude is involved. "Add from a link" gets it too.
- **The request doesn't land in an unrelated chat.** If the open chat is busy with something else (a plan,
  a resume entry), a Claude button starts a new chat titled after the task, instead of appending.
  Alternative: ask "Put this in the open chat, or start a new one?" once, and remember the answer.

### B. Show what is reliable, and why something can't be done (cause 2)

- **"Mark as sent (1)" is greyed out with no reason.** Proposal: when it is disabled, say why next to it
  ("Pick the subset this site gets first"). The same goes for every greyed-out Claude button ("Claude
  isn't available": the tooltip says it, but nothing on the page does).
- **The table's Industry and Strength are blank for 星河科技, although its About is full.** It was filled
  before Claude filled every field (the 2026-10-18 fix). Nothing is broken, but it looks broken.
  Proposal: when the About exists and the fields are empty, the company page says "Claude only wrote the
  About last time: fill in the rest?" with the button. Or just press "Ask Claude to fill it in again"
  once.
- **The internal id "C1"** sits next to the company's name. It is how Claude and `scripts/hunt.py` refer to
  it. Proposal: move it into the ⋯ menu or a tooltip.

### C. Give each page a path (cause 3)

- **The company page as the steps of 网申, in order**, at the top of "Its careers site":
  1. Know the company (filled in: ✓ / not yet).
  2. Pick the subset this site gets.
  3. Sign up on its site: "Open its site". Claude can fill the fields if you ask, but you submit.
  4. Mark as sent: it keeps the version that went out.

  Each step shows done or not, and the next step is the one highlighted button. Every other button stays
  where it is.
- **One line of reassurance at the top of Applying**, because it is true: "Nothing here submits anything to
  a company or a site. You submit; a profile changes only when you press 'Make these changes'."
- **The browser's status in the top bar** ("The browser isn't open · Show the browser") is always there in
  Applying, and makes it look as if something should be running. Proposal: when the browser is off, show
  only a quiet "Browser" button, and show the status line only while it is open or busy.

### D. Make mistakes cheap (cause 4)

- **An undo for every change made in a table or on a page**: status, dates, text cells, order. A toast,
  "Status: Want to apply → Applied · Undo", for a few seconds, plus Ctrl+Z while it shows. The data
  already keeps a ledger (`events.tsv`), so an undo is a reverse change, and nothing is erased.
- **Tables: show which part opens the row.** In Notion only the title opens; the other cells edit. That
  stays (it is what you asked for). Proposal: on hover the title gets Notion's small "Open" chip, and an
  editable cell shows a pencil cursor, so the difference shows before the click.

### E. Small things

- At 1440 wide with the chat open, the Status column cuts "Want to apply" to "Want to ap…". The column can
  be dragged wider. Proposal: a minimum width that fits the longest status in the interface language.
- "Add a company": a large gap between the first field and the second label (the dialog's hint row).
- A platform's page has its own switcher (LinkedIn · BOSS直聘 · +) right under the Platforms tab, which
  already leads to both. It is the same choice in two places. Proposal: keep "All platforms" and the
  table, and drop the switcher, or keep the switcher and skip the table when there are two or three
  platforms. Your call; neither loses anything.

## Order suggested, if you approve

1. A and B (say what a click sets off, and why a button is off): small, and they remove most of the fear.
2. D (undo).
3. C (the path on the company page, the reassurance line, a quieter browser status).
