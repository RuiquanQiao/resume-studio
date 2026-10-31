"""A profile's shape, what a resume subset would put on a site, and when two values are the same.

The resume side is worked out here, not by Claude: the same resume subset always gives the same
profile (`desired`), and the window shows it next to the local copy as a suggestion. What a site says
comes in the shape `validate_current` checks (from the recorded route, or from Claude), and `same`
decides whether two values differ in a way worth showing (dates written differently, an organisation's
longer official name).
"""
from __future__ import annotations

import re
from typing import Any

from resume import model

from .sites import BASICS, FIELDS, Site

# a resume section -> the site's section, by id first, then by words in the id or title
SECTION_WORDS = [
    ("experience", ("experience", "work", "employment", "intern", "工作", "实习", "経歴", "경력")),
    ("projects", ("project", "项目", "プロジェクト", "프로젝트")),
    ("education", ("education", "study", "school", "教育", "学历", "学歴", "학력")),
    ("awards", ("award", "honor", "honour", "prize", "奖", "受賞", "수상")),
    ("skills", ("skill", "技能", "スキル", "기술")),
]


def site_section(section: Any, lang: str) -> str | None:
    sid = str(section.get("id") or "")
    if sid in FIELDS or sid == "skills":
        return sid
    words = (sid + " " + model.tr(section.get("title"), lang)).lower()
    for target, keys in SECTION_WORDS:
        if any(k in words for k in keys):
            return target
    return "skills" if str(section.get("kind")) == "list" else None


_MARK = [(re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"), (re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"\*(.+?)\*"), r"\1")]


def plain(text: str) -> str:
    """The resume's inline markup (**bold**, *italic*, [text](link)) as plain text."""
    for rx, rep in _MARK:
        text = rx.sub(rep, text)
    return text.strip()


def _date(value: Any, lang: str) -> str:
    s = model.tr(value, lang).strip()
    if not s:
        return ""
    if s.lower() in model._PRESENT:   # noqa: SLF001 - the same words the renderer accepts
        return "present"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else s


_SCHOOL = re.compile(r"universit|college|institut|school|academy|polytechnic|hochschule|universidad|université|"
                     r"大学|学院|学校|大學|학교|대학", re.IGNORECASE)


def _school(text: str) -> bool:
    return bool(_SCHOOL.search(text or ""))


def item_fields(section: str, entry: Any, lang: str, bullet: str) -> dict:
    title, sub = plain(model.tr(entry.get("title"), lang)), plain(model.tr(entry.get("subtitle"), lang))
    lines = [plain(model.bullet_text(b, lang)) for b in entry.get("bullets") or []]
    desc = "\n".join(bullet + x for x in lines if x)
    start, end = _date(entry.get("start"), lang), _date(entry.get("end"), lang)
    loc = plain(model.tr(entry.get("location"), lang))
    if section == "experience":
        return {"title": sub, "org": title, "location": loc, "start": start, "end": end, "description": desc}
    if section == "education":
        if _school(sub) and not _school(title):   # some resumes put the degree first and the school under it
            title, sub = sub, title
        return {"org": title, "degree": sub, "start": start, "end": end, "description": desc}
    if section == "projects":
        return {"name": title, "role": sub, "start": start, "end": end, "link": model.tr(entry.get("link"), lang),
                "description": desc}
    if section == "awards":
        return {"title": title, "issuer": sub, "date": end or start, "description": desc}
    raise ValueError(section)


def label_of(section: str, fields: dict) -> str:
    parts = {"experience": ("title", "org"), "education": ("degree", "org"), "projects": ("name", "role"),
             "awards": ("title", "issuer")}.get(section, ())
    return " · ".join(str(fields.get(p) or "") for p in parts if fields.get(p)) or "—"


def desired(doc: Any, subset: Any, lang: str, site: Site) -> dict:
    """The profile this resume subset gives on `site`, in resume language `lang`."""
    view_sections = {str(s.get("id")): s for s in doc.get("sections") or []}
    order = model.section_order(doc, subset)
    hidden = {str(s) for s in subset.get("hide_sections") or []}
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    chosen = list(doc.get("entries") or []) if model.is_all(subset) else \
        [by_id[str(i)] for i in subset.get("entries") or [] if str(i) in by_id]
    rank = {sid: i for i, sid in enumerate(order)}
    chosen.sort(key=lambda e: rank.get(str(e.get("section")), 999))   # stable: the job's order within a section

    items, skills, skipped = [], [], []
    for e in chosen:
        sid = str(e.get("section"))
        sec = view_sections.get(sid)
        if sec is None or sid in hidden:
            continue
        target = site_section(sec, lang)
        if target == "skills" and "skills" in site.sections:
            for t in e.get("tech") or []:
                name = plain(model.tr(t, lang))
                if name and name.lower() not in {s.lower() for s in skills}:
                    skills.append(name)
            continue
        if target not in site.sections or target == "skills":
            skipped.append({"key": str(e.get("id")), "section": model.tr(sec.get("title"), lang) or sid,
                            "label": plain(model.tr(e.get("title"), lang))})
            continue
        f = item_fields(target, e, lang, site.bullet)
        for name in site.lacks.get(target, []):
            # nowhere to put it on this site: it leads the description instead of disappearing
            extra = f.pop(name, "")
            if extra:
                f["description"] = extra + ("\n" + f["description"] if f.get("description") else "")
        items.append({"section": target, "key": str(e.get("id")), "label": label_of(target, f), "fields": f})

    headline = subset.get("headline") if "headline" in subset else (doc.get("profile") or {}).get("headline")
    basics = {}
    if "headline" in site.basics:
        basics["headline"] = plain(model.tr(headline, lang))
    return {"site": site.id, "subset": str(subset.get("id")), "lang": lang, "basics": basics,
            "items": items, "skills": skills, "skipped": skipped}


# ---- what the site says now (Claude reads it and sends it in this shape) --------------------------
CURRENT_SHAPE = """\
{
  "url": "<the address of the profile page you read>",
  "basics": {"headline": "...", "about": "..."},          // what the site shows now ("" when empty)
  "items": [                                              // every item on the site, in its order
    {"section": "experience", "key": "EXP-002" or null,  // key: the resume item it is, or null
     "fields": {"title": "...", "org": "...", "location": "...", "start": "2024-02", "end": "present",
                "description": "..."}}
  ],
  "skills": ["Python", "..."],                            // every skill listed on the site
  "drafts": {"about": "..."},                             // your proposed text for fields the resume has none for
  "notes": "anything the user should know (a section you couldn't read, a field the site lacks), in the user's language"
}"""


def validate_current(data: Any, site: Site) -> tuple[dict, list[str]]:
    """Check what Claude read; returns it cleaned up, plus what is wrong with it."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return {}, ["the profile must be a JSON object"]
    out: dict[str, Any] = {"url": str(data.get("url") or ""), "basics": {}, "items": [], "skills": [], "drafts": {},
                           "notes": str(data.get("notes") or "")}
    for name, slot in (("basics", "basics"), ("drafts", "drafts")):
        val = data.get(name) or {}
        if not isinstance(val, dict):
            errs.append(f"`{name}` must be an object")
            continue
        for k, v in val.items():
            if k not in BASICS:
                errs.append(f"`{name}.{k}`: only {', '.join(BASICS)} are known")
            elif not isinstance(v, str):
                errs.append(f"`{name}.{k}` must be text")
            else:
                out[slot][k] = v
    for i, it in enumerate(data.get("items") or []):
        if not isinstance(it, dict):
            errs.append(f"items[{i}] must be an object")
            continue
        sec = it.get("section")
        if sec not in FIELDS or sec not in site.sections:
            errs.append(f"items[{i}].section must be one of {', '.join(s for s in site.sections if s in FIELDS)}")
            continue
        fields = it.get("fields") or {}
        if not isinstance(fields, dict):
            errs.append(f"items[{i}].fields must be an object")
            continue
        known = site.fields(sec)
        unknown = [k for k in fields if k not in known]
        if unknown:
            errs.append(f"items[{i}].fields: {', '.join(unknown)} not known for {sec} on {site.name} (use {', '.join(known)})")
        key = it.get("key")
        out["items"].append({"section": sec, "key": str(key) if key else None,
                             "fields": {k: str(v if v is not None else "") for k, v in fields.items() if k in known}})
    skills = data.get("skills") or []
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        errs.append("`skills` must be a list of names")
    else:
        out["skills"] = [s.strip() for s in skills if s.strip()]
    return out, errs


# ---- when two values are the same ---------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def norm_date(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return ""
    if s in model._PRESENT or s in ("present", "now", "至今", "现在"):   # noqa: SLF001
        return "present"
    m = re.search(r"(\d{4})\s*[-./年]\s*(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", s)
    if m and m.group(1) in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1)]:02d}"
    m = re.fullmatch(r"(\d{4})", s)
    return m.group(1) if m else s


def norm_text(s: str) -> str:
    lines = [re.sub(r"^\s*(?:[•·\-*–]|\d+[.、)])\s*", "", x) for x in (s or "").splitlines()]
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lower()


def same(field: str, a: str, b: str) -> bool:
    if field in ("start", "end", "date"):
        return norm_date(a) == norm_date(b)
    if field == "link":
        return (a or "").strip().rstrip("/").lower().removeprefix("https://").removeprefix("http://") == \
            (b or "").strip().rstrip("/").lower().removeprefix("https://").removeprefix("http://")
    if field in ("org", "issuer", "degree"):
        # sites pick organisations from their own list ("The Harbour City University"): a name
        # that contains the other is the same organisation, not a change worth making
        x, y = norm_text(a), norm_text(b)
        return x == y or bool(x and y and (x in y or y in x))
    return norm_text(a) == norm_text(b)
