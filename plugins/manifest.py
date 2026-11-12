"""A plugin's manifest (plugin.json): its schema, checked strictly, so a plugin from the market is data and nothing
else (docs/PLUGINS.md). Standard library only: scripts/plugin.py uses it without the venv."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FORMAT = 1                                    # the plugin format this app reads ("app": ">=1")
LANGS = ("en", "zh", "ja", "ko", "de", "fr", "es")
TYPES = ("title", "text", "longtext", "number", "url", "date", "select", "relation")
COLORS = ("gray", "blue", "green", "yellow", "orange", "red", "purple")
LAYOUTS = ("table", "board", "calendar", "gantt")
ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FILES = re.compile(r"^(plugin\.json|[A-Za-z0-9_-]{1,40}\.md|icon\.(svg|png))$")
RESERVED = {"id", "created", "updated", "by"}   # a record's own keys
MAX_MANIFEST, MAX_PLUGIN = 256 * 1024, 2 * 1024 * 1024


class ManifestError(ValueError):
    pass


def local(text: Any, lang: str) -> str:
    """A {lang: text} label in `lang`, else English, else any."""
    if isinstance(text, str):
        return text
    if not isinstance(text, dict) or not text:
        return ""
    return str(text.get(lang) or text.get("en") or next(iter(text.values())))


def _keys(obj: dict, allowed: set[str], where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise ManifestError(f"{where}: unknown key {sorted(extra)[0]!r}")


def _label(v: Any, where: str, required: bool = True) -> None:
    if v is None and not required:
        return
    if not isinstance(v, dict) or not v or not all(isinstance(k, str) and isinstance(x, str) and x.strip() for k, x in v.items()):
        raise ManifestError(f"{where}: a label is a {{language: text}} map with English at least")
    if "en" not in v:
        raise ManifestError(f"{where}: the label needs English (\"en\")")
    if any(len(x) > 2000 for x in v.values()):
        raise ManifestError(f"{where}: a label is too long")


def _version(v: Any) -> tuple[int, ...]:
    if not isinstance(v, str) or not re.match(r"^\d+\.\d+\.\d+$", v):
        raise ManifestError("version: three numbers, like 1.0.0")
    return tuple(int(x) for x in v.split("."))


def version_key(v: str) -> tuple[int, ...]:
    try:
        return _version(v)
    except ManifestError:
        return (0, 0, 0)


def check(m: Any) -> dict:
    """The manifest as given, once it passes; ManifestError says what is wrong."""
    if not isinstance(m, dict):
        raise ManifestError("plugin.json must be a JSON object")
    if "service" in m:
        raise ManifestError("service: paid services are not supported yet")
    _keys(m, {"id", "version", "app", "name", "description", "author", "homepage", "collections", "suggestions", "guide"}, "plugin")
    if not isinstance(m.get("id"), str) or not ID.match(m["id"]):
        raise ManifestError("id: lowercase letters, digits and -, at most 40")
    _version(m.get("version"))
    app = m.get("app", ">=1")
    if not isinstance(app, str) or not re.match(r"^>=\d+$", app) or int(app[2:]) > FORMAT:
        raise ManifestError(f"app: needs a newer Resume Studio (this one reads format {FORMAT})")
    _label(m.get("name"), "name")
    _label(m.get("description"), "description")
    for k in ("author", "homepage"):
        if k in m and (not isinstance(m[k], str) or len(m[k]) > 200):
            raise ManifestError(f"{k}: a short text")
    if "homepage" in m and not re.match(r"^https://", m["homepage"]):
        raise ManifestError("homepage: an https:// address")
    if "guide" in m and (not isinstance(m["guide"], str) or not FILES.match(m["guide"]) or not m["guide"].endswith(".md")):
        raise ManifestError("guide: a .md file in the plugin's folder")
    sug = m.get("suggestions", [])
    if not isinstance(sug, list) or len(sug) > 6:
        raise ManifestError("suggestions: a list of at most 6")
    for i, s in enumerate(sug):
        _label(s, f"suggestions[{i}]")
    cols = m.get("collections")
    if not isinstance(cols, list) or not 1 <= len(cols) <= 6:
        raise ManifestError("collections: 1 to 6")
    seen = set()
    for c in cols:
        _collection(c, seen)
    for c in cols:   # a relation links to another collection of the same plugin
        for p in c["properties"]:
            if p["type"] == "relation" and p["to"] not in seen:
                raise ManifestError(f"collection {c['id']}.{p['key']}: to names a collection of this plugin, not {p['to']!r}")
    return m


def _collection(c: Any, seen: set) -> None:
    if not isinstance(c, dict):
        raise ManifestError("a collection is an object")
    _keys(c, {"id", "name", "item", "properties", "status", "dates", "views"}, "collection")
    cid = c.get("id")
    if not isinstance(cid, str) or not KEY.match(cid) or cid in seen:
        raise ManifestError(f"collection id {cid!r}: lowercase, unique")
    seen.add(cid)
    where = f"collection {cid}"
    _label(c.get("name"), where + " name")
    _label(c.get("item"), where + " item")
    props = c.get("properties")
    if not isinstance(props, list) or not 1 <= len(props) <= 40:
        raise ManifestError(f"{where}: 1 to 40 properties")
    keys: dict[str, dict] = {}
    for p in props:
        if not isinstance(p, dict):
            raise ManifestError(f"{where}: a property is an object")
        _keys(p, {"key", "type", "label", "options", "free", "hint", "width", "hidden", "to", "multiple"}, f"{where} property")
        k = p.get("key")
        if not isinstance(k, str) or not KEY.match(k) or k in keys or k in RESERVED:
            raise ManifestError(f"{where}: property key {k!r}: lowercase, unique, not {sorted(RESERVED)}")
        if p.get("type") not in TYPES:
            raise ManifestError(f"{where}.{k}: type is one of {', '.join(TYPES)}")
        _label(p.get("label"), f"{where}.{k}")
        _label(p.get("hint"), f"{where}.{k} hint", required=False)
        if "width" in p and (not isinstance(p["width"], int) or not 60 <= p["width"] <= 600):
            raise ManifestError(f"{where}.{k}: width 60 to 600")
        if "hidden" in p and not isinstance(p["hidden"], bool):
            raise ManifestError(f"{where}.{k}: hidden is true or false")
        if p["type"] == "select":
            opts = p.get("options")
            if not isinstance(opts, list) or not 1 <= len(opts) <= 40:
                raise ManifestError(f"{where}.{k}: a select needs 1 to 40 options")
            vals = set()
            for o in opts:
                if not isinstance(o, dict):
                    raise ManifestError(f"{where}.{k}: an option is an object")
                _keys(o, {"value", "label", "color"}, f"{where}.{k} option")
                if not isinstance(o.get("value"), str) or not KEY.match(o["value"]) or o["value"] in vals:
                    raise ManifestError(f"{where}.{k}: option value {o.get('value')!r}: lowercase, unique")
                vals.add(o["value"])
                _label(o.get("label"), f"{where}.{k}.{o['value']}")
                if "color" in o and o["color"] not in COLORS:
                    raise ManifestError(f"{where}.{k}.{o['value']}: color is one of {', '.join(COLORS)}")
            if "free" in p and not isinstance(p["free"], bool):
                raise ManifestError(f"{where}.{k}: free is true or false")
        elif "options" in p or "free" in p:
            raise ManifestError(f"{where}.{k}: only a select has options")
        if p["type"] == "relation":
            if not isinstance(p.get("to"), str) or not KEY.match(p["to"]):
                raise ManifestError(f"{where}.{k}: a relation names the collection it links to (to)")
            if "multiple" in p and not isinstance(p["multiple"], bool):
                raise ManifestError(f"{where}.{k}: multiple is true or false")
        elif "to" in p or "multiple" in p:
            raise ManifestError(f"{where}.{k}: only a relation has to and multiple")
        keys[k] = p
    if sum(p["type"] == "title" for p in keys.values()) != 1:
        raise ManifestError(f"{where}: exactly one title property")
    st = c.get("status")
    if st is not None and (st not in keys or keys[st]["type"] != "select"):
        raise ManifestError(f"{where}: status names a select property")
    dates = c.get("dates", [])
    if not isinstance(dates, list) or any(d not in keys or keys[d]["type"] != "date" for d in dates):
        raise ManifestError(f"{where}: dates name date properties")
    views = c.get("views", [{"id": "v1", "name": {"en": "All"}}])
    if not isinstance(views, list) or not 1 <= len(views) <= 8:
        raise ManifestError(f"{where}: 1 to 8 views")
    vids = set()
    for v in views:
        if not isinstance(v, dict):
            raise ManifestError(f"{where}: a view is an object")
        _keys(v, {"id", "name", "layout", "group"}, f"{where} view")
        if not isinstance(v.get("id"), str) or not KEY.match(v["id"]) or v["id"] in vids:
            raise ManifestError(f"{where}: view id {v.get('id')!r}: lowercase, unique")
        vids.add(v["id"])
        _label(v.get("name"), f"{where} view {v['id']}")
        if v.get("layout", "table") not in LAYOUTS or (v.get("layout") in ("calendar", "gantt") and not dates):
            raise ManifestError(f"{where} view {v['id']}: layout is one of {', '.join(LAYOUTS)} (calendar and Gantt need dates)")
        if "group" in v and (v["group"] not in keys or keys[v["group"]]["type"] != "select"):
            raise ManifestError(f"{where} view {v['id']}: group names a select property")


def read(folder: Path) -> dict:
    """A plugin folder's manifest, checked with its files: only what a plugin may hold, within the sizes."""
    folder = Path(folder)
    total = 0
    for f in folder.rglob("*"):
        rel = f.relative_to(folder).as_posix()
        if f.is_dir() or f.is_symlink() or "/" in rel or not FILES.match(rel):
            raise ManifestError(f"{rel}: a plugin holds only plugin.json, .md files and icon.svg / icon.png")
        total += f.stat().st_size
    if total > MAX_PLUGIN:
        raise ManifestError("the plugin is larger than 2 MB")
    path = folder / "plugin.json"
    if not path.is_file():
        raise ManifestError("plugin.json is missing")
    if path.stat().st_size > MAX_MANIFEST:
        raise ManifestError("plugin.json is larger than 256 KB")
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ManifestError(f"plugin.json isn't valid JSON: {e}")
    check(m)
    if m.get("guide") and not (folder / m["guide"]).is_file():
        raise ManifestError(f"guide: {m['guide']} is missing")
    return m


def collection(m: dict, cid: str) -> dict:
    for c in m["collections"]:
        if c["id"] == cid:
            return c
    raise KeyError(f"no collection {cid!r} in {m['id']}")
