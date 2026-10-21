"""resume.yaml data model.

See SKILL.md for the documented schema. Principles:
  - `entries` is the experience library: append-only in spirit, never limited to one page.
  - Every entry has a fact layer (`notes`, `evidence`) that no template ever renders, and may have
    `interview`, its mock interview (questions, answers, follow-ups), private in the same way.
  - A `subset` is the resume for one kind of role: it references entry ids plus its own
    headline, template and layout; it never copies content. The reserved subset `ALL` is the
    full set: every entry and every contact, in library order (its `entries` are ignored).
    Files before schema 2 called subsets `versions`; `migrate` renames the key.
  - Any text field may be a plain string or a per-language map like {en: ..., zh: ...}.
"""
from __future__ import annotations

import re
from typing import Any

from core.i18n import t
from core.store import fingerprint, to_plain

from . import langs as L

SCHEMA_VERSION = 2


def migrate(doc: Any) -> bool:
    """Bring an older file up to date in place (keys keep their place and comments).
    Schema 2: `versions` became `subsets`. True when something changed."""
    if not isinstance(doc, dict) or "versions" not in doc:
        return False
    old = doc["versions"]
    if doc.get("subsets"):  # both (an older tool wrote the old key again): keep every subset once
        have = {str(v.get("id")) for v in doc["subsets"]}
        doc["subsets"].extend(v for v in old or [] if str(v.get("id")) not in have)
        del doc["versions"]
    elif hasattr(doc, "insert"):  # ruamel map: the new key takes the old one's place
        pos = list(doc.keys()).index("versions")
        comment = doc.ca.items.pop("versions", None)   # a comment line above the key is kept under its name
        doc.pop("subsets", None)
        del doc["versions"]
        doc.insert(pos, "subsets", old)
        if comment:
            doc.ca.items["subsets"] = comment
    else:
        doc.pop("subsets", None)
        doc["subsets"] = doc.pop("versions")
    if "schema_version" in doc and (doc.get("schema_version") or 0) < SCHEMA_VERSION:
        doc["schema_version"] = SCHEMA_VERSION
    return True

DEFAULT_SECTIONS = [
    {"id": "education", "kind": "timeline"},
    {"id": "experience", "kind": "timeline"},
    {"id": "projects", "kind": "timeline"},
    {"id": "skills", "kind": "list"},
    {"id": "awards", "kind": "timeline"},
]


def section_title(section_id: str, lang: str) -> str | None:
    """A default section's title in one resume language (None for custom sections)."""
    titles = L.SECTION_TITLES.get(lang) or L.SECTION_TITLES["en"]
    return titles.get(section_id)


def default_sections(languages: list[str]) -> list[dict]:
    out = []
    for s in DEFAULT_SECTIONS:
        names = {lang: section_title(s["id"], lang) for lang in languages}
        out.append({"id": s["id"], "title": names[languages[0]] if len(languages) == 1 else names, "kind": s["kind"]})
    return out


def add_language_titles(doc: Any, lang: str) -> None:
    """A language was added: give the default sections their titles in it (custom ones are left to the user)."""
    for s in doc.get("sections") or []:
        name = section_title(str(s.get("id")), lang)
        title = s.get("title")
        if not name or (isinstance(title, dict) and title.get(lang)):
            continue
        if isinstance(title, dict):
            title[lang] = name
        elif title:  # one shared string so far: keep it for the languages that had it
            others = [x for x in (doc.get("settings") or {}).get("languages") or [] if x != lang]
            s["title"] = {**{o: title for o in others}, lang: name}


ALL_ID = "ALL"


def default_all_subset(lang: str = "en") -> dict:
    return {"id": ALL_ID, "label": "All entries", "lang": lang, "template": "classic", "headline": "", "layout": {}}


def is_all(subset: Any) -> bool:
    return bool(subset) and str(subset.get("id")) == ALL_ID


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
    "font": "",             # Latin font name; empty = default (see render/fonts.py)
    "cjk_font": "",         # Chinese font name; empty = default (FandolSong)
}


def skeleton(lang: str = "en") -> dict:
    """A new, empty file whose resume language is `lang` (the computer's language by default)."""
    all_v = default_all_subset(lang)
    return {
        "schema_version": SCHEMA_VERSION,
        "settings": {"languages": [lang], "export_dir": "exports"},
        "profile": {"name": "Your Name", "contacts": [], "notes": ""},
        "sections": default_sections([lang]),
        "entries": [],
        "subsets": [all_v],
    }


# ---- text helpers --------------------------------------------------------
# Font Awesome 5 icon for a contact, chosen from its label first, then from its link.
CONTACT_ICONS = [
    (("email", "mail"), ("mailto:",), "envelope"),
    (("phone", "mobile", "tel"), ("tel:",), "mobile-alt"),
    (("linkedin",), ("linkedin.com",), "linkedin"),
    (("github",), ("github.com",), "github"),
    (("wechat", "weixin"), (), "weixin"),
    (("location", "address", "city"), (), "map-marker-alt"),
    (("website", "site", "homepage", "portfolio", "blog"), ("http",), "globe"),
]


def contact_icon(label: str, url: str) -> str:
    """Icon name for a contact, or '' when nothing fits."""
    label, url = label.lower(), url.lower()
    for words, _, icon in CONTACT_ICONS:
        if any(w in label for w in words):
            return icon
    for _, prefixes, icon in CONTACT_ICONS:
        if any(p in url for p in prefixes):
            return icon
    return ""


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


def bullet_text(value: Any, lang: str) -> str:
    """One bullet in one language. Unlike tr(), an explicit empty value hides the bullet in that
    language ({en: '...', zh: ''} = not on the Chinese resume), so each language can keep its own
    number of bullets. Only a missing key falls back to another language."""
    if isinstance(value, dict) and lang in value:
        return str(value.get(lang) or "")
    return tr(value, lang)


_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def _lacks(value: Any, lang: str, bullet: bool = False) -> bool:
    if isinstance(value, dict):
        others = any(v for k, v in value.items() if k != lang)
        return others and (lang not in value if bullet else not value.get(lang))
    if bullet and value:  # one plain string for every language: flag it where it is the wrong script
        return L.is_cjk(lang) != bool(_CJK_RE.search(str(value)))
    return False


def missing_text(entry: Any, lang: str) -> dict:
    """What this entry still lacks in `lang` (it would fall back to another language)."""
    fields = [k for k in ("title", "subtitle", "location", "date") if _lacks(entry.get(k), lang)]
    bullets = [i for i, b in enumerate(entry.get("bullets") or []) if _lacks(b, lang, bullet=True)]
    return {"fields": fields, "bullets": bullets} if fields or bullets else {}


_PRESENT = {"present", "now", "current", "至今", "今"} | {v["present"].lower() for v in L.LANGS.values()}


def fmt_date(value: Any, lang: str) -> str:
    s = tr(value, lang).strip()
    if not s:
        return ""
    if s.lower() in _PRESENT:
        return L.present(lang)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return L.format_month(y, mo, lang)
    return s


def date_range(entry: dict, lang: str) -> str:
    if entry.get("date"):
        return tr(entry["date"], lang)
    start, end = fmt_date(entry.get("start"), lang), fmt_date(entry.get("end"), lang)
    if start and end:
        return f"{start} – {end}"
    return start or end


# ---- lookup --------------------------------------------------------------
KINDS = ("profile", "settings", "sections", "entry", "subset")


def get_item(doc: Any, kind: str, item_id: str | None = None) -> Any:
    if kind in ("profile", "settings", "sections"):
        return doc.get(kind)
    coll = doc.get("entries" if kind == "entry" else "subsets") or []
    for item in coll:
        if str(item.get("id")) == str(item_id):
            return item
    return None


def interview_question(entry: Any, path: list[int]) -> Any:
    """One question of an entry's mock interview: `path` [2, 0] is the first follow-up of the third question."""
    items, q = entry.get("interview") or [], None
    for i in path:
        if not isinstance(i, int) or not 0 <= i < len(items):
            return None
        q = items[i]
        items = q.get("follow_ups") or []
    return q


def interview_path(path: list[int]) -> str:
    """[2, 0] -> interview[2].follow_ups[0], the way Claude finds it in the file."""
    return "interview" + "".join(f"[{i}]" if n == 0 else f".follow_ups[{i}]" for n, i in enumerate(path))


def get_subset(doc: Any, subset_id: str) -> Any:
    """Like get_item, but ALL always exists (a file may not have saved it yet)."""
    v = get_item(doc, "subset", subset_id)
    if v is None and subset_id == ALL_ID:
        return default_all_subset(first_lang(doc))
    return v


def first_lang(doc: Any) -> str:
    """The file's main resume language (the first one in settings)."""
    return str(((doc.get("settings") or {}).get("languages") or ["en"])[0])


def section_order(doc: Any, subset: Any) -> list[str]:
    """Every section id in the order this subset shows them. The order belongs to the subset;
    ids it doesn't list yet (sections added later) go last, in the order of the sections list."""
    ids = [str(s.get("id")) for s in doc.get("sections") or []]
    listed = [str(s) for s in (subset or {}).get("sections") or [] if str(s) in ids]
    return list(dict.fromkeys(listed + ids))


def with_all(subsets: list, lang: str = "en") -> list:
    """Subsets as the UI sees them: ALL first, then the others."""
    rest = [v for v in subsets if not is_all(v)]
    found = next((v for v in subsets if is_all(v)), None)
    return [found if found is not None else default_all_subset(lang), *rest]


def hashes(doc: Any) -> dict:
    return {
        "profile": fingerprint(doc.get("profile")),
        "settings": fingerprint(doc.get("settings")),
        "sections": fingerprint(doc.get("sections")),
        "entry": {str(e.get("id")): fingerprint(e) for e in doc.get("entries") or []},
        "subset": {str(v.get("id")): fingerprint(v) for v in doc.get("subsets") or []},
    }


def next_entry_id(doc: Any) -> str:
    """Internal, stable, section-independent id. The UI never shows it: the sidebar numbers
    entries within their own section instead, so renaming or moving sections changes nothing."""
    nums = [int(m.group(1)) for e in doc.get("entries") or []
            if (m := re.fullmatch(r"EXP-(\d+)", str(e.get("id", "")), re.IGNORECASE))]
    return f"EXP-{(max(nums) + 1) if nums else 1:03d}"


def next_subset_id(doc: Any, taken: Any = ()) -> str:
    """The next SUB-xxx, past every subset's id and any in `taken` (deleted subsets that still have versions)."""
    ids = [str(v.get("id", "")) for v in doc.get("subsets") or []] + [str(x) for x in taken]
    nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"(?:SUB|JOB)-(\d+)", i))]   # JOB-: older files
    return f"SUB-{(max(nums) + 1) if nums else 1:03d}"


def validate(doc: Any) -> list[str]:
    """Human-readable warnings; never blocks saving."""
    warn: list[str] = []
    ids = [str(e.get("id")) for e in doc.get("entries") or []]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        warn.append(t("Duplicate entry ID: {id}", id=dup))
    section_ids = {str(s.get("id")) for s in doc.get("sections") or []}
    for e in doc.get("entries") or []:
        if str(e.get("section")) not in section_ids:
            warn.append(t("{id} is in section “{section}”, which isn't in the sections list", id=e.get("id"), section=e.get("section")))
    known = set(ids)
    for v in doc.get("subsets") or []:
        if is_all(v):
            continue
        for ref in v.get("entries") or []:
            if str(ref) not in known:
                warn.append(t("Subset {job} refers to entry {ref}, which doesn't exist", job=v.get("id"), ref=ref))
    return warn


# ---- view (engine-agnostic) ---------------------------------------------
def build_view(doc: Any, subset: dict) -> dict:
    """Resolve one subset into plain, single-language data for a renderer.

    Text keeps the tiny inline markup (**bold**, *italic*, [text](url)); each
    renderer converts it to its own syntax. The fact layer is dropped here, so no
    template can leak it.
    """
    lang = str(subset.get("lang") or "en")
    layout = dict(DEFAULT_LAYOUT)
    layout.update(to_plain(subset.get("layout") or {}))
    accent = str(layout.get("accent") or "").lstrip("#").upper()
    layout["accent"] = accent if re.fullmatch(r"[0-9A-F]{6}", accent) else DEFAULT_LAYOUT["accent"]
    if layout.get("paper") not in ("a4", "letter"):
        layout["paper"] = "a4"

    everything = is_all(subset)
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    if everything:
        chosen = list(doc.get("entries") or [])
    else:
        chosen = [by_id[str(i)] for i in subset.get("entries") or [] if str(i) in by_id]

    rank = {sid: i for i, sid in enumerate(section_order(doc, subset))}
    sections = sorted(doc.get("sections") or [], key=lambda s: rank[str(s.get("id"))])
    hidden = {str(s) for s in subset.get("hide_sections") or []}

    p = doc.get("profile") or {}
    hidden_contacts = set() if everything else {str(c) for c in subset.get("hide_contacts") or []}
    headline = subset.get("headline") if "headline" in subset else p.get("headline")  # older files
    profile = {
        "name": tr(p.get("name"), lang),
        "headline": tr(headline, lang),
        "contacts": [{"label": tr(c.get("label"), lang), "value": tr(c.get("value"), lang),
                      "url": tr(c.get("url"), lang),
                      "icon": contact_icon(tr(c.get("label"), lang), tr(c.get("url"), lang))}
                     for c in p.get("contacts") or []
                     if str(c.get("label") or "") not in hidden_contacts],
    }

    out_sections = []
    ref = 0
    for s in sections:
        sid = str(s.get("id"))
        if sid in hidden:
            continue
        items = []
        for e in chosen:
            if str(e.get("section")) != sid:
                continue
            kept = [(i, bullet_text(b, lang)) for i, b in enumerate(e.get("bullets") or [])]
            kept = [(i, t) for i, t in kept if t.strip()]
            items.append({
                "id": str(e.get("id")),
                "ref": ref,                       # renderers report per-bullet line counts as ref.index
                "title": tr(e.get("title"), lang),
                "subtitle": tr(e.get("subtitle"), lang),
                "location": tr(e.get("location"), lang),
                "dates": date_range(e, lang),
                "link": tr(e.get("link"), lang),
                "tech": [tr(t, lang) for t in e.get("tech") or [] if tr(t, lang)],
                "bullets": [t for _, t in kept],
                "bidx": [i for i, _ in kept],     # each shown bullet's index in the entry's list
            })
            ref += 1
        if items:
            out_sections.append({"id": sid, "title": tr(s.get("title"), lang),
                                 "kind": str(s.get("kind") or "timeline"), "entries": items})

    missing = []
    for e in chosen:
        if str(e.get("section")) in hidden or not any(str(s.get("id")) == str(e.get("section")) for s in sections):
            continue
        gap = missing_text(e, lang)
        if gap:
            missing.append({"id": str(e.get("id")), **gap})

    return {"lang": lang, "profile": profile, "sections": out_sections, "layout": layout,
            "template": str(subset.get("template") or "classic"), "missing": missing}
