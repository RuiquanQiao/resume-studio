"""Turn a rendered PDF into page images for the preview.

Images (instead of an embedded PDF viewer) let the UI fit pages to the panel,
show every page stacked so an overflow is visible at a glance, and swap renders
without flicker. pdftoppm ships with TeX Live and MiKTeX, next to xelatex.

pdftoppm draws a page in a few dozen milliseconds but its own PNG writer takes about half a
second per page, so it hands over raw pixels and the PNG is written here (zlib, fast setting),
one page per thread.
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def _png(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[i:i + stride] for i in range(0, height * stride, stride))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b""))


_PPM_RE = re.compile(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s")


def _page(exe: str, pdf: Path, n: int, dpi: int) -> bytes | None:
    """Page n as PNG bytes (pdftoppm writes one page's PPM to stdout with -singlefile)."""
    p = subprocess.run([exe, "-r", str(dpi), "-f", str(n), "-l", str(n), "-singlefile", str(pdf)],
                       capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    m = _PPM_RE.match(p.stdout)
    if p.returncode != 0 or not m or int(m.group(3)) != 255:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    rgb = p.stdout[m.end():m.end() + w * h * 3]
    return _png(w, h, rgb) if len(rgb) == w * h * 3 else None


def rasterize(pdf: Path, out_dir: Path, dpi: int = 150, pages: int = 0) -> list[Path]:
    """`pages`: how many the PDF has (from the LaTeX log); 0 = ask pdftoppm for them all at once."""
    exe = find_pdftoppm()
    if not exe or not pdf.exists():
        return []
    for old in out_dir.glob("page-*.png"):
        old.unlink(missing_ok=True)
    images = None
    if pages > 0:
        with ThreadPoolExecutor(min(pages, 4)) as pool:
            images = list(pool.map(lambda n: _page(exe, pdf, n, dpi), range(1, pages + 1)))
    if images and all(images):
        for n, data in enumerate(images, 1):
            (out_dir / f"page-{n}.png").write_bytes(data)
    else:   # page count unknown, or this pdftoppm can't write to stdout: its own PNG files
        subprocess.run([exe, "-png", "-r", str(dpi), str(pdf), str(out_dir / "page")],
                       capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    # pdftoppm pads the page number depending on page count (page-1.png / page-01.png)
    return sorted(out_dir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
