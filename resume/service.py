"""Operations shared by the HTTP API and the command-line tools."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from core.store import YamlStore, fingerprint, merge_into, to_plain

from . import model
from .render import all_templates, get_renderer
from .render.raster import rasterize


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
        plain = to_plain(doc)
        plain["versions"] = model.with_all(plain.get("versions") or [])
        return {
            "doc": plain,
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
            if current is None and kind == "version" and str(item_id) == model.ALL_ID:
                current = self._add_all_version(doc)
            if current is None and kind in ("entry", "version"):
                raise KeyError(f"{kind} {item_id} 不存在")
            if base and current is not None and fingerprint(current) != base:
                raise Conflict(to_plain(current))
            if kind in ("entry", "version"):
                data = dict(data)
                new_id = str(data.get("id") or item_id)
                if kind == "version" and model.ALL_ID in (str(item_id), new_id) and new_id != str(item_id):
                    raise ValueError("ALL 是保留的 ID（全部经历），不能改名，也不能给岗位使用")
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

    @staticmethod
    def _add_all_version(doc: Any) -> Any:
        if doc.get("versions") is None:
            doc["versions"] = []
        doc["versions"].insert(0, model.default_all_version())
        return doc["versions"][0]

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
                raise KeyError(f"经历 {entry_id} 不存在")
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
            for v in doc.get("versions") or []:
                refs = v.get("entries") or []
                for i in range(len(refs) - 1, -1, -1):
                    if str(refs[i]) == entry_id:
                        del refs[i]
            self.store.save(doc)
        return self.state()

    def create_version(self, copy_from: str | None, new_id: str | None, label: str | None = None) -> tuple[str, dict]:
        """A new job target. Copying ALL starts it with every entry ticked."""
        with self.store.lock:
            doc, _ = self.store.load()
            if not any(model.is_all(v) for v in doc.get("versions") or []):
                self._add_all_version(doc)
            versions = doc["versions"]
            existing = {str(v.get("id")) for v in versions}
            base_id = (new_id or "").strip() or model.next_version_id(doc)
            if base_id == model.ALL_ID:
                raise ValueError("ALL 是保留的 ID（全部经历）")
            vid, n = base_id, 2
            while vid in existing:
                vid, n = f"{base_id}-{n}", n + 1
            src = model.get_version(doc, copy_from) if copy_from else None
            if src is not None:
                v = to_plain(src)
                v["id"] = vid
                v["label"] = label or f"{v.get('label') or copy_from} (copy)"
                if model.is_all(src):  # every entry ticked; the job writes its own headline
                    v["entries"] = [str(e.get("id")) for e in doc.get("entries") or []]
                    v["headline"] = ""
            else:
                v = {"id": vid, "label": label or vid, "lang": "en", "template": "classic", "headline": "",
                     "entries": [], "layout": {}}
            versions.append(v)
            self.store.save(doc)
        return vid, self.state()

    def delete_version(self, version_id: str) -> dict:
        if version_id == model.ALL_ID:
            raise ValueError("ALL（全部经历）不能删除")
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
        version = model.get_version(doc, version_id)
        if version is None:
            raise KeyError(f"岗位 {version_id} 不存在")
        view = model.build_view(doc, to_plain(version))
        engine = str(version.get("engine") or "latex")
        build_dir = self.build_root / self._safe(version_id)
        result = get_renderer(engine).render(view, build_dir)
        images = rasterize(result.pdf, build_dir) if result.ok and result.pdf else []
        return {**result.to_json(), "version": version_id, "digest": digest,
                "images": len(images), "stamp": int(time.time() * 1000)}

    def pdf_path(self, version_id: str) -> Path:
        return self.build_root / self._safe(version_id) / "main.pdf"

    def page_image(self, version_id: str, page: int) -> Path | None:
        build_dir = self.build_root / self._safe(version_id)
        for p in build_dir.glob("page-*.png"):
            if int(p.stem.split("-")[-1]) == page:
                return p
        return None

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
