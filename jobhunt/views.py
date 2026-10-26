"""The tables' views (docs/JOB_HUNT.md, 2026-10-19): like a Notion database, each table has several views,
each with its own layout (table, board, calendar or Gantt), columns (order, width, hidden, wrapped), filters, sorts and group.
They belong to the data, so they live in the data folder:

    .studio/hunt/views.json   {"apps": {"active": "v1", "views": [{id, name, layout, columns, filters, sorts, group}]},
                               "companies": {...}, "platforms": {...}}

The window owns the defaults (it knows each table's properties); this keeps what it saves, cleaned."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from core.i18n import t

TABLES = ("apps", "companies", "platforms")
LAYOUTS = ("table", "board", "calendar", "gantt", "map")
OPS = ("is", "is_not", "contains", "not_contains", "before", "after", "empty", "not_empty")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _s(v: Any, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


def _key(v: Any) -> str:
    k = _s(v, 40)
    return k if KEY.match(k) else ""


def clean_view(v: Any, n: int) -> dict:
    v = v if isinstance(v, dict) else {}
    columns = [c for c in v.get("columns") or [] if isinstance(c, dict)]
    if any(c.get("key") == "deadline" for c in columns):
        # a role's deadline column (until 2026-10-21) became its next step: that column takes its place
        ev = next((c for c in columns if c.get("key") == "events"), {})
        columns = [{**ev, "key": "events"} if c.get("key") == "deadline" else c for c in columns if c.get("key") != "events"]
        v = {**v, "filters": [f for f in v.get("filters") or [] if not (isinstance(f, dict) and f.get("key") == "deadline")],
             "sorts": [{**s, "key": "events"} if isinstance(s, dict) and s.get("key") == "deadline" else s for s in v.get("sorts") or []]}
    cols, seen = [], set()
    for c in columns:
        if not isinstance(c, dict) or not _key(c.get("key")) or c["key"] in seen:
            continue
        seen.add(c["key"])
        w = c.get("width")
        cols.append({"key": c["key"], "width": max(60, min(900, int(w))) if isinstance(w, (int, float)) else None,
                     "hidden": bool(c.get("hidden")), "wrap": bool(c.get("wrap"))})
    filters = []
    for f in v.get("filters") or []:
        if not isinstance(f, dict) or not _key(f.get("key")) or f.get("op") not in OPS:
            continue
        val = f.get("value")
        val = [_s(x, 80) for x in val][:30] if isinstance(val, list) else _s(val, 120)
        filters.append({"key": f["key"], "op": f["op"], "value": val})
    sorts = [{"key": s["key"], "dir": "desc" if s.get("dir") == "desc" else "asc"}
             for s in (v.get("sorts") or []) if isinstance(s, dict) and _key(s.get("key"))][:5]
    return {"id": _s(v.get("id"), 20) or f"v{n}", "name": _s(v.get("name"), 40) or t("Table"),
            "layout": v.get("layout") if v.get("layout") in LAYOUTS else "table",
            "columns": cols[:40], "filters": filters[:20], "sorts": sorts,
            "group": _key(v.get("group")), "folded": [_s(x, 80) for x in (v.get("folded") or [])][:50],
            # columns kept on screen from the left while the table scrolls sideways (the title alone by default)
            "freeze": v["freeze"] if isinstance(v.get("freeze"), int) and not isinstance(v.get("freeze"), bool) and 0 <= v["freeze"] <= 40 else 1}


def clean_table(v: Any) -> dict:
    v = v if isinstance(v, dict) else {}
    views, ids = [], set()
    for i, x in enumerate((v.get("views") or [])[:20]):
        cv = clean_view(x, i + 1)
        while cv["id"] in ids:
            cv["id"] += "x"
        ids.add(cv["id"])
        views.append(cv)
    if not views:
        raise ValueError(t("A table keeps at least one view"))
    active = _s(v.get("active"), 20)
    return {"active": active if active in ids else views[0]["id"], "views": views}


class Views:
    def __init__(self, hunt_dir: Path) -> None:
        self.path = Path(hunt_dir) / "views.json"
        self.lock = threading.Lock()

    def all(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out = {}
        for k in TABLES:
            try:
                if k in data:
                    out[k] = clean_table(data[k])
            except ValueError:
                pass
        return out

    def save(self, table: str, value: Any) -> dict:
        if table not in TABLES:
            raise KeyError(t("No table “{name}”", name=table))
        tb = clean_table(value)
        with self.lock:
            data = self.all()
            data[table] = tb
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        return tb
