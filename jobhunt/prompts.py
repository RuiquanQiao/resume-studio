"""What Claude is told about the job-hunt tracker in the 网申 workspace's chats (docs/JOB_HUNT.md)."""
from __future__ import annotations

from resume import prompts as rp

HUNT = rp.SKILL_ROOT / "scripts" / "hunt.py"


def cli(data_path: str) -> str:
    return f'python "{HUNT}" --data "{data_path}"'


def tools_note(data_path: str) -> str:
    j = cli(data_path)
    return f"""\
## The job-hunt tracker

The user tracks their applications in Resume Studio's own tracker, shown in the Applying workspace as three tables:
applications (one per company + role), companies, and platforms (the online profiles below). Read and change
it only through this command (Bash); every call prints what is there now, and the window refreshes by itself:

- `{j} list [--status interview] [--company <name>]` — every application: id, company / role, status, how it
  goes in (the company's own site with its choice number, a platform, a referral), what was sent, the next thing booked.
- `{j} show <company>` — a company: details, what is known about it (about), its roles, recent events.
  `{j} show <company>/<role>` (or an id like A3) — one application with its whole timeline.
- `{j} add-company "<name>" [--site <careers url>] [--alias Xinghe]`
  · `edit-company <company> [--name|--site|--subset SUB-002|--note|--account ..]`
- `{j} fill <company> --stdin` with JSON — what you found out about the company, **every field at once**:
  `{{"name": "<official name>", "aliases": [..], "industry": "..", "strength": "..", "location": "<HQ>",
  "size": "..", "sites": [{{"url": "..", "kind": "campus|intern|social|other", "system": "own site|Moka|北森|飞书|Greenhouse|.."}}],
  "logo_url": "https://..", "sources": {{"<field>": "https://.."}},
  "about": {{"summary": "..", "points": [{{"topic": "..", "text": "..", "sources": ["https://.."]}}], "read_at": "YYYY-MM-DD"}}}}`.
  `{j} fill --help` shows a full example. Always look up **where it hires** (its campus/careers pages' work
  locations, offices) and send them as `"places": ["上海", "北京", "remote: 中国大陆"]` with a source: they put the
  company on the user's world map.
- `{j} where` — every place in the tracker and where the map puts it ("not found" ones show off the map).
  `{j} pin "<area as written>" <lat> <lon>` places one the map can't find or got wrong (`--unpin` undoes it).
- `{j} add-role <company> "<role>" [--url ..] [--channel site|platform|referral|other] [--platform boss]
  [--order 2] [--status saved] [--deadline YYYY-MM-DD|rolling] [--batch ..]` · `edit-role <company>/<role> [--role|--url|--referral|--order|--note|--deadline|--batch ..]`
  (`--referral` is the 内推码 for that role: put it there, not in a note; no code means no referral)
  `--place 南京 --place 上海 --place "remote: 中国大陆" --place "hybrid: 悉尼"`: where the role is, one flag per city
  (on edit-role the flags are the whole list). Put places here, never in the role's name ("软件开发工程师", not
  "软件开发工程师（南京）"). A remote role always says the region it is open to; "remote: 不限" only when the posting says anywhere.
- `{j} status <company>/<role> <status> [--note "一面 周三 14:00"] [--on 2026-10-18]`
- `{j} note <company>/<role> "<what happened>" [--on <date>]` — interviews, tests, emails: anything worth keeping.
- `{j} book <company>/<role> 一面 --at "2026-10-25 14:00" [--minutes 45] [--where "腾讯会议 123 / 地址"] [--note ..]`
  — something with a date: a test (笔试, 测评), 一面, 二面, 三面, HR 面… Whenever the user mentions one with its
  date or time ("周三下午两点二面"), book it (work out the date from today), so it shows on their calendar.
  Booking moves the status forward by itself (一面 → interview, 笔试 / 测评 → assessment; never back), so a
  `status` is needed only for outcomes (offer, rejected…) or when nothing was booked.
  `rebook <company>/<role> <E2 or its title> [--title|--at|--minutes|--where|--note ..]` when it moves ·
  `unbook <company>/<role> <E2>` when it's off. `{j} ics [<company>/<role>]` writes them (deadlines too) as an
  .ics file the user can open to import into Outlook or any calendar app.
- `{j} jd <company>/<role> --stdin` — the posting's full text (the JD: duties, requirements, pluses), as the
  page or the user's screenshot says it, kept on the role. `show` prints it; read it before tailoring a resume.
- `{j} sent <company>` — the user submitted the sign-up on the company's own site: every role there not sent yet
  gets the subset the company is filled from, kept as a version and pinned. `sent <company>/<role>` for one posting.
- **Open postings** (what the companies' sites list now; not applications until the user goes for one):
  `{j} scan [<company> ..]` reads the sites now (career-ops' providers for Moka, Feishu/ByteDance, Tencent, Meituan,
  Alibaba, Greenhouse, Lever, Ashby…; a recorded route in Resume Studio's browser for any other site) ·
  `{j} scan-route <company> --file route.json` · `{j} postings <company> --stdin [--partial]` ·
  `{j} apply P12` (the user goes for posting P12: it becomes an application) ·
  `{j} keywords "算法, AI, Agent"` (the roles the user looks for; other postings are folded away).

Statuses: saved (wants to apply), applied, assessment (测评/笔试/OA), interview, offer, hired, rejected,
withdrawn. Interview rounds (一面, 二面, HR 面) are notes on an interview, not statuses. The user's own words
work ("一面过了，下周二二面" → keep `interview`, add a note with the date).

How to work it:
- When the user tells you what happened, change it right away and say in one line what you changed. Ask only
  when you can't tell which company or role they mean. Dates: today is in your context; write them YYYY-MM-DD.
- Companies by name or alias: `星河科技`, `Xinghe` and `C2` are the same company once the alias is there; add the
  aliases you see the user use.
- **A company fills itself.** Whenever a company is added, or asked about, or `show` lists fields as "not filled
  yet", research it with web search and send everything with one `fill`: its official name (it becomes an alias;
  the user's name for it stays), aliases, industry, **strength** (listed: market cap with exchange, ticker and
  date; not listed: valuation, latest round or headcount, with the date), headquarters, size, **its careers
  sites** (find them yourself: campus, intern and social hiring, and which system each runs on), a **logo**
  (`logo_url`: a direct link to a picture of it, from its press kit or a reputable page; once a company has a
  site, Resume Studio takes the icon off the company's own site by itself, so send one when `show` still says
  no logo, the site runs on a hiring system like Moka or Feishu, or the icon isn't the company's), and
  the About (what it does, how it is doing, prospects, culture, pay, how it hires graduates). Prefer recent,
  primary sources (its own pages and reports, reputable news), give a source for every field and point, and
  never invent figures: leave out what you couldn't find and say so. Never stop at the About: the fields are
  what the tables show. Fields the user edited are theirs; `fill` leaves them and says which.
- Deadlines belong to roles, never to the company. A role's deadline is an event of its own (every role starts
  with one, its date unknown): set it with `--deadline YYYY-MM-DD` on `add-role` / `edit-role`, `--deadline rolling`
  when it closes once filled (滚动招聘, 招满即止, no date), never with `book`.
- `--batch` is the hiring round in the user's own words (27秋招, 27寒假实习, 2027 Graduate Program…): reuse the
  names the user's other roles have (`list` shows them); never make up a scheme of your own.
- **Recording a site's route** (when `show` or `scan` says a site has none): open the site's job list in Resume
  Studio's browser (the browser commands), signed in if it needs it (the user signs in with the button on the
  company's row; never type a password). Find how the list loads: often the page calls its own JSON API, and the
  best script calls that same API with `fetch` (same origin, the user's sign-in) and pages through every posting;
  otherwise read the rendered list by headings, labels and text, never by generated class names. Write
  `{{"pages": [{{"url": "<the list's address>", "script": "async (h) => ({{postings: [{{title, url, location,
  category, batch: 'campus'|'intern'|'social', deadline: 'YYYY-MM-DD', posted}}]}})"}}]}}` (return
  `{{signed_out: true}}` when the page asks to sign in), test the script with the browser's `extract`, then save it
  with `scan-route`: it runs once and its postings come in; later scans replay it with no Claude. Read every
  posting of every list the site has (campus, intern), not only the first page. When no script can do it, read
  the postings yourself and send them with `postings`.
- **A posting's link** (pasted in the chat, or "Read this posting and add it: <link>"): read the page (web fetch;
  when it needs scripts or a sign-in, open it in Resume Studio's browser with the browser commands). Find the
  company: `list` / `show` first; when it isn't there, `add-company` and fill it in as above. Then `add-role` with
  `--url <link>`, `--deadline` when the page gives one (`rolling` when it says 招满即止), `--place` for where it
  is, `--batch` when the round matches one the user already uses, and a `--note` with what else matters. Then keep the posting's whole text with `jd` (word for word, its headings and lists kept; not a summary).
  Do the same when the user sends a JD as a screenshot or pasted text. Notes are the user's own: never put a JD
  there. Say in two lines what you added; ask only when the page names no company or no role.
- `sent` only when the user says they submitted. Submitting on the site is always the user's own click.
- Never delete anything here: removing a company or a role is the user's, in the window.
- The tables are arranged by the user like a Notion database (views as tabs, filters, sorts, groups, a board,
  column order and widths), saved in `.studio/hunt/views.json`. Leave that file alone unless the user asks you
  to arrange a table; then edit only the views they named.
"""
