"""resume.yaml data model.

See SKILL.md for the documented schema. Principles:
  - `entries` is the experience library: append-only in spirit, never limited to one page.
  - Every entry has a fact layer (`notes`, `evidence`) that no template ever renders.
  - A `version` is one job target (岗位): it references entry ids plus its own headline,
    template and layout; it never copies content. The reserved version `ALL` is the full
    set: every entry and every contact, in library order (its `entries` are ignored).
  - Any text field may be a plain string or a per-language map like {en: ..., zh: ...}.
"""
from __future__ import annotations

import re
import sys
from typing import Any

from core.store import fingerprint, to_plain

SCHEMA_VERSION = 1

DEFAULT_SECTIONS = [
    {"id": "education", "title": {"en": "Education", "zh": "教育背景"}, "kind": "timeline"},
    {"id": "experience", "title": {"en": "Experience", "zh": "工作经历"}, "kind": "timeline"},
    {"id": "projects", "title": {"en": "Projects", "zh": "项目经历"}, "kind": "timeline"},
    {"id": "skills", "title": {"en": "Skills", "zh": "专业技能"}, "kind": "list"},
    {"id": "awards", "title": {"en": "Awards", "zh": "荣誉奖项"}, "kind": "timeline"},
]


ALL_ID = "ALL"


def default_all_version() -> dict:
    return {"id": ALL_ID, "label": "全部经历", "lang": "en", "template": "classic", "headline": "", "layout": {}}


def is_all(version: Any) -> bool:
    return bool(version) and str(version.get("id")) == ALL_ID


def default_cjk_font() -> str:
    if sys.platform.startswith("win"):
        return "Microsoft YaHei"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


DEFAULT_LAYOUT = {
    "paper": "a4",          # a4 | letter
    "font_size": 10.0,      # pt, any value
    "line_spread": 1.0,
    "margin_x": 1.4,        # cm
    "margin_y": 1.2,        # cm
    "section_sep": 6.0,     # pt before each section title
    "entry_sep": 3.0,       # pt between entries
    "item_sep": 1.0,        # pt between bullets
    "accent": "1F4E79",     # hex, no '#'
    "font": "",             # main font name; empty = template default
    "cjk_font": "",         # empty = platform default
}


def skeleton() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "settings": {"languages": ["en"], "export_dir": "exports"},
        "profile": {"name": "Your Name", "contacts": [], "notes": ""},
        "sections": [dict(s) for s in DEFAULT_SECTIONS],
        "entries": [],
        "versions": [default_all_version()],
    }


# ---- text helpers --------------------------------------------------------
def tr(value: Any, lang: str) -> str:
    """Resolve a plain-or-per-language text value for one language."""
    if value is None:
        return ""
    if isinstance(value, dict):
        v = value.get(lang)
        if v:
            return str(v)
        for other in value.values():  # fall back to any language that has text
            if other:
                return str(other)
        return ""
    return str(value)


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_PRESENT = {"present", "now", "current", "至今", "今"}


def fmt_date(value: Any, lang: str) -> str:
    s = tr(value, lang).strip()
    if not s:
        return ""
    if s.lower() in _PRESENT:
        return "至今" if lang == "zh" else "Present"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", s)
    if m:
        y, mo = m.group(1), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y}.{mo:02d}" if lang == "zh" else f"{_MONTHS[mo - 1]} {y}"
    return s


def date_range(entry: dict, lang: str) -> str:
    if entry.get("date"):
        return tr(entry["date"], lang)
    start, end = fmt_date(entry.get("start"), lang), fmt_date(entry.get("end"), lang)
    if start and end:
        return f"{start} – {end}"
    return start or end


# ---- lookup --------------------------------------------------------------
KINDS = ("profile", "settings", "sections", "entry", "version")


def get_item(doc: Any, kind: str, item_id: str | None = None) -> Any:
    if kind in ("profile", "settings", "sections"):
        return doc.get(kind)
    coll = doc.get("entries" if kind == "entry" else "versions") or []
    for item in coll:
        if str(item.get("id")) == str(item_id):
            return item
    return None


def get_version(doc: Any, version_id: str) -> Any:
    """Like get_item, but ALL always exists (a file may not have saved it yet)."""
    v = get_item(doc, "version", version_id)
    if v is None and version_id == ALL_ID:
        return default_all_version()
    return v


def with_all(versions: list) -> list:
    """Versions as the UI sees them: ALL first, then the job targets."""
    rest = [v for v in versions if not is_all(v)]
    found = next((v for v in versions if is_all(v)), None)
    return [found if found is not None else default_all_version(), *rest]


def hashes(doc: Any) -> dict:
    return {
        "profile": fingerprint(doc.get("profile")),
        "settings": fingerprint(doc.get("settings")),
        "sections": fingerprint(doc.get("sections")),
        "entry": {str(e.get("id")): fingerprint(e) for e in doc.get("entries") or []},
        "version": {str(v.get("id")): fingerprint(v) for v in doc.get("versions") or []},
    }


def next_entry_id(doc: Any) -> str:
    """Internal, stable, section-independent id. The UI never shows it: the sidebar numbers
    entries within their own section instead, so renaming or moving sections changes nothing."""
    nums = [int(m.group(1)) for e in doc.get("entries") or []
            if (m := re.fullmatch(r"EXP-(\d+)", str(e.get("id", "")), re.IGNORECASE))]
    return f"EXP-{(max(nums) + 1) if nums else 1:03d}"


def next_version_id(doc: Any) -> str:
    nums = [int(m.group(1)) for v in doc.get("versions") or []
            if (m := re.fullmatch(r"JOB-(\d+)", str(v.get("id", ""))))]
    return f"JOB-{(max(nums) + 1) if nums else 1:03d}"


def validate(doc: Any) -> list[str]:
    """Human-readable warnings; never blocks saving."""
    warn: list[str] = []
    ids = [str(e.get("id")) for e in doc.get("entries") or []]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        warn.append(f"经历 ID 重复：{dup}")
    section_ids = {str(s.get("id")) for s in doc.get("sections") or []}
    for e in doc.get("entries") or []:
        if str(e.get("section")) not in section_ids:
            warn.append(f"{e.get('id')} 的 section「{e.get('section')}」不在 sections 列表里")
    known = set(ids)
    for v in doc.get("versions") or []:
        if is_all(v):
            continue
        for ref in v.get("entries") or []:
            if str(ref) not in known:
                warn.append(f"岗位 {v.get('id')} 引用了不存在的经历 {ref}")
    return warn


# ---- view (engine-agnostic) ---------------------------------------------
def build_view(doc: Any, version: dict) -> dict:
    """Resolve one version into plain, single-language data for a renderer.

    Text keeps the tiny inline markup (**bold**, *italic*, [text](url)); each
    renderer converts it to its own syntax. The fact layer is dropped here, so no
    template can leak it.
    """
    lang = str(version.get("lang") or "en")
    layout = dict(DEFAULT_LAYOUT)
    layout.update(to_plain(version.get("layout") or {}))
    if not layout.get("cjk_font"):
        layout["cjk_font"] = default_cjk_font()
    accent = str(layout.get("accent") or "").lstrip("#").upper()
    layout["accent"] = accent if re.fullmatch(r"[0-9A-F]{6}", accent) else DEFAULT_LAYOUT["accent"]
    if layout.get("paper") not in ("a4", "letter"):
        layout["paper"] = "a4"

    everything = is_all(version)
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    if everything:
        chosen = list(doc.get("entries") or [])
    else:
        chosen = [by_id[str(i)] for i in version.get("entries") or [] if str(i) in by_id]

    sections = list(doc.get("sections") or [])
    order = [str(s) for s in version.get("sections") or []]
    if order:
        rank = {sid: i for i, sid in enumerate(order)}
        sections.sort(key=lambda s: rank.get(str(s.get("id")), len(rank)))
    hidden = {str(s) for s in version.get("hide_sections") or []}

    p = doc.get("profile") or {}
    hidden_contacts = set() if everything else {str(c) for c in version.get("hide_contacts") or []}
    headline = version.get("headline") if "headline" in version else p.get("headline")  # older files
    profile = {
        "name": tr(p.get("name"), lang),
        "headline": tr(headline, lang),
        "contacts": [{"label": tr(c.get("label"), lang), "value": tr(c.get("value"), lang),
                      "url": tr(c.get("url"), lang)} for c in p.get("contacts") or []
                     if str(c.get("label") or "") not in hidden_contacts],
    }

    out_sections = []
    for s in sections:
        sid = str(s.get("id"))
        if sid in hidden:
            continue
        items = []
        for e in chosen:
            if str(e.get("section")) != sid:
                continue
            items.append({
                "id": str(e.get("id")),
                "title": tr(e.get("title"), lang),
                "subtitle": tr(e.get("subtitle"), lang),
                "location": tr(e.get("location"), lang),
                "dates": date_range(e, lang),
                "link": tr(e.get("link"), lang),
                "tech": [tr(t, lang) for t in e.get("tech") or [] if tr(t, lang)],
                "bullets": [tr(b, lang) for b in e.get("bullets") or [] if tr(b, lang)],
            })
        if items:
            out_sections.append({"id": sid, "title": tr(s.get("title"), lang),
                                 "kind": str(s.get("kind") or "timeline"), "entries": items})

    return {"lang": lang, "profile": profile, "sections": out_sections, "layout": layout,
            "template": str(version.get("template") or "classic")}
