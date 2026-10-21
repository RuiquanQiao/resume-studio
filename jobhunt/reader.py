"""Reading a role's own page: its job description, and where and until when it is open, with no Claude.

The page is opened in Resume Studio's browser (a tab of its own, minimized, closed afterwards), because most
careers sites (Moka, 北森, Feishu, Workday) draw the posting with scripts after the page has loaded.

The description is found in two ways, the first that finds one wins:
  1. headings: the smallest part of the page holding the most of 职位描述 / 岗位职责 / 任职要求 /
     Responsibilities / Requirements… (Moka, 北森, most Chinese sites);
  2. text density, after Readability: the part of the page with the most running text (Workday, which has no
     such headings, only paragraphs and lists), and only a long one: a short notice ("This domain is for use
     in examples", a login wall) is no description, and must never replace one.
Where and until when come from labelled lines ("工作地点", "Location", "截止日期", "结束日期：2026年10月31日").
"""
from __future__ import annotations

import re
from typing import Any

from .store import _places

READ_JS = r"""
async (h) => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // section headings: a description has two or more of these (the title above them, 职位描述, doesn't count)
  const MARK = /工作职责|岗位职责|职位职责|工作内容|任职要求|任职资格|岗位要求|职位要求|加分项|responsibilities|requirements|qualifications|what you('|’)ll do|what you will do|about the role|about the job|the opportunity/gi;
  const ACTIONS = /^(分享|收藏|投递|申请|申请职位|立即投递|立即申请|投递简历|我要投递|apply|apply now|share|save)$/i;
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "NAV", "HEADER", "FOOTER", "BUTTON", "SELECT", "OPTION"]);
  const marks = (s) => new Set((s.match(MARK) || []).map((x) => x.toLowerCase())).size;
  const tidy = (s) => s.split("\n").filter((x) => !ACTIONS.test(x.trim())).join("\n").replace(/\n{3,}/g, "\n\n").trim();
  function find() {
    const els = [...document.body.querySelectorAll("*")].filter((el) => !SKIP.has(el.tagName) && !el.closest("nav,header,footer"));
    // 1. the smallest part with the most section headings
    let best = null, bestMarks = 0, bestLen = Infinity;
    for (const el of els) {
      const s = el.textContent || "";
      if (s.length < 120 || s.length > 20000) continue;
      const m = marks(s);
      if (m > bestMarks || (m === bestMarks && m > 0 && s.length < bestLen)) { best = el; bestMarks = m; bestLen = s.length; }
    }
    if (best && best.innerText.trim().length >= 150) return { jd: tidy(best.innerText), how: "headings" };
    // 2. the part with the most running text: each paragraph counts for its parent, half for the one above
    const score = new Map();
    for (const el of els) {
      let own = 0;
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent.trim().length;
      if (own < 25 || el.closest("a")) continue;
      const p = el.parentElement, g = p && p.parentElement;
      if (p) score.set(p, (score.get(p) || 0) + own);
      if (g) score.set(g, (score.get(g) || 0) + own / 2);
    }
    let top = null, topScore = 0;
    for (const [el, s] of score) if (s > topScore && el !== document.body) { top = el; topScore = s; }
    return top && topScore >= 800 ? { jd: tidy(top.innerText), how: "density" } : null;
  }
  // single-page sites draw the posting after "load", some after a "Loading" screen: wait until the text
  // stops growing, and look again a few times while no description is there yet
  // (35 s at most, by the clock: the caller gives up at 60, and timers can run slow in a background window)
  const end = Date.now() + 35000;
  let got = null;
  while (!got && Date.now() < end) {
    let last = -1, still = 0;
    while (still < 4 && Date.now() < end) {
      await sleep(400);
      const n = ((document.body && document.body.innerText) || "").length;
      still = n === last && n > 200 ? still + 1 : 0; last = n;
    }
    if (document.body) got = find();
    if (!got && Date.now() < end) await sleep(1500);
  }
  return { title: document.title, jd: got ? got.jd.slice(0, 20000) : "", how: got ? got.how : "",
           text: document.body ? document.body.innerText.slice(0, 30000) : "" };
}
"""

LOCATION_LABELS = ("工作地点", "工作城市", "工作地址", "办公地点", "work location", "locations", "location")
DEADLINE_LABELS = ("截止日期", "截止时间", "申请截止", "投递截止", "结束日期", "closing date", "application deadline",
                   "applications close", "apply by", "deadline")
EMPTY = {"-", "—", "--", "暂无", "无", "不限", "n/a", "none"}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _labelled(lines: list[str], labels: tuple[str, ...]) -> str:
    """The value written after a label: on its line ("工作地点：杭州") or on the next one that isn't the label again."""
    for i, line in enumerate(lines):
        low = line.casefold()
        for lab in labels:
            if not low.startswith(lab):
                continue
            rest = line[len(lab):].strip()
            if rest.startswith((":", "：")):
                rest = rest[1:].strip()
                return rest if rest.casefold() not in EMPTY and len(rest) <= 80 else ""
            if rest:
                continue   # a sentence that starts with the word ("Location is flexible"), not a label
            for nxt in lines[i + 1:i + 3]:
                if nxt.casefold() == low:
                    continue   # Moka writes the label twice
                return nxt if nxt.casefold() not in EMPTY and len(nxt) <= 80 else ""
    return ""


def _date_in(s: str) -> str:
    m = re.search(r"(20\d\d)\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else ""
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(20\d\d)\b", s) or re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(20\d\d)\b", s)
    if m:
        a, b, y = m.groups()
        day, mon = (a, b) if a.isdigit() else (b, a)
        mo = MONTHS.get(mon[:3].casefold())
        return f"{int(y):04d}-{mo:02d}-{int(day):02d}" if mo and 1 <= int(day) <= 31 else ""
    return ""


def details(text: str) -> dict:
    """Where and until when, from the page's text: {"places": [...], "deadline": "YYYY-MM-DD"}, each only if written."""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    out: dict[str, Any] = {}
    loc = _labelled(lines, LOCATION_LABELS)
    if loc:
        try:
            out["places"] = _places([x for x in re.split(r"\s*[/,，、;；|]\s*", loc) if x])
        except ValueError:
            pass
    when = _labelled(lines, DEADLINE_LABELS)
    if not when:   # "结束日期：2026年10月31日" sits under a heading of its own on Workday
        when = next((x for x in lines if len(x) <= 120 and _date_in(x)
                     and re.search(r"截止|结束日期|closing|closes?\b|deadline|apply by", x, re.IGNORECASE)), "")
    if when and _date_in(when):
        out["deadline"] = _date_in(when)
    return out


def read_posting(browser: Any, url: str) -> dict:
    """{"jd", "places", "deadline", "title"} from the role's page; "jd" is "" when no description was found."""
    got = browser.read_page(url, READ_JS, timeout=60) or {}
    if not isinstance(got, dict):
        got = {}
    return {"jd": str(got.get("jd") or "").strip(), "title": str(got.get("title") or ""), **details(str(got.get("text") or ""))}
