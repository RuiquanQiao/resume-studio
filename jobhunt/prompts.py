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
  goes in (the company's own site with its choice number, a platform, a referral), what was sent, next step.
- `{j} show <company>` — a company: details, what is known about it (about), its roles, recent events.
  `{j} show <company>/<role>` (or an id like A3) — one application with its whole timeline.
- `{j} add-company "<name>" [--site <careers url>] [--alias Xinghe] [--industry ..] [--location ..] [--deadline 2026-10-31]`
  · `edit-company <company> [--name|--site|--subset SUB-002|--note|--account ..]`
- `{j} add-role <company> "<role>" [--url ..] [--channel site|platform|referral|other] [--platform boss]
  [--order 2] [--status saved]` · `edit-role <company>/<role> [--role|--url|--order|--note ..]`
- `{j} status <company>/<role> <status> [--note "一面 周三 14:00"] [--on 2026-10-18]`
- `{j} note <company>/<role> "<what happened>" [--on <date>]` — interviews, tests, emails: anything worth keeping.
- `{j} next <company>/<role> "<next step>" [--due <date>]`
- `{j} about <company> --stdin` with JSON `{{"summary": "...", "points": [{{"topic": "...", "text": "...",
  "sources": ["https://..."]}}], "read_at": "YYYY-MM-DD"}}` — what you found out about the company.
- `{j} sent <company>` — the user submitted the sign-up on the company's own site: every role there not sent yet
  gets the subset the company is filled from, kept as a version and pinned. `sent <company>/<role>` for one posting.

Statuses: saved (wants to apply), applied, assessment (测评/笔试/OA), interview, offer, hired, rejected,
withdrawn. Interview rounds (一面, 二面, HR 面) are notes on an interview, not statuses. The user's own words
work ("一面过了，下周二二面" → keep `interview`, add a note with the date).

How to work it:
- When the user tells you what happened, change it right away and say in one line what you changed. Ask only
  when you can't tell which company or role they mean. Dates: today is in your context; write them YYYY-MM-DD.
- Companies by name or alias: `星河科技`, `Xinghe` and `C2` are the same company once the alias is there; add the
  aliases you see the user use.
- About a company (what it does, how it is doing, prospects, culture, pay, how it hires graduates): research
  with web search, prefer recent and primary sources (the company's own pages and reports, reputable news), say
  how recent each point is, and save it with `about` with a source for every point. Never invent figures;
  say plainly what you couldn't find.
- `sent` only when the user says they submitted. Submitting on the site is always the user's own click.
- Never delete anything here: removing a company or a role is the user's, in the window.
"""
