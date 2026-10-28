"""Taking the user's data elsewhere: all of it as one zip, or all of it moved to another folder.

The data is the data file, its `resume-studio-data/` folder (without `.cache`, which the app rebuilds)
and the export folder when it sits inside the data file's folder. `.local/` (this computer's settings,
the app's browser profile with the user's sign-ins) is not part of it and never goes along.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from core.i18n import t
from core.store import CACHE_DIR, DATA_DIR

from .service import Studio


def parts(studio: Studio) -> list[tuple[Path, str]]:
    """(path on disk, name inside the data folder) of every piece there is."""
    doc, _ = studio.store.load()
    out = [(studio.store.path, studio.store.path.name)]
    if studio.store.state_dir.is_dir():
        out.append((studio.store.state_dir, DATA_DIR))
    raw = str((doc.get("settings") or {}).get("export_dir") or "exports")
    exports = (studio.data_dir / raw).resolve()
    if exports.is_dir() and exports.is_relative_to(studio.data_dir) and exports != studio.data_dir:
        out.append((exports, exports.relative_to(studio.data_dir).as_posix()))
    return out


def _files(src: Path):
    """Every file under `src` but the cache, with its path relative to `src`."""
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_file() and rel.parts[0] != CACHE_DIR:
            yield p, rel


def export_zip(studio: Studio, target: Path) -> int:
    """Write the whole data into `target` (a .zip); returns how many files went in."""
    n = 0
    tmp = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for src, name in parts(studio):
                if src.is_file():
                    z.write(src, name)
                    n += 1
                    continue
                for p, rel in _files(src):
                    z.write(p, f"{name}/{rel.as_posix()}")
                    n += 1
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return n


def check_move(studio: Studio, folder: Path) -> None:
    """Raise ValueError (said to the user) when the data can't go into `folder`."""
    if folder == studio.data_dir:
        raise ValueError(t("Your data is already in this folder."))
    if folder.is_relative_to(studio.store.state_dir):
        raise ValueError(t("Pick a folder outside {name}.", name=DATA_DIR))
    taken = [name for _, name in parts(studio) if (folder / name).exists()]
    if taken:
        raise ValueError(t("This folder already has {names}; pick an empty one, or open that data file instead.",
                           names=", ".join(taken)))


def move(studio: Studio, folder: Path) -> tuple[Path, list[str]]:
    """Copy every piece into `folder`, then remove the originals. Returns the new data file and
    what could not be removed at the old place (the copy is complete either way)."""
    check_move(studio, folder)
    pieces = parts(studio)
    done: list[Path] = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for src, name in pieces:
            dst = folder / name
            done.append(dst)
            if src.is_file():
                shutil.copy2(src, dst)
                continue
            for p, rel in _files(src):
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst / rel)
    except OSError:
        for d in done:   # nothing half-moved: the old place stays the one in use
            shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink(missing_ok=True)
        raise
    _repoint_chats(folder / DATA_DIR / "chats", studio.store.state_dir, folder / DATA_DIR)
    left = []
    for src, _ in pieces:
        try:
            shutil.rmtree(src) if src.is_dir() else src.unlink()
        except OSError:
            left.append(str(src))
    return folder / studio.store.path.name, left


def _repoint_chats(chats: Path, old: Path, new: Path) -> None:
    """Attachments are named by their full path in the chats' records: point them at the new place."""
    pairs = [(json.dumps(str(old), ensure_ascii=False)[1:-1], json.dumps(str(new), ensure_ascii=False)[1:-1]),
             (old.as_posix(), new.as_posix())]
    for f in chats.rglob("*.json") if chats.is_dir() else []:
        text = f.read_text(encoding="utf-8")
        fixed = text
        for a, b in pairs:
            fixed = fixed.replace(a, b)
        if fixed != text:
            f.write_text(fixed, encoding="utf-8")
