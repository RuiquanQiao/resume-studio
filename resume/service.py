"""Operations shared by the HTTP API and the command-line tools."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from core.i18n import system_locale, t
from core.store import YamlStore, fingerprint, merge_into, to_plain

from . import langs, model, versions
from .render import all_templates, get_renderer
from .render.fonts import catalog as font_catalog
from .render.raster import rasterize


class Conflict(Exception):
    def __init__(self, current: Any):
        super().__init__("changed elsewhere")
        self.current = current


class Studio:
    def __init__(self, data_path: str | Path):
        self.store = YamlStore(data_path, upgrade=model.migrate)
        self.data_dir = self.store.path.parent
        self.build_root = self.store.state_dir / "build"
        self.versions = versions.Versions(self.store.state_dir)
        self.extra_state = lambda: {}  # the UI layer adds runtime info (e.g. Claude availability)

    # ---- setup ---------------------------------------------------------
    def ensure_file(self) -> bool:
        """Create a skeleton resume.yaml if missing. Returns True if created. An existing file in
        an older format is rewritten in the current one, so Claude reads the same keys as SKILL.md."""
        if self.store.exists():
            with self.store.lock:
                doc = self.store._yaml.load(self.store.read_text())
                if model.migrate(doc):
                    self.store.save(doc)
            return False
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(self.store.dump(model.skeleton(langs.match(system_locale()))))
        return True

    # ---- reading -------------------------------------------------------
    def state(self) -> dict:
        doc, digest = self.store.load()
        plain = to_plain(doc)
        plain["subsets"] = model.with_all(plain.get("subsets") or [], model.first_lang(doc))
        return {
            "doc": plain,
            "digest": digest,
            "hashes": model.hashes(doc),
            "warnings": model.validate(doc),
            "templates": all_templates(),
            "fonts": font_catalog(),
            "resume_langs": langs.catalog(),
            "layout_defaults": model.DEFAULT_LAYOUT,
            "data_path": str(self.store.path),
            "subset_versions": {str(v.get("id")): self.versions.status(str(v.get("id")), versions.content(doc, v))
                                for v in doc.get("subsets") or [] if not model.is_all(v)},
            **self.extra_state(),
        }

    # ---- writing (per item, so concurrent edits elsewhere survive) -----
    def update_item(self, kind: str, item_id: str | None, data: Any, base: str | None) -> dict:
        with self.store.lock:
            doc, _ = self.store.load()
            current = model.get_item(doc, kind, item_id)
            if current is None and kind == "subset" and str(item_id) == model.ALL_ID:
                current = self._add_all_subset(doc)
            if current is None and kind in ("entry", "subset"):
                raise KeyError(t("{kind} {id} doesn't exist", kind=kind, id=item_id))
            if base and current is not None and fingerprint(current) != base:
                raise Conflict(to_plain(current))
            if kind in ("entry", "subset"):
                data = dict(data)
                new_id = str(data.get("id") or item_id)
                if kind == "subset" and model.ALL_ID in (str(item_id), new_id) and new_id != str(item_id):
                    raise ValueError(t("ALL is reserved for the full set: it can't be renamed or used for a subset"))
                if new_id != str(item_id) and model.get_item(doc, kind, new_id) is not None:
                    raise ValueError(t("ID {id} already exists", id=new_id))
                data["id"] = new_id
                merge_into(current, data)
                if kind == "entry" and new_id != str(item_id):  # keep subsets pointing at it
                    for v in doc.get("subsets") or []:
                        refs = v.get("entries") or []
                        for i, ref in enumerate(refs):
                            if str(ref) == str(item_id):
                                refs[i] = new_id
            elif current is None:
                doc[kind] = data
            else:
                before = [str(x) for x in (current.get("languages") or [])] if kind == "settings" else []
                doc[kind] = merge_into(current, data)
                if kind == "settings":  # a new resume language: the default sections get their titles in it
                    for lang in data.get("languages") or []:
                        if str(lang) not in before:
                            model.add_language_titles(doc, str(lang))
            self.store.save(doc)
            if kind == "subset" and str(data["id"]) != str(item_id):
                self._rename_builds(str(item_id), str(data["id"]))
                self.versions.rename(str(item_id), str(data["id"]))
        return self.state()

    def _rename_builds(self, old_id: str, new_id: str) -> None:
        """A renamed job keeps its preview builds (JOB-1, JOB-1@zh, ...) under the new ID."""
        old, new = self._safe(old_id), self._safe(new_id)
        if old == new or not self.build_root.is_dir():
            return
        for d in list(self.build_root.iterdir()):
            name, at, lang = d.name.partition("@")
            if d.is_dir() and name == old:
                target = self.build_root / (new + at + lang)
                shutil.rmtree(target, ignore_errors=True)  # a leftover of an older job with the new ID
                try:
                    d.rename(target)
                except OSError:
                    pass  # busy (a render is writing it): the next render rebuilds it, a later delete prunes it

    @staticmethod
    def _add_all_subset(doc: Any) -> Any:
        if doc.get("subsets") is None:
            doc["subsets"] = []
        doc["subsets"].insert(0, model.default_all_subset(model.first_lang(doc)))
        return doc["subsets"][0]

    def create_entry(self, section: str, after: str | None = None) -> tuple[str, dict]:
        with self.store.lock:
            doc, _ = self.store.load()
            new_id = model.next_entry_id(doc)
            entry = {"id": new_id, "section": section, "title": "", "subtitle": "", "start": "", "end": "",
                     "bullets": [], "notes": "", "evidence": []}
            if doc.get("entries") is None:
                doc["entries"] = []
            doc["entries"].append(entry)
            self.store.save(doc)
        return new_id, self.state()

    def move_entry(self, entry_id: str, direction: int) -> dict:
        """Reorder the library itself (what ALL shows): swap with the neighbour in the same section."""
        with self.store.lock:
            doc, _ = self.store.load()
            entries = doc.get("entries") or []
            idx = next((i for i, e in enumerate(entries) if str(e.get("id")) == entry_id), None)
            if idx is None:
                raise KeyError(t("Entry {id} doesn't exist", id=entry_id))
            sec = str(entries[idx].get("section"))
            same = [i for i, e in enumerate(entries) if str(e.get("section")) == sec]
            k = same.index(idx) + (1 if direction > 0 else -1)
            if 0 <= k < len(same):
                j = same[k]
                a, b = entries[idx], entries[j]
                entries[idx] = b
                entries[j] = a
                self.store.save(doc)
        return self.state()

    def delete_entry(self, entry_id: str) -> dict:
        with self.store.lock:
            doc, _ = self.store.load()
            entries = doc.get("entries") or []
            for i in range(len(entries) - 1, -1, -1):
                if str(entries[i].get("id")) == entry_id:
                    del entries[i]
            for v in doc.get("subsets") or []:
                refs = v.get("entries") or []
                for i in range(len(refs) - 1, -1, -1):
                    if str(refs[i]) == entry_id:
                        del refs[i]
            self.store.save(doc)
        return self.state()

    def create_subset(self, copy_from: str | None, new_id: str | None, label: str | None = None) -> tuple[str, dict]:
        """A new job target. Copying ALL starts it with every entry ticked; a copy of a job keeps
        that job's section order, anything else starts from ALL's."""
        with self.store.lock:
            doc, _ = self.store.load()
            if not any(model.is_all(v) for v in doc.get("subsets") or []):
                self._add_all_subset(doc)
            subsets = doc["subsets"]
            existing = {str(v.get("id")) for v in subsets} | self.versions.ids()   # a deleted subset's history keeps its id
            base_id = (new_id or "").strip() or model.next_subset_id(doc, self.versions.ids())
            if base_id == model.ALL_ID:
                raise ValueError(t("ALL is reserved for the full set"))
            vid, n = base_id, 2
            while vid in existing:
                vid, n = f"{base_id}-{n}", n + 1
            src = model.get_subset(doc, copy_from) if copy_from else None
            if src is not None:
                v = to_plain(src)
                v["id"] = vid
                v["label"] = label or f"{v.get('label') or copy_from} (copy)"
                v.pop("posting", None)    # the postings stay with the job they were sent with
                v.pop("postings", None)
                if model.is_all(src):  # every entry ticked; the job writes its own headline
                    v["entries"] = [str(e.get("id")) for e in doc.get("entries") or []]
                    v["headline"] = ""
            else:
                v = {"id": vid, "label": label or vid, "lang": model.first_lang(doc), "template": "classic", "headline": "",
                     "entries": [], "layout": {}}
            if src is None or model.is_all(src):  # a new resume starts from ALL's section order
                v["sections"] = model.section_order(doc, model.get_subset(doc, model.ALL_ID))
            subsets.append(v)
            self.store.save(doc)
        return vid, self.state()

    def delete_subset(self, subset_id: str) -> dict:
        if subset_id == model.ALL_ID:
            raise ValueError(t("ALL (the full set) can't be deleted"))
        with self.store.lock:
            doc, _ = self.store.load()
            subsets = doc.get("subsets") or []
            for i in range(len(subsets) - 1, -1, -1):
                if str(subsets[i].get("id")) == subset_id:
                    del subsets[i]
            self.store.save(doc)
            self._prune_builds(doc)
        return self.state()

    def _prune_builds(self, doc: Any) -> None:
        """Drop preview builds of jobs that are gone (this one, and any left by a rename or an
        edit to the file). Exports are the user's and stay."""
        keep = {self._safe(str(v.get("id"))) for v in doc.get("subsets") or []} | {self._safe(model.ALL_ID)}
        if not self.build_root.is_dir():
            return
        for d in self.build_root.iterdir():
            if d.is_dir() and d.name.split("@", 1)[0] not in keep:
                shutil.rmtree(d, ignore_errors=True)

    # ---- rendering -----------------------------------------------------
    def _safe(self, name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "subset"

    def render(self, subset_id: str, lang: str | None = None, images: bool = True) -> dict:
        """Render one subset. `lang` renders it in another language (same template and layout),
        e.g. to check that the Chinese bullets fit on one line; that build goes to its own folder."""
        doc, digest = self.store.load()
        subset = model.get_subset(doc, subset_id)
        if subset is None:
            raise KeyError(t("Subset {id} doesn't exist", id=subset_id))
        v = to_plain(subset)
        if lang:
            v["lang"] = lang
        build = self._safe(subset_id) + (f"@{self._safe(lang)}" if lang else "")
        return {**self._render_view(model.build_view(doc, v), str(subset.get("engine") or "latex"), build, images),
                "subset": subset_id, "digest": digest}

    def render_version(self, subset_id: str, n: int, images: bool = True) -> dict:
        """Render version `n` of a subset as it was, whatever the file says now."""
        c = self._version(subset_id, n)["content"]
        view = model.build_view(versions.as_doc(c), c["subset"])
        return {**self._render_view(view, str(c["subset"].get("engine") or "latex"), self._version_build(subset_id, n), images),
                "subset": subset_id, "version": n}

    def _version_build(self, subset_id: str, n: int) -> str:
        return f"{self._safe(subset_id)}@v{int(n)}"

    def _render_view(self, view: dict, engine: str, build: str, images: bool) -> dict:
        build_dir = self.build_root / build
        result = get_renderer(engine).render(view, build_dir)
        pngs = []
        if images and result.ok and result.pdf:
            pngs = list(build_dir.glob("page-*.png"))
            pdf_time = result.pdf.stat().st_mtime
            if not (result.cached and len(pngs) == result.pages and all(p.stat().st_mtime >= pdf_time for p in pngs)):
                pngs = rasterize(result.pdf, build_dir)
        return {**result.to_json(), "lang": view["lang"], "missing": view["missing"], "images": len(pngs),
                "stamp": int(time.time() * 1000)}

    def bullet_report(self, result: dict) -> list[dict]:
        """Bullets that take more than one line, with the text, for people and for Claude."""
        doc, _ = self.store.load()
        out = []
        for eid, by_idx in (result.get("lines") or {}).items():
            e = model.get_item(doc, "entry", eid)
            if e is None:
                continue
            for idx, m in sorted(by_idx.items(), key=lambda kv: int(kv[0])):
                if m["n"] > 1:
                    b = (e.get("bullets") or [])[int(idx)]
                    out.append({"id": eid, "title": model.tr(e.get("title"), result["lang"]), "index": int(idx),
                                "lines": m["n"], "fill": m["fill"], "text": model.bullet_text(b, result["lang"])})
        return out

    def pdf_path(self, subset_id: str, n: int | None = None) -> Path:
        return self.build_root / (self._version_build(subset_id, n) if n else self._safe(subset_id)) / "main.pdf"

    def page_image(self, subset_id: str, page: int, n: int | None = None) -> Path | None:
        build_dir = self.build_root / (self._version_build(subset_id, n) if n else self._safe(subset_id))
        for p in build_dir.glob("page-*.png"):
            if int(p.stem.split("-")[-1]) == page:
                return p
        return None

    def export(self, subset_id: str, n: int | None = None) -> dict:
        """Copy the PDF (and its source) to the export folder: the subset now, or its version `n`
        (named <id>-v<n>, so it never replaces the current one)."""
        result = self.render_version(subset_id, n) if n else self.render(subset_id)
        if not result["ok"]:
            return result
        doc, _ = self.store.load()
        export_dir = self.data_dir / str((doc.get("settings") or {}).get("export_dir") or "exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        name = self._safe(subset_id) + (f"-v{int(n)}" if n else "")
        out_pdf = export_dir / f"{name}.pdf"
        shutil.copyfile(result["pdf"], out_pdf)
        exported = {"pdf": str(out_pdf)}
        if result.get("source"):
            src = Path(result["source"])
            out_src = export_dir / f"{name}{src.suffix}"
            shutil.copyfile(src, out_src)
            exported["source"] = str(out_src)
        return {**result, "exported": exported}

    # ---- a subset's versions (like commits: kept when the user says so) ----------
    def _subset_now(self, subset_id: str) -> tuple[Any, Any]:
        doc, _ = self.store.load()
        if subset_id == model.ALL_ID:
            raise ValueError(t("ALL (the full set) has no versions: it is what the subsets are made from"))
        sub = model.get_item(doc, "subset", subset_id)
        return doc, sub

    def _version(self, subset_id: str, n: int) -> dict:
        v = self.versions.get(subset_id, int(n))
        if v is None:
            raise KeyError(t("Version {n} doesn't exist", n=n))
        return v

    def subset_versions(self, subset_id: str) -> dict:
        """The versions, newest first, and where the subset stands now (None: it was deleted)."""
        doc, sub = self._subset_now(subset_id)
        vs = self.versions.all(subset_id)
        status = self.versions.status(subset_id, versions.content(doc, sub)) if sub is not None else None
        return {"subset": subset_id, "exists": sub is not None, "status": status,
                "versions": [versions.Versions.summary(v) for v in reversed(vs)]}

    def commit_subset(self, subset_id: str, note: str = "") -> dict:
        """Keep what the subset says now as its next version (nothing new when it hasn't changed)."""
        with self.store.lock:
            doc, sub = self._subset_now(subset_id)
            if sub is None:
                raise KeyError(t("Subset {id} doesn't exist", id=subset_id))
            v, created = self.versions.commit(subset_id, versions.content(doc, sub), note)
        return {**self.subset_versions(subset_id), "made": versions.Versions.summary(v), "created": created}

    def set_version_note(self, subset_id: str, n: int, note: str) -> dict:
        self.versions.set_note(subset_id, int(n), note)
        return self.subset_versions(subset_id)

    def compare_versions(self, subset_id: str, n: int, against: int | None = None) -> dict:
        """What changed from version `n` to version `against`, or to now when it's left out;
        with n = 0, everything in `against` (or now) counts as added."""
        old = self._version(subset_id, n)["content"] if n else None
        if against:
            new = self._version(subset_id, against)["content"]
        else:
            doc, sub = self._subset_now(subset_id)
            if sub is None:
                raise KeyError(t("Subset {id} doesn't exist", id=subset_id))
            new = versions.content(doc, sub)
        return {"from": n, "to": against, "items": versions.compare(old, new)}

    def restore_impact(self, subset_id: str, n: int) -> dict:
        doc, _ = self._subset_now(subset_id)
        return versions.impact(doc, self._version(subset_id, n)["content"])

    def restore_version(self, subset_id: str, n: int) -> dict:
        """Make the subset say what version `n` said. What it says now is kept as a version first
        (if it isn't one already), so going back can itself be undone."""
        c = self._version(subset_id, n)["content"]
        with self.store.lock:
            doc, sub = self._subset_now(subset_id)
            if sub is not None:
                self.versions.commit(subset_id, versions.content(doc, sub), t("Before going back to v{n}", n=n), auto=True)
            versions.restore(doc, c)
            self.store.save(doc)
        return self.state()
