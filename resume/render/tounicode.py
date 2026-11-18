"""Give every font in a built PDF a text layer a resume parser can read.

Recruiting systems (北森, Moka, Workday…) parse the uploaded PDF into fields from its text layer, and most
parsers (pypdf, PDFBox and the like) read a CID font's text only from its /ToUnicode map. xdvipdfmx leaves that
map out on purpose for a font in one of Adobe's character collections (FandolSong is Adobe-GB1, HaranoAji
Adobe-Japan1): Acrobat and poppler look the collection up themselves, so the PDF looks fine there, but pypdf
reads every Chinese character as noise ("陈亚历" came out as "ӧ࿰৥"). Three repairs, all to the font
dictionaries only (the page stays byte-for-byte what TeX drew):

- a collection font gets the CIDs it uses from Adobe's own CID-to-Unicode map, which TeX Live ships
  (fonts/cmap/adobemapping/.../pdf2unicode/Adobe-GB1-UCS2);
- an icon font (Font Awesome) gets a map to a space, so "+61 400…" is not read as "Ȳ +61 400…";
- a map that sends a glyph to a non-breaking or soft hyphen (U+2011, U+2010, U+00AD: one glyph the font shares
  with "-") sends it to "-", so "scikit-learn" still matches a keyword.
"""
from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path

COLLECTIONS = ("GB1", "CNS1", "Japan1", "Korea1", "KR")
_HYPHENS = {0x2010: 0x2D, 0x2011: 0x2D, 0x00AD: 0x2D}
_HEX = r"<([0-9A-Fa-f]+)>"
_CHAR_RE = re.compile(rf"^\s*{_HEX}\s*{_HEX}\s*$", re.M)
_RANGE_RE = re.compile(rf"^\s*{_HEX}\s*{_HEX}\s*{_HEX}\s*$", re.M)
_BLOCK_RE = re.compile(r"begin(bfchar|bfrange)(.*?)end\1", re.S)


@functools.lru_cache(maxsize=None)
def _collection_map(ordering: str) -> dict[int, str]:
    """CID -> text for an Adobe character collection, from TeX's own copy of Adobe's map ({} if not found)."""
    exe = shutil.which("kpsewhich")
    if not exe:
        return {}
    try:
        path = subprocess.run([exe, "-format=cmap", f"Adobe-{ordering}-UCS2"], capture_output=True, text=True,
                              timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if not path or not Path(path).is_file():
        return {}
    return _parse_cmap(Path(path).read_text(encoding="latin-1"))


def _text(hexstr: str) -> str:
    return bytes.fromhex(hexstr).decode("utf-16-be", errors="ignore")


def _parse_cmap(src: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for kind, body in _BLOCK_RE.findall(src):
        if kind == "bfchar":
            for code, dst in _CHAR_RE.findall(body):
                out[int(code, 16)] = _text(dst)
        else:
            for lo, hi, dst in _RANGE_RE.findall(body):
                start = int(dst, 16)
                for i, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                    out[code] = chr(start + i)
    return out


def _used_codes(reader) -> dict[int, set[int]]:
    """Font object number -> the 2-byte codes (CIDs, under Identity-H) the pages show in it. Read from the
    content streams, since a CJK font's widths array is often left out (every glyph 1000 wide)."""
    from pypdf.generic import ContentStream
    used: dict[int, set[int]] = {}
    for page in reader.pages:
        res = page.get("/Resources")
        fonts = res.get_object().get("/Font") if res else None
        if not fonts:
            continue
        fonts = fonts.get_object()
        current: set[int] | None = None
        content = page.get_contents()
        if content is None:
            continue
        for operands, op in ContentStream(content, reader).operations:
            if op == b"Tf":
                ref = fonts.raw_get(operands[0]) if operands[0] in fonts else None
                current = used.setdefault(ref.idnum, set()) if hasattr(ref, "idnum") else None
            elif current is not None and op in (b"Tj", b"TJ", b"'", b'"'):
                for item in (operands[0] if op == b"TJ" else operands[-1:]):
                    raw = getattr(item, "original_bytes", None)
                    if raw is not None:
                        current.update(int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2))
    return used


def _cmap_stream(mapping: dict[int, str]) -> bytes:
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    items = sorted(mapping.items())
    for k in range(0, len(items), 100):
        chunk = items[k:k + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines += [f"<{cid:04X}> <{text.encode('utf-16-be').hex().upper()}>" for cid, text in chunk]
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def _fonts(reader):
    """(object number, font dictionary) for every font the pages use, once each."""
    seen = set()
    for page in reader.pages:
        res = page.get("/Resources")
        fonts = res.get_object().get("/Font") if res else None
        if not fonts:
            continue
        fonts = fonts.get_object()
        for key in fonts:
            ref = fonts.raw_get(key)
            num = getattr(ref, "idnum", None)
            if num is not None and num not in seen:
                seen.add(num)
                yield num, ref.get_object()


def _fixed_hyphens(data: bytes) -> bytes | None:
    src = data.decode("latin-1")
    changed = False

    def one(m):
        nonlocal changed
        code = int(m.group(2), 16)
        if code in _HYPHENS:
            changed = True
            return f"{m.group(1)}<{_HYPHENS[code]:04X}>"
        return m.group(0)
    # one mapping per line: "<code> <text>" (bfchar) or "<lo> <hi> <text>" (bfrange); only the text is changed
    out = re.sub(r"^([ \t]*<[0-9A-Fa-f]+>[ \t]*(?:<[0-9A-Fa-f]+>[ \t]*)?)<([0-9A-Fa-f]{4})>(?=[ \t]*$)", one, src,
                 flags=re.M)
    return out.encode("latin-1") if changed else None


def repair(pdf: Path) -> list[str]:
    """Repair `pdf` in place; returns what was done, one line per font (empty: nothing needed)."""
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject
    except ImportError:  # an older .venv: the PDF stays as xdvipdfmx made it
        return []
    writer = PdfWriter(clone_from=PdfReader(str(pdf)))
    done: list[str] = []
    used = None
    for num, font in _fonts(writer):
        if font.get("/Subtype") != "/Type0":
            continue
        name = str(font.get("/BaseFont", "")).lstrip("/")
        tu = font.get("/ToUnicode")
        if tu is not None:
            stream = tu.get_object()
            fixed = _fixed_hyphens(stream.get_data())
            if fixed is not None:
                stream.set_data(fixed)
                done.append(f"{name}: hyphens")
            continue
        enc = str(font.get("/Encoding", ""))
        cidfont = font["/DescendantFonts"][0].get_object()
        info = cidfont.get("/CIDSystemInfo", {})
        info = info.get_object() if info else {}
        ordering = str(info.get("/Ordering", ""))
        if enc not in ("/Identity-H", "/Identity-V"):
            continue
        if used is None:
            used = _used_codes(writer)
        cids = sorted(used.get(num, ()))
        if not cids:
            continue
        if "FontAwesome" in name:
            mapping = dict.fromkeys(cids, " ")
        elif str(info.get("/Registry", "")) == "Adobe" and ordering in COLLECTIONS:
            table = _collection_map(ordering)
            mapping = {c: table[c] for c in cids if c in table}
            mapping = {c: chr(_HYPHENS.get(ord(t), ord(t))) if len(t) == 1 else t for c, t in mapping.items()}
        else:
            continue
        if not mapping:
            continue
        stream = DecodedStreamObject()
        stream.set_data(_cmap_stream(mapping))
        font[NameObject("/ToUnicode")] = writer._add_object(stream)
        done.append(f"{name}: {len(mapping)} of {len(cids)} glyphs")
    if done:
        tmp = pdf.with_suffix(".fixing.pdf")
        with open(tmp, "wb") as f:
            writer.write(f)
        tmp.replace(pdf)
    return done
