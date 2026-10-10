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
# desc is English; the page translates it like any other text
LATIN = [
    {"name": "Source Serif 4", "kind": "serif", "desc": "Modern serif with sturdy strokes; pairs well with CJK serifs; covers Greek and Cyrillic",
     **_files("SourceSerif4-", ".otf", "Regular", "Bold", "RegularIt", "BoldIt")},
    {"name": "TeX Gyre Termes", "kind": "serif", "desc": "Times style, classic academic",
     **_files("texgyretermes-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "TeX Gyre Pagella", "kind": "serif", "desc": "Palatino style, open and elegant",
     **_files("texgyrepagella-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "Libertinus Serif", "kind": "serif", "desc": "Successor of Linux Libertine, compact; covers Greek and Cyrillic",
     **_files("LibertinusSerif-", ".otf", "Regular", "Bold", "Italic", "BoldItalic")},
    {"name": "Latin Modern Roman", "kind": "serif", "desc": "LaTeX default, thin strokes",
     **_files("lmroman10-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "Times New Roman", "kind": "serif", "desc": "System font", "sys": ["times.ttf", "Times New Roman.ttf"]},
    {"name": "Georgia", "kind": "serif", "desc": "System font, easy on screens", "sys": ["georgia.ttf", "Georgia.ttf"]},
    {"name": "Cambria", "kind": "serif", "desc": "System font, Office's default serif", "sys": ["cambria.ttc"]},
    {"name": "Source Sans 3", "kind": "sans", "desc": "Clean humanist sans; pairs well with CJK sans fonts",
     **_files("SourceSans3-", ".otf", "Regular", "Bold", "RegularIt", "BoldIt")},
    {"name": "TeX Gyre Heros", "kind": "sans", "desc": "Helvetica style",
     **_files("texgyreheros-", ".otf", "regular", "bold", "italic", "bolditalic")},
    {"name": "Fira Sans", "kind": "sans", "desc": "Technical feel",
     **_files("FiraSans-", ".otf", "Regular", "Bold", "Italic", "BoldItalic")},
    {"name": "Lato", "kind": "sans", "desc": "Rounded and friendly",
     **_files("Lato-", ".ttf", "Regular", "Bold", "Italic", "BoldItalic")},
    {"name": "Arial", "kind": "sans", "desc": "System font", "sys": ["arial.ttf", "Arial.ttf"]},
    {"name": "Calibri", "kind": "sans", "desc": "System font, Office's default", "sys": ["calibri.ttf"]},
]

# lang: the CJK script a font is made for (langs.py scripts: zh, zh-tw, ja, ko)
CJK = [
    {"name": "FandolSong", "label": "Fandol 宋体", "lang": "zh", "kind": "serif", "desc": "Ships with TeX Live, real bold",
     **_files("FandolSong-", ".otf", "Regular", "Bold", None, None)},
    {"name": "STZhongsong", "label": "华文中宋", "lang": "zh", "kind": "serif", "desc": "Heavier than Song, prints clearly", "bold": False, "sys": ["STZHONGS.TTF"]},
    {"name": "STSong", "label": "华文宋体", "lang": "zh", "kind": "serif", "desc": "", "bold": False, "sys": ["STSONG.TTF"]},
    {"name": "SimSun", "label": "中易宋体", "lang": "zh", "kind": "serif", "desc": "Windows Song, the thinnest strokes", "bold": False, "sys": ["simsun.ttc"]},
    {"name": "Songti SC", "label": "宋体-简", "lang": "zh", "kind": "serif", "desc": "macOS", "sys": ["Songti.ttc"]},
    {"name": "Noto Serif CJK SC", "label": "思源宋体", "lang": "zh", "kind": "serif", "desc": "", "sys": ["NotoSerifCJK-Regular.ttc"]},
    {"name": "Microsoft YaHei", "label": "微软雅黑", "lang": "zh", "kind": "sans", "desc": "Windows default Hei, on the bold side", "sys": ["msyh.ttc"]},
    {"name": "DengXian", "label": "等线", "lang": "zh", "kind": "sans", "desc": "Thinner and more modern than YaHei", "sys": ["Deng.ttf"]},
    {"name": "FandolHei", "label": "Fandol 黑体", "lang": "zh", "kind": "sans", "desc": "Ships with TeX Live",
     **_files("FandolHei-", ".otf", "Regular", "Bold", None, None)},
    {"name": "SimHei", "label": "黑体", "lang": "zh", "kind": "sans", "desc": "", "bold": False, "sys": ["simhei.ttf"]},
    {"name": "PingFang SC", "label": "苹方", "lang": "zh", "kind": "sans", "desc": "macOS", "sys": ["PingFang.ttc"]},
    {"name": "Noto Sans CJK SC", "label": "思源黑体", "lang": "zh", "kind": "sans", "desc": "", "sys": ["NotoSansCJK-Regular.ttc"]},
    {"name": "KaiTi", "label": "楷体", "lang": "zh", "kind": "kai", "desc": "", "bold": False, "sys": ["simkai.ttf"]},
    {"name": "FangSong", "label": "仿宋", "lang": "zh", "kind": "fang", "desc": "", "bold": False, "sys": ["simfang.ttf"]},
    {"name": "Microsoft JhengHei", "label": "微軟正黑體", "lang": "zh-tw", "kind": "sans", "desc": "Windows", "sys": ["msjh.ttc"]},
    {"name": "MingLiU", "label": "細明體", "lang": "zh-tw", "kind": "serif", "desc": "Windows", "bold": False, "sys": ["mingliu.ttc"]},
    {"name": "Songti TC", "label": "宋體-繁", "lang": "zh-tw", "kind": "serif", "desc": "macOS", "sys": ["Songti.ttc"]},
    {"name": "PingFang TC", "label": "蘋方-繁", "lang": "zh-tw", "kind": "sans", "desc": "macOS", "sys": ["PingFang.ttc"]},
    {"name": "Noto Serif CJK TC", "label": "思源宋體", "lang": "zh-tw", "kind": "serif", "desc": "", "sys": ["NotoSerifCJK-Regular.ttc"]},
    {"name": "Noto Sans CJK TC", "label": "思源黑體", "lang": "zh-tw", "kind": "sans", "desc": "", "sys": ["NotoSansCJK-Regular.ttc"]},
    {"name": "HaranoAjiMincho", "label": "原ノ味明朝", "lang": "ja", "kind": "serif", "desc": "Ships with TeX Live, real bold",
     **_files("HaranoAjiMincho-", ".otf", "Regular", "Bold", None, None)},
    {"name": "HaranoAjiGothic", "label": "原ノ味角ゴシック", "lang": "ja", "kind": "sans", "desc": "Ships with TeX Live, real bold",
     **_files("HaranoAjiGothic-", ".otf", "Regular", "Bold", None, None)},
    {"name": "Yu Mincho", "label": "游明朝", "lang": "ja", "kind": "serif", "desc": "Windows", "sys": ["yumin.ttf"]},
    {"name": "Yu Gothic", "label": "游ゴシック", "lang": "ja", "kind": "sans", "desc": "Windows", "sys": ["YuGothR.ttc"]},
    {"name": "MS Mincho", "label": "ＭＳ 明朝", "lang": "ja", "kind": "serif", "desc": "Windows", "bold": False, "sys": ["msmincho.ttc"]},
    {"name": "Meiryo", "label": "メイリオ", "lang": "ja", "kind": "sans", "desc": "Windows", "sys": ["meiryo.ttc"]},
    {"name": "Hiragino Mincho ProN", "label": "ヒラギノ明朝", "lang": "ja", "kind": "serif", "desc": "macOS", "sys": ["ヒラギノ明朝 ProN.ttc"]},
    {"name": "Noto Serif CJK JP", "label": "Noto Serif CJK JP", "lang": "ja", "kind": "serif", "desc": "", "sys": ["NotoSerifCJK-Regular.ttc"]},
    {"name": "UnBatang", "label": "은바탕", "lang": "ko", "kind": "serif", "desc": "Ships with TeX Live",
     **_files("UnBatang", ".ttf", "", "Bold", None, None)},
    {"name": "UnDotum", "label": "은돋움", "lang": "ko", "kind": "sans", "desc": "Ships with TeX Live",
     **_files("UnDotum", ".ttf", "", "Bold", None, None)},
    {"name": "Malgun Gothic", "label": "맑은 고딕", "lang": "ko", "kind": "sans", "desc": "Windows", "sys": ["malgun.ttf"]},
    {"name": "Batang", "label": "바탕", "lang": "ko", "kind": "serif", "desc": "Windows", "bold": False, "sys": ["batang.ttc"]},
    {"name": "AppleMyungjo", "label": "애플명조", "lang": "ko", "kind": "serif", "desc": "macOS", "sys": ["AppleMyungjo.ttf"]},
    {"name": "Noto Serif CJK KR", "label": "Noto Serif CJK KR", "lang": "ko", "kind": "serif", "desc": "", "sys": ["NotoSerifCJK-Regular.ttc"]},
]

# A CJK resume: a CJK serif + a Latin serif of matching weight. Latin-script English keeps LaTeX's
# own default; Greek, Cyrillic and Vietnamese need a font that has their letters.
DEFAULT_LATIN_CJK = "Source Serif 4"
DEFAULT_LATIN_WIDE = "Source Serif 4"
WIDE_SCRIPTS = {"greek", "cyrillic"}
# the default CJK font per script: the first one installed
DEFAULT_CJK_BY_SCRIPT = {
    "zh": ["FandolSong"],
    "zh-tw": ["Noto Serif CJK TC", "Songti TC", "MingLiU", "Microsoft JhengHei", "FandolSong"],
    "ja": ["HaranoAjiMincho", "Yu Mincho", "Hiragino Mincho ProN", "MS Mincho", "Noto Serif CJK JP"],
    "ko": ["UnBatang", "Batang", "AppleMyungjo", "Noto Serif CJK KR", "Malgun Gothic"],
}
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


def default_cjk(script: str) -> str:
    have = _available()
    names = DEFAULT_CJK_BY_SCRIPT.get(script) or DEFAULT_CJK_BY_SCRIPT["zh"]
    return next((n for n in names if n in have), None) or next((f["name"] for f in CJK if f["name"] in have), DEFAULT_CJK)


def default_latin(script: str, has_cjk: bool) -> str:
    """'' = the template's own font (Latin Modern)."""
    if has_cjk or script in WIDE_SCRIPTS or script == "latin-wide":
        return DEFAULT_LATIN_CJK if DEFAULT_LATIN_CJK in _available() else ""
    return ""


def catalog() -> dict:
    """What the UI lists: only installed fonts, plus the defaults for each script."""
    have = _available()
    pick = lambda fonts: [{"name": f["name"], "label": f.get("label") or f["name"], "kind": f["kind"], "desc": f["desc"],
                           **({"lang": f["lang"]} if "lang" in f else {})} for f in fonts if f["name"] in have]
    return {"latin": pick(LATIN), "cjk": pick(CJK), "default_latin_cjk": DEFAULT_LATIN_CJK,
            "default_latin_wide": DEFAULT_LATIN_WIDE if DEFAULT_LATIN_WIDE in have else "",
            "default_cjk": DEFAULT_CJK, "default_cjk_by_script": {s: default_cjk(s) for s in DEFAULT_CJK_BY_SCRIPT}}


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


def resolve(layout: dict, has_cjk: bool, script: str = "latin") -> dict:
    """The fonts one render uses: {main: spec|None, cjk: spec|None, cjk_fallback: name|None}.
    `script` is the resume language's (langs.py): it picks the default fonts."""
    have = _available()
    latin = str(layout.get("font") or "").strip() or default_latin(script, has_cjk)
    out = {"main": _spec(latin, False) if latin else None, "cjk": None, "cjk_fallback": None}
    if has_cjk:
        cjk = str(layout.get("cjk_font") or "").strip() or default_cjk(script if script in DEFAULT_CJK_BY_SCRIPT else "zh")
        out["cjk"] = _spec(cjk, True)
        if cjk.startswith("Fandol"):
            out["cjk_fallback"] = next((n for n in _CJK_FALLBACK if n in have), None)
    return out
