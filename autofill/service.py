"""Per data file: the user's sites, and per site the local copy of its profile, what the site said at
the last sync, the recorded read route, which resume subset it is compared with, and how pushing
went. Kept in `resume-studio-data/autofill/` next to the data file (personal data: git-ignored like the rest of
`resume-studio-data`)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from resume import model
from resume.service import Studio

from . import profile, route, sites, sync
from .sites import Site

APPLY_STATES = ("doing", "done", "failed", "skipped")
SITE_FIELDS = ("name", "home", "lang", "notes")   # what the user can change on any site in the list


class Autofill:
    def __init__(self, studio: Studio):
        self.studio = studio
        self.root = studio.store.state_dir / "autofill"
        self.lock = threading.RLock()

    def _path(self, site_id: str) -> Path:
        return self.root / f"{site_id}.json"

    # ---- the user's sites (`sites.json`; a file that never chose has the DEFAULT ones) -----------
    def _records(self) -> list[dict]:
        try:
            data = json.loads((self.root / "sites.json").read_text(encoding="utf-8"))
            return [r for r in data.get("sites") or [] if isinstance(r, dict)]
        except (OSError, ValueError, AttributeError):
            return [{"id": p, "preset": p} for p in sites.DEFAULT]

    def _write_records(self, recs: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "sites.tmp"
        tmp.write_text(json.dumps({"sites": recs}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.root / "sites.json")

    def sites(self) -> list[Site]:
        with self.lock:
            return [s for s in (sites.build(r) for r in self._records()) if s is not None]

    def site(self, site_id: str) -> Site | None:
        return next((s for s in self.sites() if s.id == site_id), None)

    def presets(self) -> list[dict]:
        """The sites Resume Studio has a guide for, and whether the list has them already."""
        have = {str(r.get("preset") or "") for r in self._records()}
        return [{"id": p.id, "name": p.name, "home": p.home, "lang": p.lang, "added": p.id in have}
                for p in sites.PRESETS.values()]

    def add_site(self, *, preset: str = "", name: str = "", home: str = "", lang: str = "", notes: str = "") -> Site:
        with self.lock:
            recs = self._records()
            taken = {str(r.get("id")) for r in recs} | {"chat", "sites"}   # those are routes of their own
            if preset:
                if preset not in sites.PRESETS:
                    raise ValueError(f"unknown preset {preset!r}")
                base = preset
            else:
                home = home.strip()
                if "://" not in home and "." in home:
                    home = "https://" + home
                if not sites.valid_home(home):
                    raise ValueError("home")
                base = sites.slug(name, home)
            sid, n = base, 2
            # a site removed earlier keeps its read and ticks under its id: adding it again brings them back
            while sid in taken:
                sid, n = f"{base}-{n}", n + 1
            rec: dict[str, Any] = {"id": sid}
            if preset:
                rec["preset"] = preset
            for k, v in (("name", name), ("home", home), ("lang", lang), ("notes", notes)):
                if v.strip():
                    rec[k] = v.strip()
            recs.append(rec)
            self._write_records(recs)
            return sites.build(rec)

    def edit_site(self, site_id: str, **fields: str) -> Site:
        with self.lock:
            recs = self._records()
            rec = next((r for r in recs if r.get("id") == site_id), None)
            if rec is None:
                raise KeyError(site_id)
            for k, v in fields.items():
                if k not in SITE_FIELDS or v is None:
                    continue
                v = str(v).strip()
                if k == "home" and v:
                    if "://" not in v and "." in v:
                        v = "https://" + v
                    if not sites.valid_home(v):
                        raise ValueError("home")
                preset = sites.PRESETS.get(str(rec.get("preset") or ""))
                if not v and (preset is None and k == "home"):
                    raise ValueError("home")
                if v and not (preset is not None and getattr(preset, k, None) == v):
                    rec[k] = v
                else:
                    rec.pop(k, None)   # back to the preset's own
            self._write_records(recs)
            return sites.build(rec)

    def remove_site(self, site_id: str) -> None:
        """Off the list. What was read there stays in its file, for when it comes back."""
        with self.lock:
            recs = self._records()
            if not any(r.get("id") == site_id for r in recs):
                raise KeyError(site_id)
            self._write_records([r for r in recs if r.get("id") != site_id])

    def move_site(self, site_id: str, index: int) -> None:
        with self.lock:
            recs = self._records()
            rec = next((r for r in recs if r.get("id") == site_id), None)
            if rec is None:
                raise KeyError(site_id)
            recs.remove(rec)
            recs.insert(max(0, min(index, len(recs))), rec)
            self._write_records(recs)

    def learn(self, site: Site, shape: Any) -> tuple[Site, list[str]]:
        """What a read found out about a site the user added: its sections, fields and limits."""
        if site.preset or shape is None:
            return site, []
        if not isinstance(shape, dict):
            return site, ["`site` must be an object"]
        errs = []
        secs = shape.get("sections")
        if not isinstance(secs, list) or not all(s in sites.ALL_SECTIONS for s in secs):
            errs.append(f"`site.sections` must list which of {', '.join(sites.ALL_SECTIONS)} the site has")
        basics = shape.get("basics", [])
        if not isinstance(basics, list) or not all(b in profile.BASICS for b in basics):
            errs.append(f"`site.basics` must list which of {', '.join(profile.BASICS)} the site has")
        limits = shape.get("limits") or {}
        if not isinstance(limits, dict) or not all(isinstance(v, int) and v > 0 for v in limits.values()):
            errs.append('`site.limits` must map "section.field" to a number of characters')
        lacks = shape.get("lacks") or {}
        if not isinstance(lacks, dict) or not all(s in profile.FIELDS and isinstance(fs, list) for s, fs in lacks.items()):
            errs.append("`site.lacks` must map a section to the fields it has no place for")
        if errs:
            return site, errs
        with self.lock:
            recs = self._records()
            rec = next((r for r in recs if r.get("id") == site.id), None)
            if rec is None:
                return site, []
            rec.update(sections=secs, basics=basics, limits={str(k): v for k, v in limits.items()},
                       lacks={s: [str(f) for f in fs] for s, fs in lacks.items()}, learned=True)
            self._write_records(recs)
            return sites.build(rec) or site, []

    def load(self, site_id: str) -> dict:
        with self.lock:
            try:
                return json.loads(self._path(site_id).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

    def save(self, site_id: str, **fields: Any) -> dict:
        with self.lock:
            st = {**self.load(site_id), **fields}
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self._path(site_id).with_suffix(".tmp")
            tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self._path(site_id))
            return st

    # ---- which resume, in which language ------------------------------------------
    def target(self, site: Site, st: dict | None = None) -> tuple[Any, str]:
        """(subset, lang) for this site: the user's choice, else ALL in the site's usual language."""
        st = self.load(site.id) if st is None else st
        doc, _ = self.studio.store.load()
        chosen = st.get("subset_id") or st.get("version_id")   # version_id: saved before subsets were named
        subset = model.get_subset(doc, str(chosen or model.ALL_ID))
        if subset is None:
            subset = model.get_subset(doc, model.ALL_ID)
        langs = [str(x) for x in (doc.get("settings") or {}).get("languages") or [model.first_lang(doc)]]
        lang = str(st.get("lang") or "")
        if lang not in langs:
            lang = next((x for x in langs if x == site.lang or x.split("-")[0] == site.lang), langs[0])
        return subset, lang

    def desired(self, site: Site, st: dict | None = None) -> dict:
        doc, _ = self.studio.store.load()
        subset, lang = self.target(site, st)
        return profile.desired(doc, subset, lang, site)

    # ---- the local copy and its sync with the site ----------------------------------
    def copies(self, site_id: str, st: dict | None = None) -> tuple[dict | None, dict | None]:
        """(base, local). A file from before the local copy existed has only what was last read."""
        st = self.load(site_id) if st is None else st
        if st.get("base") is None and st.get("current"):
            m = sync.merge(None, None, st["current"])
            st = self.save(site_id, base=m["base"], local=m["local"])
        return st.get("base"), st.get("local")

    def _limits(self, site: Site, it: dict) -> None:
        for f in it["fields"]:
            lim = site.limits.get(f"{it['section']}.{f['name']}")
            if lim:
                f["limit"], f["over"] = lim, len(f["to"] or "") > lim

    def pending(self, site: Site, st: dict | None = None) -> list[dict]:
        st = self.load(site.id) if st is None else st
        base, local = self.copies(site.id, st)
        ticks = st.get("ticks") or {}
        applied = st.get("applied") or {}
        out = sync.pending(base, local)
        for it in out:
            it["checked"] = bool(ticks.get(it["id"], True))
            self._limits(site, it)
            if it["id"] in applied:
                it["applied"] = applied[it["id"]]
        return out

    def reference(self, site: Site, local: dict | None, st: dict) -> dict:
        """What the chosen resume subset says, next to the local copy: per item the fields the resume
        words differently, the resume's entries the copy lacks, and its skills not there yet."""
        if local is None:
            return {"items": {}, "basics": {}, "missing": [], "skills": []}
        want = self.desired(site, st)
        items = local.get("items") or []
        m = sync.match(items, [{"section": w["section"], "fields": w["fields"]} for w in want["items"]])
        per: dict[str, dict] = {}
        missing = []
        for j, w in enumerate(want["items"]):
            if j not in m:
                missing.append({"key": w["key"], "section": w["section"], "label": w["label"], "fields": w["fields"]})
                continue
            it = items[m[j]]
            diff = {k: v for k, v in w["fields"].items() if v and not profile.same(k, v, str(it["fields"].get(k) or ""))}
            per[it["id"]] = {"key": w["key"], "fields": diff}
        basics = {k: v for k, v in want["basics"].items()
                  if v and not profile.same(k, v, str((local.get("basics") or {}).get(k) or ""))}
        have = {s.lower() for s in local.get("skills") or []}
        return {"items": per, "basics": basics, "missing": missing, "skills": [s for s in want["skills"] if s.lower() not in have]}

    def view(self, site: Site) -> dict:
        st = self.load(site.id)
        subset, lang = self.target(site, st)
        base, local = self.copies(site.id, st)
        st = self.load(site.id)
        r = st.get("route")
        return {"site": site.public(), "subset_id": str(subset.get("id")), "lang": lang,
                "local": local, "base": base, "synced": base is not None, "synced_at": st.get("synced_at") or st.get("read_at"),
                "conflicts": st.get("conflicts") or {}, "notes": st.get("notes") or [],
                "last_check": st.get("last_check"), "checking": st.get("checking"),
                "route": {"recorded": bool(r), "pages": len((r or {}).get("pages") or []), "covers": route.covers(r),
                          "at": st.get("route_at"), "broken": st.get("route_broken")},
                "pending": self.pending(site, st), "reference": self.reference(site, local, st),
                "signed_out": bool(st.get("signed_out")), "read_chat": st.get("read_chat"),
                "apply_chat": st.get("apply_chat"), "applying": st.get("applying") or [], "applied": st.get("applied") or {}}

    def summary(self) -> list[dict]:
        out = []
        for site in self.sites():
            st = self.load(site.id)
            pend = [p for p in self.pending(site, st) if p["checked"]] if st.get("base") or st.get("current") else None
            out.append({**site.public(), "synced_at": st.get("synced_at") or st.get("read_at"), "signed_out": bool(st.get("signed_out")),
                        "changes": len(pend) if pend is not None else None, "conflicts": len(st.get("conflicts") or {}),
                        "check_error": (st.get("last_check") or {}).get("error") or ""})
        return out

    # ---- a fresh read of the site comes in (by the route, or from Claude) ---------------
    def take_reading(self, site: Site, reading: dict, scope: list[str] | None, mode: str) -> dict:
        reading = {**reading, "items": [{**i, "fields": _dates(i.get("fields") or {})} for i in reading.get("items") or []]}
        with self.lock:
            st = self.load(site.id)
            base, local = self.copies(site.id, st)
            m = sync.merge(base, local, reading, scope)
            # an unsettled conflict stays while neither side moved on
            old = st.get("conflicts") or {}
            keep = {}
            for k, c in old.items():
                if k in m["conflicts"]:
                    continue
                if _value(m["local"], k) == c.get("local") and _value(m["base"], k) == c.get("site"):
                    keep[k] = c
            conflicts = {**keep, **m["conflicts"]}
            now = time.time()
            check = {"at": now, "mode": mode, "synced": m["synced"], "conflicts": len(m["conflicts"]), "notes": m["notes"]}
            self.save(site.id, base=m["base"], local=m["local"], conflicts=conflicts, notes=m["notes"], synced_at=now,
                      last_check=check, signed_out=False, checking=None, route_broken=None,
                      current={**reading, "url": reading.get("url", "")})
            return check

    def check_failed(self, site: Site, err: str, signed_out: bool = False) -> None:
        self.save(site.id, checking=None, signed_out=signed_out or self.load(site.id).get("signed_out", False),
                  last_check={"at": time.time(), "mode": "route", "error": err, "signed_out": signed_out},
                  route_broken=None if signed_out else err)

    def set_current(self, site: Site, data: Any) -> tuple[dict, list[str]]:
        """What Claude read (`browser.py profile`): the whole profile, into the local copy."""
        if isinstance(data, dict) and "site" in data:
            site, errs = self.learn(site, data.get("site"))
            if errs:
                return {}, errs
        elif not site.preset and not site.learned:
            return {}, ["this site is new to Resume Studio: add `site` (its sections, basics, limits, lacks) to the JSON"]
        cur, errs = profile.validate_current(data, site)
        if errs:
            return {}, errs
        return self.take_reading(site, cur, None, "claude"), []

    def set_route(self, site: Site, data: Any) -> tuple[dict, list[str]]:
        if isinstance(data, dict) and data.get("site") is not None:
            site, errs = self.learn(site, data.get("site"))
            if errs:
                return {}, errs
        r, errs = route.validate(data, site)
        if errs:
            return {}, errs
        self.save(site.id, route=r, route_at=time.time(), route_broken=None)
        return r, []

    # ---- the user's edits to the local copy --------------------------------------------
    def edit(self, site: Site, op: dict) -> dict:
        """One change to the local copy: set a field, add / remove / restore an item, settle a conflict,
        take the resume's wording, undo a pending change."""
        with self.lock:
            st = self.load(site.id)
            base, local = self.copies(site.id, st)
            if local is None:
                raise ValueError("check for updates first: there is no local copy yet")
            local = sync.clean(local)
            conflicts = dict(st.get("conflicts") or {})
            kind = op.get("op")
            item = str(op.get("item") or "")
            field = str(op.get("field") or "")
            find = lambda: next((i for i in local["items"] if i["id"] == item), None)   # noqa: E731
            if kind == "set":
                value = str(op.get("value") if op.get("value") is not None else "")
                if item == "basics":
                    local["basics"][field] = value
                    conflicts.pop(f"basics.{field}", None)   # typed over: the user's answer to it
                elif item == "skills":
                    local["skills"] = [s.strip() for s in value.split("\n") if s.strip()]
                else:
                    it = find()
                    if it is None:
                        raise KeyError(item)
                    if field not in site.fields(it["section"]):
                        raise ValueError(f"{field} isn't a field of {it['section']} on {site.name}")
                    it["fields"][field] = value
                    conflicts.pop(f"{item}.{field}", None)
            elif kind == "add":
                section = str(op.get("section") or "")
                if section not in site.sections or section == "skills":
                    raise ValueError(f"{site.name} has no {section}")
                fields = {k: str(v) for k, v in (op.get("fields") or {}).items() if k in site.fields(section)}
                iid = sync.new_id(base, local)
                at = max((n + 1 for n, i in enumerate(local["items"]) if i["section"] == section), default=len(local["items"]))
                local["items"].insert(at, {"id": iid, "section": section, "fields": fields})
                item = iid
            elif kind == "remove":
                if find() is None:
                    raise KeyError(item)
                local["items"] = [i for i in local["items"] if i["id"] != item]
                conflicts = {k: v for k, v in conflicts.items() if not k.startswith(item + ".")}
            elif kind == "revert":
                local = sync.revert(base or sync.empty(), local, str(op.get("change") or ""))
                pre = str(op.get("change") or "").removeprefix("item.")
                conflicts = {k: v for k, v in conflicts.items() if not (k.startswith(pre + ".") or k == pre)}
            elif kind == "settle":
                key, take = str(op.get("key") or ""), op.get("take")
                c = conflicts.pop(key, None)
                if c is None:
                    raise KeyError(key)
                if take == "site":
                    _set_value(local, key, c["site"])
            elif kind == "add_skills":
                have = {s.lower() for s in local["skills"]}
                local["skills"] += [s for s in op.get("skills") or [] if isinstance(s, str) and s.lower() not in have]
            else:
                raise ValueError(f"unknown op {kind!r}")
            self.save(site.id, local=local, conflicts=conflicts)
            return {"item": item}

    # ---- pushing to the site (Claude, in the browser) ------------------------------------
    def report(self, site: Site, change_id: str, state: str, note: str = "") -> dict:
        if state not in APPLY_STATES:
            raise ValueError(f"state must be one of {', '.join(APPLY_STATES)}")
        with self.lock:
            st = self.load(site.id)
            base, local = self.copies(site.id, st)
            pend = {p["id"]: p for p in sync.pending(base, local)}
            applied = dict(st.get("applied") or {})
            if change_id not in pend and not (state == "done" and change_id in (st.get("applying") or [])):
                raise KeyError(change_id)
            applied[change_id] = {"state": state, "note": note, "at": time.time()}
            fields: dict[str, Any] = {"applied": applied}
            if state == "done" and change_id in pend:
                fields["base"] = sync.pushed(base or sync.empty(), local or sync.empty(), pend[change_id])
            return self.save(site.id, **fields)

    def choose(self, site: Site, *, subset_id: str | None = None, lang: str | None = None,
               ticks: dict | None = None) -> dict:
        fields: dict[str, Any] = {}
        if subset_id is not None:
            fields["subset_id"] = subset_id
        if lang is not None:
            fields["lang"] = lang
        if ticks is not None:
            fields["ticks"] = {str(k): bool(v) for k, v in ticks.items()}
        return self.save(site.id, **fields)


def _dates(fields: dict) -> dict:
    """Dates the way the copy keeps them: YYYY-MM (or YYYY, or "present"), whatever the site wrote."""
    out = dict(fields)
    for k in ("start", "end", "date"):
        if out.get(k):
            d = profile.norm_date(str(out[k]))
            if re.fullmatch(r"\d{4}(-\d{2})?|present", d):
                out[k] = d
    return out


def _value(p: dict, key: str) -> str:
    """A conflict's field in a profile: "basics.<name>" or "<item id>.<field>"."""
    a, _, f = key.partition(".")
    if a == "basics":
        return str((p.get("basics") or {}).get(f) or "")
    it = next((i for i in p.get("items") or [] if i["id"] == a), None)
    return str((it or {}).get("fields", {}).get(f) or "")


def _set_value(p: dict, key: str, value: str) -> None:
    a, _, f = key.partition(".")
    if a == "basics":
        p["basics"][f] = value
        return
    it = next((i for i in p["items"] if i["id"] == a), None)
    if it is not None:
        it["fields"][f] = value
