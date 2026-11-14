"""Versions of a subset: what it said at a moment the user chose, kept like commits.

A subset's resume depends on the shared library (entries, profile, section titles), so a version
freezes everything the subset renders: its own settings plus the visible layer of the entries it
picks, in every resume language. The fact layer (notes, evidence) and the mock interview are left
out: they are private and never part of what an employer got. The subset's name (`label`) and its
linked postings are kept but don't count as a change: renaming a subset doesn't make a new version.

Versions live next to the data file, one JSON file per subset in `resume-studio-data/subsets/`, oldest first,
numbered 1, 2, 3… They stay when a subset is deleted (an application may still point at one).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.i18n import t
from core.store import fingerprint, merge_into, to_plain

from . import model, skills

FACT_KEYS = ("notes", "evidence", "interview")
SKILL_PICKS = ("show_skills", "hide_skills")   # live in the subset, kept in a version as what they made it show
UNCOUNTED = ("id", "label", "postings", "posting")   # kept with a version, but not what makes it different


def _visible(item: Any) -> dict:
    return {k: v for k, v in to_plain(item or {}).items() if k not in FACT_KEYS}


def content(doc: Any, subset: Any) -> dict:
    """What `subset` renders right now, independent of the rest of the file."""
    sub = to_plain(subset)
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    picked = [by_id[str(i)] for i in sub.get("entries") or [] if str(i) in by_id]
    chosen = [_visible(e) for e in picked]
    used = {str(e.get("section")) for e in chosen}
    c = {"subset": {k: v for k, v in sub.items() if k not in ("postings", "posting", *SKILL_PICKS)},
         "profile": _visible(doc.get("profile")),
         "sections": [to_plain(s) for s in doc.get("sections") or [] if str(s.get("id")) in used],
         "entries": chosen}
    # a list section shows only the skills the chosen entries back (resume/skills.py): what each of its lines
    # shows is kept, when it isn't every item, since the version holds the chosen entries and not the library
    everywhere = skills.backed(doc, list(doc.get("entries") or []))
    shown = {}
    for e in picked:
        if skills.is_list(doc, e):
            items = to_plain(skills.visible(doc, sub, e, picked, everywhere))
            if items != to_plain(e.get("tech") or []):
                shown[str(e.get("id"))] = items
    if shown:
        c["skills_shown"] = shown
    return c


def digest(c: dict) -> str:
    return fingerprint({**c, "subset": {k: v for k, v in c["subset"].items() if k not in UNCOUNTED}})


def shape(c: dict) -> list:
    """What a version's major number is made of: which entries it picks, in what order, and how its sections
    are ordered and hidden."""
    s = c.get("subset") or {}
    used = {str(e.get("section")) for e in c.get("entries") or []}
    hidden = {str(x) for x in s.get("hide_sections") or []}
    order = [i for i in model.section_order({"sections": c.get("sections") or []}, s) if i in used and i not in hidden]
    return [[str(x) for x in s.get("entries") or []], order]


# Numbers are given only to what went out, and nobody types them (2026-11-15). The major number moves when the
# shape (entries, their order, the sections' order) differs from the last one sent; the minor one counts the
# different wordings sent under that major: v2.3 is the third. A send that says what an earlier one said is
# that earlier version again. A copy kept without being sent (before going back to a version) has no number.
def sent_order(vs: list[dict]) -> list[dict]:
    """The versions that went out, in the order they first went out (a copy sent later comes last)."""
    return sorted((v for v in vs if v.get("sent")), key=lambda v: (str(v.get("sent_at") or v.get("at") or ""), v["n"]))


def names(vs: list[dict]) -> dict[int, str]:
    out, major, minor, prev = {}, 0, 0, None
    for v in sent_order(vs):
        sh = shape(v.get("content") or {})
        if prev is None or sh != prev:
            major, minor = major + 1, 0
        minor += 1
        out[v["n"]] = f"{major}.{minor}"
        prev = sh
    return out


def next_name(vs: list[dict], c: dict, same: int | None = None) -> str:
    """The number `c` goes out as: the sent version it says exactly, or the next one."""
    named = names(vs)
    if same is not None and same in named:
        return named[same]
    sent = sent_order(vs)
    if not sent:
        return "1.1"
    major, _, minor = named[sent[-1]["n"]].partition(".")
    if shape(sent[-1].get("content") or {}) != shape(c):
        return f"{int(major) + 1}.1"
    return f"{major}.{int(minor) + 1}"


def as_doc(c: dict) -> dict:
    """A version as a data file of its own, so the normal view and renderer can show it."""
    sub = {**c["subset"], "_shown": c.get("skills_shown") or {}}
    return {"profile": c["profile"], "sections": c["sections"], "entries": c["entries"], "subsets": [sub]}


class Versions:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "subsets"
        self.lock = threading.RLock()

    @staticmethod
    def _safe(subset_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", subset_id) or "subset"

    def _path(self, subset_id: str) -> Path:
        return self.dir / f"{self._safe(subset_id)}.json"

    def _read(self, subset_id: str) -> dict:
        p = self._path(subset_id)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"id": subset_id, "versions": []}
        return data if isinstance(data, dict) and isinstance(data.get("versions"), list) else {"id": subset_id, "versions": []}

    def _write(self, subset_id: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self._path(subset_id)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        os.replace(tmp, p)

    # ---- reading ---------------------------------------------------------
    def all(self, subset_id: str) -> list[dict]:
        return self._read(subset_id)["versions"]

    def get(self, subset_id: str, n: int) -> dict | None:
        return next((v for v in self.all(subset_id) if v.get("n") == n), None)

    def ids(self) -> set[str]:
        """Every subset id that has versions, including subsets deleted since."""
        out = set()
        if self.dir.is_dir():
            for p in self.dir.glob("*.json"):
                try:
                    out.add(str(json.loads(p.read_text(encoding="utf-8")).get("id") or p.stem))
                except (OSError, ValueError):
                    out.add(p.stem)
        return out

    def status(self, subset_id: str, current: dict) -> dict:
        """Where the subset stands: its newest version, whether it changed since, and which version
        it says exactly (`same_as`: after going back to v1, it is v1 again, though v2 is newer)."""
        vs = self.all(subset_id)
        last = vs[-1] if vs else None
        h = digest(current)
        same = next((v["n"] for v in reversed(vs) if v.get("hash") == h), None)
        sent = sent_order(vs)
        sent_same = next((v["n"] for v in reversed(sent) if v.get("hash") == h), None)
        # `latest` / `same_as` are running numbers (what an application keeps); `names` are what the window shows
        # for the sent ones (v2.1); `next` is what the subset goes out as now; `last_sent` the last one that went out
        return {"count": len(vs), "latest": last["n"] if last else None,
                "changed": last is None or last.get("hash") != h, "same_as": same,
                "sent_same": sent_same, "last_sent": sent[-1]["n"] if sent else None,
                "last_sent_at": (sent[-1].get("sent_at") or sent[-1].get("at")) if sent else None,
                "names": {str(k): x for k, x in names(vs).items()}, "next": next_name(vs, current, sent_same)}

    def name(self, subset_id: str, n: int) -> str:
        return names(self.all(subset_id)).get(int(n), "")

    @staticmethod
    def summary(v: dict, name: str | None = None) -> dict:
        return {**{k: v.get(k) for k in ("n", "at", "note", "hash", "auto", "sent")}, "name": name or ""}

    # ---- writing ---------------------------------------------------------
    def commit(self, subset_id: str, c: dict, note: str = "", auto: bool = False, sent: bool = False) -> tuple[dict, bool]:
        """Keep `c` as the next version, unless it is the same as the newest one (or, when it goes out, as any
        one: going back to v1 and sending it sends v1). `sent` marks it as gone out. (version, created)"""
        with self.lock:
            data = self._read(subset_id)
            vs = data["versions"]
            h = digest(c)
            same = next((v for v in reversed(vs) if v.get("hash") == h), None)
            if same is not None and (same is vs[-1] or auto or sent):   # nothing new
                if sent and not same.get("sent"):
                    same["sent"], same["sent_at"] = True, time.strftime("%Y-%m-%dT%H:%M:%S")
                    self._write(subset_id, data)
                return same, False
            v = {"n": (vs[-1]["n"] + 1) if vs else 1, "hash": h, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "note": note.strip(), "content": c}
            if auto:
                v["auto"] = True
            if sent:
                v["sent"], v["sent_at"] = True, v["at"]
            vs.append(v)
            data["id"] = subset_id
            self._write(subset_id, data)
            return v, True

    def mark_sent(self, subset_id: str, sends: dict[int, str]) -> bool:
        """Versions an application records as sent ({n: when}), from before versions knew it themselves."""
        with self.lock:
            data = self._read(subset_id)
            changed = False
            for v in data["versions"]:
                if v.get("n") in sends and not v.get("sent"):
                    v["sent"], v["sent_at"] = True, sends[v["n"]] or v.get("at")
                    changed = True
            if changed:
                self._write(subset_id, data)
            return changed

    def set_note(self, subset_id: str, n: int, note: str) -> dict:
        with self.lock:
            data = self._read(subset_id)
            v = next((x for x in data["versions"] if x.get("n") == n), None)
            if v is None:
                raise KeyError(t("Version {n} doesn't exist", n=n))
            v["note"] = note.strip()
            v.pop("auto", None)
            self._write(subset_id, data)
            return v

    def rename(self, old: str, new: str) -> None:
        """A renamed subset keeps its history under the new id."""
        with self.lock:
            src = self._path(old)
            if not src.exists() or self._path(new) == src:
                return
            data = self._read(old)
            data["id"] = new
            for v in data["versions"]:   # each version renders (and comes back) under the new id
                (v.get("content") or {}).get("subset", {})["id"] = new
            self._write(new, data)
            src.unlink()


# ---- comparing two versions (or a version and now) ---------------------------
def _lines(label: str, value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        return [f"{label} · {lang}: {text}" for lang, text in value.items() if text not in (None, "")]
    if isinstance(value, list):
        return [f"{label}: " + ", ".join(model.tr(x, "en") if isinstance(x, dict) else str(x) for x in value)]
    return [f"{label}: {value}"]


def entry_text(e: dict) -> str:
    out: list[str] = []
    for key, label in (("title", t("Name")), ("subtitle", t("Subtitle")), ("location", t("Location")),
                       ("start", t("Start")), ("end", t("End")), ("link", t("Link"))):
        out += _lines(label, e.get(key))
        if key == "end" and e.get("expected"):
            out.append(t("End: expected"))
    if e.get("tech"):
        out.append(f"{t('Tech')}: " + ", ".join(model.tr(x, "en") for x in e["tech"]))
    for b in e.get("bullets") or []:
        if isinstance(b, dict):
            out += [f"• [{lang}] {text if text != '' else t('(hidden)')}" for lang, text in b.items()]
        else:
            out.append(f"• {b}")
    return "\n".join(out)


def subset_text(c: dict) -> str:
    s, out = c["subset"], []
    out += _lines(t("Headline / job target"), s.get("headline"))
    out += _lines(t("Resume language"), s.get("lang"))
    out += _lines(t("Template"), s.get("template"))
    titles = {str(x.get("id")): x.get("title") for x in c.get("sections") or []}
    order = model.section_order({"sections": c.get("sections") or []}, s)
    if order:
        out.append(f"{t('Sections')}: " + " → ".join(model.tr(titles.get(i), s.get("lang") or "en") or i for i in order))
    for key, label in (("hide_sections", t("Hidden sections")), ("hide_contacts", t("Hidden contacts"))):
        out += _lines(label, s.get(key))
    for k, v in sorted((s.get("layout") or {}).items()):
        out.append(f"{t('Layout')} · {k}: {v}")
    return "\n".join(out)


def profile_text(c: dict) -> str:
    p = c.get("profile") or {}
    out = _lines(t("Name"), p.get("name"))
    for x in p.get("contacts") or []:
        label = model.tr(x.get("label"), "en")
        out.append(f"{label}: {model.tr(x.get('value'), 'en')}" + (f" ({model.tr(x.get('url'), 'en')})" if x.get("url") else ""))
    return "\n".join(out)


def compare(old: dict | None, new: dict) -> list[dict]:
    """What differs, as items with a before / after text each (the window shows them as a diff)."""
    lang = str(new["subset"].get("lang") or "en")
    items = []
    a, b = (subset_text(old) if old else ""), subset_text(new)
    if a != b:
        items.append({"kind": "subset", "id": str(new["subset"].get("id")), "name": t("Subset settings"),
                      "status": "changed" if old else "added", "before": a, "after": b})
    a, b = (profile_text(old) if old else ""), profile_text(new)
    if a != b:
        items.append({"kind": "profile", "id": "profile", "name": t("Profile"),
                      "status": "changed" if old else "added", "before": a, "after": b})
    def shown(c: dict | None) -> dict:   # a list line as the version showed it
        s = (c or {}).get("skills_shown") or {}
        return {str(e.get("id")): ({**e, "tech": s[str(e.get("id"))]} if str(e.get("id")) in s else e)
                for e in (c or {}).get("entries") or []}

    before, after = shown(old), shown(new)
    for eid, e in after.items():
        prev = before.get(eid)
        a, b = (entry_text(prev) if prev else ""), entry_text(e)
        if a != b or prev is None:
            items.append({"kind": "entry", "id": eid, "name": model.tr(e.get("title"), lang) or eid,
                          "status": "changed" if prev else "added", "before": a, "after": b})
    for eid, e in before.items():
        if eid not in after:
            items.append({"kind": "entry", "id": eid, "name": model.tr(e.get("title"), lang) or eid,
                          "status": "removed", "before": entry_text(e), "after": ""})
    return items


# ---- going back to a version --------------------------------------------------
def impact(doc: Any, c: dict) -> dict:
    """What going back to `c` changes beyond the subset itself: the shared things other subsets see."""
    sid = str(c["subset"].get("id"))
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    changed = [e for e in c["entries"] if str(e.get("id")) not in by_id or _visible(by_id[str(e.get("id"))]) != e]
    ids = {str(e.get("id")) for e in changed}
    others = [str(v.get("label") or v.get("id")) for v in doc.get("subsets") or []
              if str(v.get("id")) != sid and not model.is_all(v) and ids & {str(x) for x in v.get("entries") or []}]
    lang = str(c["subset"].get("lang") or "en")
    current_titles = {str(s.get("id")): to_plain(s) for s in doc.get("sections") or []}
    return {"entries": [model.tr(e.get("title"), lang) or str(e.get("id")) for e in changed],
            "back": [model.tr(e.get("title"), lang) or str(e.get("id")) for e in c["entries"] if str(e.get("id")) not in by_id],
            "others": others,
            "profile": _visible(doc.get("profile")) != c["profile"],
            "sections": any(current_titles.get(str(s.get("id"))) != s for s in c["sections"])}


def restore(doc: Any, c: dict) -> None:
    """Make the file render `c` again (in place, so comments survive): the subset's own settings, the
    visible layer of its entries (re-adding any deleted since), the profile and its sections' titles.
    The fact layer, the subset's name and its postings stay as they are now; nothing else is removed."""
    sid = str(c["subset"].get("id"))
    sub = model.get_item(doc, "subset", sid)
    data = {**c["subset"]}
    if sub is None:
        doc.setdefault("subsets", []).append(data)
    else:
        for k in ("label", "postings", "posting"):
            if k in sub:
                data[k] = to_plain(sub[k])
            else:
                data.pop(k, None)
        merge_into(sub, data)
    if doc.get("entries") is None:
        doc["entries"] = []
    for e in c["entries"]:
        cur = model.get_item(doc, "entry", str(e.get("id")))
        if cur is None:
            doc["entries"].append({**e, "notes": "", "evidence": []})
        else:
            merge_into(cur, {**e, **{k: to_plain(cur[k]) for k in FACT_KEYS if k in cur}})
    # its list lines show again what they showed then, whatever the subset's entries back now
    sub = model.get_item(doc, "subset", sid)
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    picked = [by_id[str(i)] for i in sub.get("entries") or [] if str(i) in by_id]
    for k in SKILL_PICKS:
        sub.pop(k, None)
    for e in c["entries"]:
        cur = by_id.get(str(e.get("id")))
        if cur is not None and skills.is_list(doc, cur):
            skills.pin(doc, sub, cur, (c.get("skills_shown") or {}).get(str(e.get("id")), list(e.get("tech") or [])), picked)
    prof = doc.get("profile")
    if prof is None:
        doc["profile"] = dict(c["profile"])
    else:
        merge_into(prof, {**c["profile"], **{k: to_plain(prof[k]) for k in FACT_KEYS if k in prof}})
    if doc.get("sections") is None:
        doc["sections"] = []
    for s in c["sections"]:
        cur = next((x for x in doc["sections"] if str(x.get("id")) == str(s.get("id"))), None)
        if cur is None:
            doc["sections"].append(dict(s))
        else:
            merge_into(cur, dict(s))
