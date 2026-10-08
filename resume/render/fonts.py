"""Font catalog for the LaTeX renderer: what the UI offers and how each name becomes fontspec.

layout.font / layout.cjk_font hold a catalog name ("Source Serif 4", "FandolSong") or any other
font name the user typed (a custom font, passed to fontspec as is). Empty means the default below.
Fonts shipped with TeX Live are loaded by file name, which works on every platform; system fonts
are loaded by family name and only offered when their file is actually installed.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _files(stem: str, ext: str, r: str, b: str | None, i: str | None, bi: str | None) -> dict:
    """A TeX Live font loaded by file: `stem` + style suffix + `ext`."""
    return {"tex": f"{stem}{r}{ext}", "stem": stem, "ext": ext, "styles": (r, b, i, bi)}


# kind: serif | sans (CJK also: kai | fang). bold=False -> xeCJK fakes it.
LATIN = [
    {"name": "Source Serif 4", "kind": "serif", "desc": "现代衬线，笔画偏厚，和宋体最搭",
     **_files("SourceSerif4-", ".otf", "Regular", "Bold", "RegularIt", "BoldIt")},
    {"name": "TeX Gyre Termes", "kind": "serif", "desc": "Times 风格，经典学术",
     **_files("texgyretermes-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "TeX Gyre Pagella", "kind": "serif", "desc": "Palatino 风格，舒展优雅",
     **_files("texgyrepagella-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "Libertinus Serif", "kind": "serif", "desc": "Linux Libertine 的延续，紧凑",
     **_files("LibertinusSerif-", ".otf", "Regular", "Bold", "Italic", "BoldItalic")},
    {"name": "Latin Modern Roman", "kind": "serif", "desc": "LaTeX 默认，笔画细",
     **_files("lmroman10-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "Times New Roman", "kind": "serif", "desc": "系统字体", "sys": ["times.ttf", "Times New Roman.ttf"]},
    {"name": "Georgia", "kind": "serif", "desc": "系统字体，屏幕友好", "sys": ["georgia.ttf", "Georgia.ttf"]},
    {"name": "Cambria", "kind": "serif", "desc": "系统字体，Office 默认衬线", "sys": ["cambria.ttc"]},
    {"name": "Source Sans 3", "kind": "sans", "desc": "干净的人文无衬线，配黑体/雅黑",
     **_files("SourceSans3-", ".otf", "Regular", "Bold", "RegularIt", "BoldIt")},
    {"name": "TeX Gyre Heros", "kind": "sans", "desc": "Helvetica 风格",
     **_files("texgyreheros-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "Fira Sans", "kind": "sans", "desc": "偏技术感",
     **_files("FiraSans-", ".otf", "Regular", "Bold", "Italic", "BoldItalic")},
    {"name": "Lato", "kind": "sans", "desc": "圆润友好",
     **_files("Lato-", ".ttf", "Regular", "Bold", "Italic", "BoldItalic")},
    {"name": "Arial", "kind": "sans", "desc": "系统字体", "sys": ["arial.ttf", "Arial.ttf"]},
    {"name": "Calibri", "kind": "sans", "desc": "系统字体，Office 默认", "sys": ["calibri.ttf"]},
]

CJK = [
    {"name": "FandolSong", "label": "Fandol 宋体", "kind": "serif", "desc": "TeX Live 自带，有真粗体",
     **_files("FandolSong-", ".otf", "Regular", "Bold", None, None)},
    {"name": "STZhongsong", "label": "华文中宋", "kind": "serif", "desc": "比宋体更厚实，打印清楚", "bold": False, "sys": ["STZHONGS.TTF"]},
    {"name": "STSong", "label": "华文宋体", "kind": "serif", "desc": "", "bold": False, "sys": ["STSONG.TTF"]},
    {"name": "SimSun", "label": "中易宋体", "kind": "serif", "desc": "Windows 宋体，笔画最细", "bold": False, "sys": ["simsun.ttc"]},
    {"name": "Songti SC", "label": "宋体-简", "kind": "serif", "desc": "macOS", "sys": ["Songti.ttc"]},
    {"name": "Noto Serif CJK SC", "label": "思源宋体", "kind": "serif", "desc": "", "sys": ["NotoSerifCJK-Regular.ttc"]},
    {"name": "Microsoft YaHei", "label": "微软雅黑", "kind": "sans", "desc": "Windows 默认黑体，偏粗", "sys": ["msyh.ttc"]},
    {"name": "DengXian", "label": "等线", "kind": "sans", "desc": "比雅黑更细更现代", "sys": ["Deng.ttf"]},
    {"name": "FandolHei", "label": "Fandol 黑体", "kind": "sans", "desc": "TeX Live 自带",
     **_files("FandolHei-", ".otf", "Regular", "Bold", None, None)},
    {"name": "SimHei", "label": "黑体", "kind": "sans", "desc": "", "bold": False, "sys": ["simhei.ttf"]},
    {"name": "PingFang SC", "label": "苹方", "kind": "sans", "desc": "macOS", "sys": ["PingFang.ttc"]},
    {"name": "Noto Sans CJK SC", "label": "思源黑体", "kind": "sans", "desc": "", "sys": ["NotoSansCJK-Regular.ttc"]},
    {"name": "KaiTi", "label": "楷体", "kind": "kai", "desc": "", "bold": False, "sys": ["simkai.ttf"]},
    {"name": "FangSong", "label": "仿宋", "kind": "fang", "desc": "", "bold": False, "sys": ["simfang.ttf"]},
]

# Chinese resume default: Song + a serif of matching weight. English keeps LaTeX's own default.
DEFAULT_LATIN_CJK = "Source Serif 4"
DEFAULT_CJK = "FandolSong"
# FandolSong covers GB2312 only; a rare character falls back to the first of these that exists
_CJK_FALLBACK = ["SimSun", "Songti SC", "Noto Serif CJK SC", "STSong"]

_BY_NAME = {f["name"]: f for f in LATIN + CJK}


def _sys_font_dirs() -> list[Path]:
    if sys.platform.startswith("win"):
        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    return [Path("/usr/share/fonts/opentype/noto"), Path("/usr/share/fonts/noto-cjk"),
            Path("/usr/share/fonts/truetype/msttcorefonts"), Path.home() / ".local/share/fonts"]


@functools.lru_cache(maxsize=1)
def _available() -> frozenset[str]:
    names = set()
    tex = [f for f in LATIN + CJK if "tex" in f]
    kpse = shutil.which("kpsewhich")
    if kpse:
        try:
            out = subprocess.run([kpse, *[f["tex"] for f in tex]], capture_output=True, text=True, timeout=20).stdout
            found = {Path(line.strip()).name.lower() for line in out.splitlines() if line.strip()}
            names |= {f["name"] for f in tex if f["tex"].lower() in found}
        except (OSError, subprocess.SubprocessError):
            pass
    present = set()
    for d in _sys_font_dirs():
        try:
            present |= {p.name.lower() for p in d.iterdir()}
        except OSError:
            continue
    names |= {f["name"] for f in LATIN + CJK if any(s.lower() in present for s in f.get("sys", []))}
    return frozenset(names)


def catalog() -> dict:
    """What the UI lists: only installed fonts."""
    have = _available()
    pick = lambda fonts: [{"name": f["name"], "label": f.get("label") or f["name"], "kind": f["kind"], "desc": f["desc"]}
                          for f in fonts if f["name"] in have]
    return {"latin": pick(LATIN), "cjk": pick(CJK), "default_latin_cjk": DEFAULT_LATIN_CJK, "default_cjk": DEFAULT_CJK}


def _spec(name: str, cjk: bool) -> dict:
    """{name, opts} for \\setmainfont{name}[opts]; unknown names are custom fonts, passed as is."""
    name = re.sub(r"[\\{}\[\]%#$^&~]", "", name)
    f = _BY_NAME.get(name)
    if f is None or name not in _available():
        return {"name": name, "opts": "AutoFakeBold=2" if cjk else ""}
    if "stem" in f:
        r, b, i, bi = f["styles"]
        opts = [f"Extension={f['ext']}", f"UprightFont=*{r}"]
        opts += [f"BoldFont=*{b}"] if b else (["AutoFakeBold=2"] if cjk else [])
        if i:
            opts += [f"ItalicFont=*{i}", f"BoldItalicFont=*{bi}"]
        return {"name": f["stem"], "opts": ", ".join(opts)}
    return {"name": name, "opts": "AutoFakeBold=2" if f.get("bold") is False else ""}


def resolve(layout: dict, has_cjk: bool) -> dict:
    """The fonts one render uses: {main: spec|None, cjk: spec|None, cjk_fallback: name|None}."""
    have = _available()
    latin = str(layout.get("font") or "").strip() or (DEFAULT_LATIN_CJK if has_cjk and DEFAULT_LATIN_CJK in have else "")
    out = {"main": _spec(latin, False) if latin else None, "cjk": None, "cjk_fallback": None}
    if has_cjk:
        cjk = str(layout.get("cjk_font") or "").strip()
        if not cjk:
            cjk = next((f["name"] for f in CJK if f["name"] in have), "FandolSong")
        out["cjk"] = _spec(cjk, True)
        if cjk.startswith("Fandol"):
            out["cjk_fallback"] = next((n for n in _CJK_FALLBACK if n in have), None)
    return out
