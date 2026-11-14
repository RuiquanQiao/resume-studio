"""The skill catalog: every skill the library names, as one record each, with how well the user really has it.

An entry's `tech` lists skills by name (the visible layer: what the resume says, version and all, "Next.js 16").
Each name is an instance of one skill of the catalog, found by its key: the name folded, without a trailing
version ("Next.js 16", "next.js" and "Next.js" are one skill), or one of a record's `aliases`. The catalog is
every skill some entry names, plus the records in the file's top-level `skills:` list, which hold what only the
fact layer knows:

    skills:                       # never rendered
      - name: Next.js
        aliases: [NextJS]
        level: none               # strong | none (proficient / can't use yet)
        by: user                  # user: picked by the user; ai: an AI reading of the fact layer the user accepted
        why: ...                  # where the level comes from, one line
        suggest: {level: none, why: ...}    # an AI reading waiting for the user to accept or dismiss

A list section (专业技能) shows, in each subset, the skills of a line that one of its chosen entries backs (names
in its `tech`); a skill no entry of the library backs (coursework, self-taught) shows everywhere. A subset can
show any other one (`show_skills`) or hide one (`hide_skills`), both by skill key.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Two levels (2026-11-15): a skill the user could explain and build on alone, or one they can't use yet. "Can use
# with the docs" and "familiar" were ladders between the two that a job never asks about; files that have them read
# them as `none` (two_levels rewrites them).
LEVELS = ("strong", "none")
OLD_LEVELS = {"working": "none", "basic": "none"}
WEIGHT = {"strong": 1.0, "none": 0.0}   # how much of a job's ask a level meets

_VERSION = re.compile(r"\s+v?\d+(?:\.\d+)*[a-z]?$", re.IGNORECASE)


def _text(value: Any) -> list[str]:
    """Every language's text of a plain-or-per-language value."""
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    return [str(value)] if value not in (None, "") else []


def base_name(name: str) -> str:
    """The name without a trailing version: "Next.js 16" -> "Next.js", "Tauri 2" -> "Tauri"."""
    s = " ".join(unicodedata.normalize("NFKC", str(name)).split())
    stripped = _VERSION.sub("", s)
    return stripped if stripped else s


def key(name: Any) -> str:
    """One skill's key: case, width and spacing folded, a trailing version dropped."""
    return base_name(str(name)).casefold()


def is_list(doc: Any, entry: Any) -> bool:
    """An entry of a list section (专业技能): its `tech` are the section's items, not what a project used."""
    sid = str(entry.get("section"))
    return any(str(s.get("id")) == sid and str(s.get("kind") or "") == "list" for s in doc.get("sections") or [])


def two_levels(doc: Any) -> bool:
    """Levels and AI readings of the four-level days made one of the two. True when something was rewritten."""
    changed = False
    for r in doc.get("skills") or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("level") or "") in OLD_LEVELS:
            r["level"] = OLD_LEVELS[str(r["level"])]
            changed = True
        s = r.get("suggest")
        if isinstance(s, dict) and str(s.get("level") or "") in OLD_LEVELS:
            s["level"] = OLD_LEVELS[str(s["level"])]
            changed = True
    return changed


def records(doc: Any) -> list:
    return [r for r in (doc.get("skills") or []) if isinstance(r, dict) and str(r.get("name") or "").strip()]


def _alias_map(doc: Any) -> dict[str, str]:
    """Any key (a record's name or alias) -> the record's key."""
    out: dict[str, str] = {}
    for r in records(doc):
        k = key(r["name"])
        out.setdefault(k, k)
        for a in r.get("aliases") or []:
            out.setdefault(key(a), k)
    return out


def resolver(doc: Any):
    """name -> the key of the skill it is an instance of."""
    aliases = _alias_map(doc)
    return lambda name: aliases.get(key(name), key(name))


def item_keys(doc: Any, item: Any, of=None) -> set[str]:
    of = of or resolver(doc)
    return {of(x) for x in _text(item)}


def backed(doc: Any, chosen: list) -> set[str]:
    """Keys of the skills these entries use (their `tech`, list sections left out)."""
    of = resolver(doc)
    out: set[str] = set()
    for e in chosen:
        if not is_list(doc, e):
            for item in e.get("tech") or []:
                out |= item_keys(doc, item, of)
    return out


def visible(doc: Any, subset: Any, entry: Any, chosen: list, everywhere: set[str] | None = None) -> list:
    """The items of a list entry this subset shows. `chosen`: the subset's entries; `everywhere`: backed() of the
    whole library (worked out once by a caller that asks for many entries)."""
    items = list(entry.get("tech") or [])
    if not subset or str(subset.get("id")) == "ALL":
        return items
    of = resolver(doc)
    here = backed(doc, chosen)
    anywhere = everywhere if everywhere is not None else backed(doc, list(doc.get("entries") or []))
    shown = {of(x) for x in subset.get("show_skills") or []}
    hidden = {of(x) for x in subset.get("hide_skills") or []}
    out = []
    for item in items:
        ks = item_keys(doc, item, of)
        if ks & hidden:
            continue
        if ks & shown or ks & here or not ks & anywhere:
            out.append(item)
    return out


def why_hidden(doc: Any, subset: Any, item: Any, chosen: list) -> str:
    """'' when a list item shows in this subset, else why not: "hidden" (the user hid it) or "unbacked" (it is
    backed elsewhere in the library, by no entry this subset picks)."""
    if not subset or str(subset.get("id")) == "ALL":
        return ""
    of = resolver(doc)
    ks = item_keys(doc, item, of)
    if ks & {of(x) for x in subset.get("hide_skills") or []}:
        return "hidden"
    if ks & {of(x) for x in subset.get("show_skills") or []} or ks & backed(doc, chosen):
        return ""
    return "unbacked" if ks & backed(doc, list(doc.get("entries") or [])) else ""


def catalog(doc: Any) -> list[dict]:
    """Every skill: [{key, name, aliases, level, by, why, suggest, used_in: [entry ids], lines: [list entry ids]}],
    in the order the library first names them, then records no entry names."""
    of = resolver(doc)
    recs = {key(r["name"]): r for r in records(doc)}
    out: dict[str, dict] = {}

    def add(k: str, name: str) -> dict:
        if k not in out:
            r = recs.get(k) or {}
            out[k] = {"key": k, "name": str(r.get("name") or base_name(name)), "aliases": [str(a) for a in r.get("aliases") or []],
                      "level": str(r.get("level") or "") if str(r.get("level") or "") in LEVELS else "",
                      "by": str(r.get("by") or ""), "why": str(r.get("why") or ""),
                      "suggest": _suggestion(r.get("suggest")), "used_in": [], "lines": [], "names": []}
        return out[k]

    for e in doc.get("entries") or []:
        lst = is_list(doc, e)
        for item in e.get("tech") or []:
            for name in _text(item):
                rec = add(of(name), name)
                if name not in rec["names"]:
                    rec["names"].append(name)
                (rec["lines"] if lst else rec["used_in"]).append(str(e.get("id")))
    for k, r in recs.items():
        add(k, str(r["name"]))
    for rec in out.values():
        rec["used_in"] = list(dict.fromkeys(rec["used_in"]))
        rec["lines"] = list(dict.fromkeys(rec["lines"]))
    return list(out.values())


def _suggestion(s: Any) -> dict | None:
    if isinstance(s, dict) and str(s.get("level") or "") in LEVELS:
        return {"level": str(s["level"]), "why": str(s.get("why") or "")}
    return None


def levels(doc: Any) -> dict[str, dict]:
    """key (and every alias key) -> {name, level} of every skill of the catalog; level '' when not set yet."""
    out: dict[str, dict] = {}
    for r in catalog(doc):
        v = {"name": r["name"], "level": r["level"]}
        out[r["key"]] = v
        for a in r["aliases"]:
            out.setdefault(key(a), v)
    return out


# ---- writing (on a document the caller saves) ----------------------------------
def record(doc: Any, name: str, create: bool = True) -> Any:
    """The record of the skill `name` is an instance of; a new one (named without the version) when `create`."""
    k = resolver(doc)(name)
    for r in records(doc):
        if key(r["name"]) == k:
            return r
    if not create:
        return None
    if doc.get("skills") is None:
        doc["skills"] = []
    doc["skills"].append({"name": base_name(name)})
    return doc["skills"][-1]


def set_level(doc: Any, name: str, level: str, by: str = "user", why: str | None = None) -> None:
    """Set (or with level '' clear) how well the user has a skill. A pending AI reading of it is settled by it."""
    if level and level not in LEVELS:
        raise ValueError(f"unknown level {level}")
    r = record(doc, name, create=bool(level))
    if r is None:
        return
    if level:
        r["level"] = level
        r["by"] = by
        if why is not None:
            if why:
                r["why"] = why
            else:
                r.pop("why", None)
    else:
        for k in ("level", "by", "why"):
            r.pop(k, None)
    r.pop("suggest", None)
    _prune(doc)


def settle(doc: Any, name: str, accept: bool) -> None:
    """Accept or dismiss the AI's pending reading of one skill."""
    r = record(doc, name, create=False)
    if r is None or not isinstance(r.get("suggest"), dict):
        return
    s = _suggestion(r.get("suggest"))
    r.pop("suggest", None)
    if accept and s:
        set_level(doc, name, s["level"], by="ai", why=s["why"])
    _prune(doc)


def _prune(doc: Any) -> None:
    """A record that says nothing (a name only) is dropped; the list goes when it is empty."""
    lst = doc.get("skills")
    if lst is None:
        return
    for i in range(len(lst) - 1, -1, -1):
        r = lst[i]
        if isinstance(r, dict) and not (set(r.keys()) - {"name"}):
            del lst[i]
    if not lst:
        del doc["skills"]


def pin(doc: Any, subset: Any, entry: Any, want: list, chosen: list) -> bool:
    """Make the list entry show exactly `want` (a subset of its items) in this subset, by adding to its
    show_skills / hide_skills only what the rule alone wouldn't give. True when something was added."""
    of = resolver(doc)
    now = visible(doc, subset, entry, chosen)
    wanted = {k for w in want for k in item_keys(doc, w, of)}
    changed = False
    for item in entry.get("tech") or []:
        ks = item_keys(doc, item, of)
        if not ks:
            continue
        name = base_name(_text(item)[0])
        if ks & wanted and item not in now:
            subset.setdefault("show_skills", [])
            if name not in subset["show_skills"]:
                subset["show_skills"].append(name)
                changed = True
        elif not ks & wanted and item in now:
            subset.setdefault("hide_skills", [])
            if name not in subset["hide_skills"]:
                subset["hide_skills"].append(name)
                changed = True
    return changed


def set_use(doc: Any, name: str, entry: Any, on: bool) -> bool:
    """Name the skill in this entry's `tech` (at the end, as `name` is written), or take every item that is an
    instance of it out. True when the entry changed."""
    of = resolver(doc)
    k = of(name)
    tech = entry.get("tech")
    has = [i for i, item in enumerate(tech or []) if k in item_keys(doc, item, of)]
    if on and not has:
        if tech is None:
            entry["tech"] = []
        entry["tech"].append(str(name).strip())
        return True
    if not on and has:
        for i in reversed(has):
            del entry["tech"][i]
        return True
    return False


def set_shown(doc: Any, subset: Any, name: str, on: bool) -> bool:
    """Show or hide the skill on this subset's list lines. Only a choice the rule alone wouldn't make is kept in
    show_skills / hide_skills; a choice back to the rule takes it out of both. True when the subset changed."""
    of = resolver(doc)
    k = of(name)
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    chosen = [by_id[str(i)] for i in subset.get("entries") or [] if str(i) in by_id]
    rule = k in backed(doc, chosen) or k not in backed(doc, list(doc.get("entries") or []))
    changed = False
    for field in ("show_skills", "hide_skills"):
        lst = subset.get(field)
        if lst is None:
            continue
        for i in range(len(lst) - 1, -1, -1):
            if of(lst[i]) == k:
                del lst[i]
                changed = True
        if not lst:
            del subset[field]
    if on != rule:
        field = "show_skills" if on else "hide_skills"
        if subset.get(field) is None:
            subset[field] = []
        subset[field].append(base_name(str(name)))
        changed = True
    return changed


# ---- the comma bug of older files ------------------------------------------------
def rejoin(items: list) -> list | None:
    """Items an older editor split at a comma inside brackets ("Python (NumPy", "SciPy)") joined again; None when
    nothing needed it."""
    out: list = []
    changed = False
    for item in items:
        if isinstance(item, str) and out and isinstance(out[-1], str) and _open(out[-1]) > 0:
            out[-1] = f"{out[-1]}, {item}"
            changed = True
        else:
            out.append(item)
    return out if changed else None


def _open(s: str) -> int:
    return s.count("(") + s.count("（") - s.count(")") - s.count("）")
