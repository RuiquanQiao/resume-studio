"""Operations shared by the HTTP API and the command-line tools."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from core.store import YamlStore, fingerprint, merge_into, to_plain

from . import model
from .render import all_templates, get_renderer


class Conflict(Exception):
    def __init__(self, current: Any):
        super().__init__("changed elsewhere")
        self.current = current


class Studio:
    def __init__(self, data_path: str | Path):
        self.store = YamlStore(data_path)
        self.data_dir = self.store.path.parent
        self.build_root = self.store.state_dir / "build"
        self.extra_state = lambda: {}  # the UI layer adds runtime info (e.g. Claude availability)

    # ---- setup ---------------------------------------------------------
    def ensure_file(self) -> bool:
        """Create a skeleton resume.yaml if missing. Returns True if created."""
        if self.store.exists():
            return False
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(self.store.dump(model.skeleton()))
        return True

    # ---- reading -------------------------------------------------------
    def state(self) -> dict:
        doc, digest = self.store.load()
        return {
            "doc": to_plain(doc),
            "digest": digest,
            "hashes": model.hashes(doc),
            "warnings": model.validate(doc),
            "templates": all_templates(),
            "layout_defaults": model.DEFAULT_LAYOUT,
            "data_path": str(self.store.path),
            **self.extra_state(),
        }

    # ---- writing (per item, so concurrent edits elsewhere survive) -----
    def update_item(self, kind: str, item_id: str | None, data: Any, base: str | None) -> dict:
        with self.store.lock:
            doc, _ = self.store.load()
            current = model.get_item(doc, kind, item_id)
            if current is None and kind in ("entry", "version"):
                raise KeyError(f"{kind} {item_id} 不存在")
            if base and current is not None and fingerprint(current) != base:
                raise Conflict(to_plain(current))
            if kind in ("entry", "version"):
                data = dict(data)
                new_id = str(data.get("id") or item_id)
                if new_id != str(item_id) and model.get_item(doc, kind, new_id) is not None:
                    raise ValueError(f"ID {new_id} 已存在")
                data["id"] = new_id
                merge_into(current, data)
                if kind == "entry" and new_id != str(item_id):  # keep versions pointing at it
                    for v in doc.get("versions") or []:
                        refs = v.get("entries") or []
                        for i, ref in enumerate(refs):
                            if str(ref) == str(item_id):
                                refs[i] = new_id
            elif current is None:
                doc[kind] = data
            else:
                doc[kind] = merge_into(current, data)
            self.store.save(doc)
        return self.state()

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

    def delete_entry(self, entry_id: str) -> dict:
        with self.store.lock:
            doc, _ = self.store.load()
            entries = doc.get("entries") or []
            for i in range(len(entries) - 1, -1, -1):
                if str(entries[i].get("id")) == entry_id:
                    del entries[i]
            for v in doc.get("versions") or []:
                refs = v.get("entries") or []
                for i in range(len(refs) - 1, -1, -1):
                    if str(refs[i]) == entry_id:
                        del refs[i]
            self.store.save(doc)
        return self.state()

    def create_version(self, copy_from: str | None, new_id: str | None) -> tuple[str, dict]:
        with self.store.lock:
            doc, _ = self.store.load()
            versions = doc.get("versions")
            if versions is None:
                doc["versions"] = versions = []
            existing = {str(v.get("id")) for v in versions}
            base_id = new_id or (f"{copy_from}-COPY" if copy_from else "RES-NEW")
            vid, n = base_id, 2
            while vid in existing:
                vid, n = f"{base_id}-{n}", n + 1
            src = model.get_item(doc, "version", copy_from) if copy_from else None
            if src is not None:
                v = to_plain(src)
                v["id"], v["label"] = vid, f"{v.get('label') or copy_from} (copy)"
            else:
                v = {"id": vid, "label": vid, "lang": "en", "template": "classic", "entries": [], "layout": {}}
            versions.append(v)
            self.store.save(doc)
        return vid, self.state()

    def delete_version(self, version_id: str) -> dict:
        with self.store.lock:
            doc, _ = self.store.load()
            versions = doc.get("versions") or []
            for i in range(len(versions) - 1, -1, -1):
                if str(versions[i].get("id")) == version_id:
                    del versions[i]
            self.store.save(doc)
        return self.state()

    # ---- rendering -----------------------------------------------------
    def _safe(self, name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "version"

    def render(self, version_id: str) -> dict:
        doc, digest = self.store.load()
        version = model.get_item(doc, "version", version_id)
        if version is None:
            raise KeyError(f"版本 {version_id} 不存在")
        view = model.build_view(doc, to_plain(version))
        engine = str(version.get("engine") or "latex")
        result = get_renderer(engine).render(view, self.build_root / self._safe(version_id))
        return {**result.to_json(), "version": version_id, "digest": digest}

    def pdf_path(self, version_id: str) -> Path:
        return self.build_root / self._safe(version_id) / "main.pdf"

    def export(self, version_id: str) -> dict:
        result = self.render(version_id)
        if not result["ok"]:
            return result
        doc, _ = self.store.load()
        export_dir = self.data_dir / str((doc.get("settings") or {}).get("export_dir") or "exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        name = self._safe(version_id)
        out_pdf = export_dir / f"{name}.pdf"
        shutil.copyfile(result["pdf"], out_pdf)
        exported = {"pdf": str(out_pdf)}
        if result.get("source"):
            src = Path(result["source"])
            out_src = export_dir / f"{name}{src.suffix}"
            shutil.copyfile(src, out_src)
            exported["source"] = str(out_src)
        return {**result, "exported": exported}
