"""What is booked for the applications, and their deadlines, as an iCalendar file (RFC 5545) that
Outlook, Apple Calendar, Google Calendar and the rest import (docs/JOB_HUNT.md, 2026-10-21).

Times are written as the user typed them, without a time zone ("floating"): a 14:00 一面 stays at 14:00
in whatever calendar it lands in. Every entry keeps its UID, so importing again updates it."""
from __future__ import annotations

import datetime as dt
import time

from core.i18n import t


def _esc(s: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 bytes go on as continuation lines (a space first), never splitting a character."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _when(at: str, minutes: int) -> list[str]:
    day = dt.date.fromisoformat(at[:10])
    if len(at) > 10:   # a time: floating local time, an hour unless it says
        start = dt.datetime.fromisoformat(at[:10] + "T" + at[11:16])
        end = start + dt.timedelta(minutes=minutes or 60)
        return [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
    return [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}"]


def calendar(apps: list[dict], companies: dict[str, str]) -> str:
    """apps: applications (with `schedule`); companies: id -> name."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Resume Studio//Applying//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + _esc(t("Applying"))]

    def event(uid: str, summary: str, when: list[str], where: str = "", desc: str = "", url: str = "") -> None:
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@resume-studio", f"DTSTAMP:{stamp}", *when, "SUMMARY:" + _esc(summary)])
        if where:
            lines.append("LOCATION:" + _esc(where))
        if desc:
            lines.append("DESCRIPTION:" + _esc(desc))
        if url.startswith(("http://", "https://")):
            lines.append("URL:" + url)
        lines.append("END:VEVENT")

    for a in apps:
        who = " · ".join(x for x in (companies.get(a.get("company") or "", ""), a.get("role") or "") if x)
        url = a.get("url") or ""
        for e in a.get("schedule") or []:
            if not e.get("at"):   # a deadline not known yet, or none (招满即止)
                continue
            desc = "\n".join(x for x in (e.get("note") or "", url) if x)
            if e.get("kind") == "apply":   # the deadline: the UID it had as a field, so importing again updates it
                event(f"{a['id']}-deadline", t("Deadline: {what}", what=who), _when(e["at"], 0), "", desc, url)
                continue
            event(f"{a['id']}-{e['id']}", f"{e['title']} · {who}", _when(e["at"], int(e.get("minutes") or 0)),
                  e.get("where") or "", desc, url)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
