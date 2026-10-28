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

from . import model

FACT_KEYS = ("notes", "evidence", "interview")
UNCOUNTED = ("id", "label", "postings", "posting")   # kept with a version, but not what makes it different


def _visible(item: Any) -> dict:
    return {k: v for k, v in to_plain(item or {}).items() if k not in FACT_KEYS}


def content(doc: Any, subset: Any) -> dict:
    """What `subset` renders right now, independent of the rest of the file."""
    sub = to_plain(subset)
    by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
    chosen = [_visible(by_id[str(i)]) for i in sub.get("entries") or [] if str(i) in by_id]
    used = {str(e.get("section")) for e in chosen}
    return {"subset": {k: v for k, v in sub.items() if k not in ("postings", "posting")},
            "profile": _visible(doc.get("profile")),
            "sections": [to_plain(s) for s in doc.get("sections") or [] if str(s.get("id")) in used],
            "entries": chosen}


def digest(c: dict) -> str:
    return fingerprint({**c, "subset": {k: v for k, v in c["subset"].items() if k not in UNCOUNTED}})


def as_doc(c: dict) -> dict:
    """A version as a data file of its own, so the normal view and renderer can show it."""
    return {"profile": c["profile"], "sections": c["sections"], "entries": c["entries"], "subsets": [c["subset"]]}


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
        return {"count": len(vs), "latest": last["n"] if last else None,
                "changed": last is None or last.get("hash") != h, "same_as": same}

    @staticmethod
    def summary(v: dict) -> dict:
        return {k: v.get(k) for k in ("n", "at", "note", "hash", "auto")}

    # ---- writing ---------------------------------------------------------
    def commit(self, subset_id: str, c: dict, note: str = "", auto: bool = False) -> tuple[dict, bool]:
        """Keep `c` as the next version, unless it is the same as the newest one. (version, created)"""
        with self.lock:
            data = self._read(subset_id)
            vs = data["versions"]
            h = digest(c)
            same = next((v for v in reversed(vs) if v.get("hash") == h), None)
            if same is not None and (same is vs[-1] or auto):   # nothing new (going back needs no copy of a kept one)
                return same, False
            v = {"n": (vs[-1]["n"] + 1) if vs else 1, "hash": h, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "note": note.strip(), "content": c}
            if auto:
                v["auto"] = True
            vs.append(v)
            data["id"] = subset_id
            self._write(subset_id, data)
            return v, True

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
                       ("start", t("Start")), ("end", t("End")), ("date", t("Date")), ("link", t("Link"))):
        out += _lines(label, e.get(key))
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
    before = {str(e.get("id")): e for e in (old or {}).get("entries") or []}
    after = {str(e.get("id")): e for e in new.get("entries") or []}
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
