"""List every interface string that needs a translation, and check the catalogs.

    python scripts/i18n_keys.py            # report missing / unused keys per language
    python scripts/i18n_keys.py --json     # the keys as JSON ({key: plural-or-null})

Keys are the English text passed to t() in Python (core/, resume/, scripts/) and in the page
(web/*.js), plus a few tables whose values go through t() indirectly (font descriptions,
template descriptions, slider labels…). English needs no catalog: the key is the text.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGS = ROOT / "web" / "i18n"
PY_DIRS = [ROOT / "core", ROOT / "resume", ROOT / "jobs", ROOT / "autofill", ROOT / "jobhunt", ROOT / "scripts"]
T_NAMES = {"t", "_t"}

_JS_STR = r'"((?:[^"\\]|\\.)*)"'
_JS_CALL = re.compile(r"\bt\(\s*" + _JS_STR + r"(?:\s*,\s*\{[^{}]*\}\s*,\s*" + _JS_STR + r")?")
# tables whose values are translated where they are used
_JS_TABLES = {
    "app.js": ["NOTE_TIPS", "AI_LABELS", "FONT_GROUPS", "CHANGE_NAMES"],
    "chat.js": ["BUILTIN_DESC"],
    "jobs.js": ["STATE_NAMES", "DECISIONS", "IMPORTANCE", "VALUE_NAMES", "CHECK_NAMES", "STEP_NAMES", "MODE_NAMES", "WEB_STEPS"],
    "autofill.js": ["OPS", "APPLIED", "SECTION_NAMES", "FIELD_NAMES", "ADD_ONE"],
    "hunt.js": ["HUNT_TABS", "HSTATES", "HCHANNELS", "SITE_KINDS", "BATCH_NAMES"],
}
_JS_GROUPS = {"jobs.js": ["MODE_GROUPS"]}   # [["Group title", [...]], ...]


def _py_keys(path: Path) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if name not in T_NAMES or not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        other = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
        out[node.args[0].value] = other
    return out


def _js_unescape(s: str) -> str:
    return json.loads('"' + s + '"')


def _js_keys(path: Path) -> dict[str, str | None]:
    src = path.read_text(encoding="utf-8")
    out = {_js_unescape(m.group(1)): (_js_unescape(m.group(2)) if m.group(2) else None) for m in _JS_CALL.finditer(src)}
    for table in _JS_TABLES.get(path.name, []):
        m = re.search(r"const " + table + r" = \{(.*?)\};", src, re.S)
        for v in re.findall(r":\s*" + _JS_STR, m.group(1) if m else ""):
            out.setdefault(_js_unescape(v), None)
    for table in _JS_GROUPS.get(path.name, []):
        m = re.search(r"const " + table + r" = \[(.*?)\];", src, re.S)
        for v in re.findall(r"\[\s*" + _JS_STR + r"\s*,\s*\[", m.group(1) if m else ""):
            out.setdefault(_js_unescape(v), None)
    m = re.search(r"const SLIDERS = \[(.*?)\];", src, re.S)
    for label in re.findall(r'\["\w+",\s*' + _JS_STR, m.group(1) if m else ""):
        out.setdefault(_js_unescape(label), None)
    m = re.search(r"const SWATCHES = \[(.*?)\];", src, re.S)
    for name in re.findall(r'\["[0-9A-F]{6}",\s*' + _JS_STR, m.group(1) if m else ""):
        out.setdefault(_js_unescape(name), None)
    return out


def _html_keys(path: Path) -> dict[str, str | None]:
    src = path.read_text(encoding="utf-8")
    keys = re.findall(r'(?:title|placeholder|aria-label)="([^"]+)"', src)
    keys += re.findall(r"<[^>]*\bdata-t\b[^>]*>([^<]+)<", src)
    return {k.strip(): None for k in keys if k.strip() and k.strip() != "Resume Studio"}


def _data_keys() -> dict[str, str | None]:
    """Descriptions that live in data: fonts, templates, Claude Code's model list."""
    sys.path.insert(0, str(ROOT))
    from core import claude_info
    from resume.render import fonts
    from resume.render.latex import TEMPLATE_DIR, _read_meta
    out: dict[str, str | None] = {}
    for f in fonts.LATIN + fonts.CJK:
        if f.get("desc"):
            out[f["desc"]] = None
    for p in sorted(TEMPLATE_DIR.glob("*.tex.j2")):
        if not p.name.startswith("_"):
            out[_read_meta(p)["description"]] = None
    for m in claude_info.LATEST:
        out[m["desc"]] = None
        if m.get("tag"):
            out[m["tag"]] = None
    out["Claude Code default"] = None
    out["~2× usage vs Sonnet"] = None
    return out


def all_keys() -> dict[str, str | None]:
    keys: dict[str, str | None] = {}
    for d in PY_DIRS:
        for p in sorted(d.rglob("*.py")):
            keys.update(_py_keys(p))
    for p in sorted((ROOT / "web").glob("*.js")):
        keys.update(_js_keys(p))
    keys.update(_html_keys(ROOT / "web" / "index.html"))
    keys.update(_data_keys())
    return dict(sorted(keys.items()))


_PH = re.compile(r"\{\w+\}")


def _bad_value(key: str, plural: str | None, value) -> str | None:
    """Why a translation can't be used, or None."""
    forms = value if isinstance(value, list) else [value]
    if not forms or not all(isinstance(f, str) and f for f in forms) or len(forms) > 2:
        return "empty or malformed"
    if isinstance(value, list) and plural is None:
        return "plural forms for a key without a count"
    want = set(_PH.findall(key)) | set(_PH.findall(plural or ""))
    for f in forms:
        if set(_PH.findall(f)) != want:
            return f"placeholders {sorted(set(_PH.findall(f)))} != {sorted(want)}"
    return None


def check() -> dict[str, dict]:
    keys = all_keys()
    report = {}
    for path in sorted(CATALOGS.glob("*.json")):
        cat = json.loads(path.read_text(encoding="utf-8"))
        report[path.stem] = {"missing": [k for k in keys if k not in cat], "unused": [k for k in cat if k not in keys],
                             "bad": {k: why for k, v in cat.items() if k in keys and (why := _bad_value(k, keys[k], v))}}
    return report


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(all_keys(), ensure_ascii=False, indent=1))
    else:
        keys = all_keys()
        print(f"{len(keys)} keys")
        for lang, r in check().items():
            print(f"{lang}: {len(r['missing'])} missing, {len(r['unused'])} unused, {len(r['bad'])} bad")
            for k in r["missing"][:20]:
                print("   missing:", k)
            for k in r["unused"][:20]:
                print("   unused:", k)
            for k, why in list(r["bad"].items())[:20]:
                print("   bad:", k, "->", why)
