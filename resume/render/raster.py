"""Turn a rendered PDF into page images for the preview.

Images (instead of an embedded PDF viewer) let the UI fit pages to the panel,
show every page stacked so an overflow is visible at a glance, and swap renders
without flicker. pdftoppm ships with TeX Live and MiKTeX, next to xelatex.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def find_pdftoppm() -> str | None:
    exe = shutil.which("pdftoppm") or shutil.which("miktex-pdftoppm")
    if exe:
        return exe
    tex = shutil.which("xelatex")
    if tex:
        for name in ("pdftoppm.exe", "pdftoppm", "miktex-pdftoppm.exe"):
            cand = Path(tex).parent / name
            if cand.exists():
                return str(cand)
    return None


def rasterize(pdf: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
    exe = find_pdftoppm()
    if not exe or not pdf.exists():
        return []
    for old in out_dir.glob("page-*.png"):
        old.unlink(missing_ok=True)
    subprocess.run([exe, "-png", "-r", str(dpi), str(pdf), str(out_dir / "page")],
                   capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # pdftoppm pads the page number depending on page count (page-1.png / page-01.png)
    pages = sorted(out_dir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    return pages
