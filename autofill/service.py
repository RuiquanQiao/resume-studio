"""Per data file: which version goes to which site, what was last read there, the user's ticks and
edits, and how applying went. Kept in `.studio/autofill/<site>.json` next to the data file (personal
data: git-ignored like the rest of `.studio`)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from resume import model
from resume.service import Studio

from . import profile, sites
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
        """(version, lang) for this site: the user's choice, else ALL in the site's usual language."""
        st = self.load(site.id) if st is None else st
        doc, _ = self.studio.store.load()
        version = model.get_version(doc, str(st.get("version_id") or model.ALL_ID))
        if version is None:
            version = model.get_version(doc, model.ALL_ID)
        langs = [str(x) for x in (doc.get("settings") or {}).get("languages") or [model.first_lang(doc)]]
        lang = str(st.get("lang") or "")
        if lang not in langs:
            lang = next((x for x in langs if x == site.lang or x.split("-")[0] == site.lang), langs[0])
        return version, lang

    def desired(self, site: Site, st: dict | None = None) -> dict:
        doc, _ = self.studio.store.load()
        version, lang = self.target(site, st)
        return profile.desired(doc, version, lang, site)

    def view(self, site: Site) -> dict:
        st = self.load(site.id)
        version, lang = self.target(site, st)
        want = self.desired(site, st)
        plan = profile.make_plan(site, want, st.get("current"), st.get("choices"))
        applied = st.get("applied") or {}
        for it in plan["items"]:
            if it["id"] in applied:
                it["applied"] = applied[it["id"]]
        return {"site": site.public(), "version_id": str(version.get("id")), "lang": lang,
                "desired": want, "current": st.get("current"), "read_at": st.get("read_at"),
                "plan": plan, "signed_out": bool(st.get("signed_out")), "read_chat": st.get("read_chat"),
                "apply_chat": st.get("apply_chat"), "applying": st.get("applying") or []}

    def summary(self) -> list[dict]:
        out = []
        for site in self.sites():
            st = self.load(site.id)
            plan = profile.make_plan(site, self.desired(site, st), st.get("current"), st.get("choices")) if st.get("current") else None
            out.append({**site.public(), "read_at": st.get("read_at"), "signed_out": bool(st.get("signed_out")),
                        "changes": plan["checked"] if plan else None})
        return out

    # ---- what Claude reports ---------------------------------------------------------
    def set_current(self, site: Site, data: Any) -> tuple[dict, list[str]]:
        if isinstance(data, dict) and "site" in data:
            site, errs = self.learn(site, data.get("site"))
            if errs:
                return {}, errs
        elif not site.preset and not site.learned:
            return {}, ["this site is new to Resume Studio: add `site` (its sections, basics, limits, lacks) to the JSON"]
        cur, errs = profile.validate_current(data, site)
        if errs:
            return {}, errs
        st = self.save(site.id, current=cur, read_at=time.time(), signed_out=False, applied={})
        plan = profile.make_plan(site, self.desired(site, st), cur, st.get("choices"))
        return plan, []

    def report(self, site: Site, item_id: str, state: str, note: str = "") -> dict:
        if state not in APPLY_STATES:
            raise ValueError(f"state must be one of {', '.join(APPLY_STATES)}")
        with self.lock:
            st = self.load(site.id)
            known = {i["id"] for i in profile.make_plan(site, self.desired(site, st), st.get("current"), st.get("choices"))["items"]}
            if item_id not in known:
                raise KeyError(item_id)
            applied = dict(st.get("applied") or {})
            applied[item_id] = {"state": state, "note": note, "at": time.time()}
            return self.save(site.id, applied=applied)

    def choose(self, site: Site, *, version_id: str | None = None, lang: str | None = None,
               choices: dict | None = None) -> dict:
        fields: dict[str, Any] = {}
        if version_id is not None:
            fields["version_id"] = version_id
        if lang is not None:
            fields["lang"] = lang
        if choices is not None:
            fields["choices"] = choices
        return self.save(site.id, **fields)
