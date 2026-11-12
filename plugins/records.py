"""A plugin's records in the data folder (resume-studio-data/plugins/<id>.json, docs/PLUGINS.md): read, added,
changed and deleted by the window and by the AI (scripts/plugin.py), every value checked against its property.
Standard library only."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.i18n import t

from . import manifest

_locks: dict[str, threading.RLock] = {}
_locks_lock = threading.Lock()


def _lock(path: Path) -> threading.RLock:
    with _locks_lock:
        return _locks.setdefault(str(path), threading.RLock())


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


class Records:
    def __init__(self, state_dir: Path, plugin: dict):
        self.m = plugin
        self.path = Path(state_dir) / "plugins" / f"{plugin['id']}.json"
        self.lock = _lock(self.path)

    # ---- the file ----
    def _load(self) -> dict:
        for attempt in range(40):
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                break
            except FileNotFoundError:
                data = {}
                break
            except PermissionError:   # Windows: _save is replacing it
                if attempt == 39:
                    raise
                time.sleep(0.025)
            except ValueError:
                raise ValueError(t("{file} isn't valid JSON; fix or remove it", file=self.path.name))
        data = data if isinstance(data, dict) else {}
        data["next"] = data.get("next") if isinstance(data.get("next"), int) else 1
        for c in self.m["collections"]:
            data[c["id"]] = [r for r in data.get(c["id"]) or [] if isinstance(r, dict) and r.get("id")]
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def signature(self) -> tuple[int, int]:
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    # ---- reading ----
    def all(self) -> dict[str, list[dict]]:
        data = self._load()
        return {c["id"]: data[c["id"]] for c in self.m["collections"]}

    def rows(self, cid: str) -> list[dict]:
        manifest.collection(self.m, cid)
        return self._load()[cid]

    def get(self, cid: str, rid: str) -> dict:
        for r in self.rows(cid):
            if r["id"] == rid:
                return r
        raise KeyError(f"no record {rid!r} in {self.m['id']} {cid}")

    # ---- checking ----
    def clean(self, cid: str, fields: Any, *, new: bool, data: dict | None = None) -> dict:
        c = manifest.collection(self.m, cid)
        if not isinstance(fields, dict):
            raise ValueError(t("The fields are a JSON object"))
        props = {p["key"]: p for p in c["properties"]}
        out: dict[str, Any] = {}
        for k, v in fields.items():
            if k in manifest.RESERVED:
                continue
            p = props.get(k)
            if not p:
                raise ValueError(t("{plugin} {collection} has no property “{name}”; it has: {keys}", plugin=self.m["id"], collection=cid, name=k, keys=", ".join(props)))
            out[k] = self._link(p, v, data if data is not None else self._load()) if p["type"] == "relation" else value(p, v)
        title = next(p["key"] for p in c["properties"] if p["type"] == "title")
        if new and not out.get(title):
            raise ValueError(t("Give it a {field}", field=title))
        if not new and title in out and not out[title]:
            raise ValueError(t("The {field} can't be empty", field=title))
        return out

    def _link(self, p: dict, v: Any, data: dict) -> Any:
        """A relation's value: the id of a record in the collection it links to ("R3"), or that record's exact title
        (what the AI knows a university by); a list of them when it links to several. "" or [] clears it."""
        target = manifest.collection(self.m, p["to"])
        tkey = next(x["key"] for x in target["properties"] if x["type"] == "title")
        rows = data[target["id"]]
        many = bool(p.get("multiple"))
        xs = v if isinstance(v, list) else [] if v in (None, "") else [v]
        if not many and len(xs) > 1:
            raise ValueError(t("{field}: one record, not a list", field=p["key"]))
        out: list[str] = []
        for x in xs:
            s = " ".join(str(x).split()) if isinstance(x, (str, int)) and not isinstance(x, bool) else None
            if s is None:
                raise ValueError(t("{field}: a record's id or title", field=p["key"]))
            if not s:
                continue
            hit = next((r for r in rows if r["id"] == s), None) or next(
                (r for r in rows if str(r.get(tkey) or "").casefold() == s.casefold()), None)
            if not hit:
                raise ValueError(t("{field}: {collection} has no record “{value}”", field=p["key"],
                                   collection=manifest.local(target["name"], "en"), value=s))
            if hit["id"] not in out:
                out.append(hit["id"])
        return out if many else (out[0] if out else "")

    # ---- writing ----
    def add(self, cid: str, fields: Any, by: str = "you") -> dict:
        with self.lock:
            data = self._load()
            row = {k: v for k, v in self.clean(cid, fields, new=True, data=data).items() if v not in ("", None, [])}
            rid = f"R{data['next']}"
            data["next"] += 1
            now = _now()
            row = {"id": rid, **row, "created": now, "updated": now, "by": by}
            data[cid].append(row)
            self._save(data)
            return row

    def edit(self, cid: str, rid: str, fields: Any, by: str = "you") -> dict:
        with self.lock:
            data = self._load()
            ch = self.clean(cid, fields, new=False, data=data)
            for r in data[cid]:
                if r["id"] == rid:
                    for k, v in ch.items():
                        if v in ("", None, []):
                            r.pop(k, None)
                        else:
                            r[k] = v
                    r["updated"], r["by"] = _now(), by
                    self._save(data)
                    return r
            raise KeyError(f"no record {rid!r} in {self.m['id']} {cid}")

    def delete(self, cid: str, rid: str) -> dict:
        with self.lock:
            data = self._load()
            for i, r in enumerate(data[cid]):
                if r["id"] == rid:
                    del data[cid][i]
                    # the records that linked to it no longer do (a university deleted leaves its programmes, unlinked)
                    for c in self.m["collections"]:
                        for p in c["properties"]:
                            if p["type"] != "relation" or p["to"] != cid:
                                continue
                            for x in data[c["id"]]:
                                v = x.get(p["key"])
                                if v == rid:
                                    x.pop(p["key"])
                                elif isinstance(v, list) and rid in v:
                                    x[p["key"]] = [y for y in v if y != rid]
                                    if not x[p["key"]]:
                                        x.pop(p["key"])
                    self._save(data)
                    return r
            raise KeyError(f"no record {rid!r} in {self.m['id']} {cid}")


def value(p: dict, v: Any) -> Any:
    """`v` as property `p` keeps it; ValueError says what is wrong. "" or None clears it."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return ""
    kind = p["type"]
    if kind == "number":
        if isinstance(v, bool):
            raise ValueError(t("{field}: a number", field=p["key"]))
        try:
            n = float(str(v).replace(",", "").strip())
        except ValueError:
            raise ValueError(t("{field}: a number, not “{value}”", field=p["key"], value=v))
        return int(n) if n.is_integer() else n
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ValueError(t("{field}: a text value", field=p["key"]))
    s = str(v).strip()
    if kind == "date":
        try:
            return dt.date.fromisoformat(s[:10]).isoformat()
        except ValueError:
            raise ValueError(t("{field}: a date as YYYY-MM-DD, not “{value}”", field=p["key"], value=s))
    if kind == "url":
        if not re.match(r"^https?://\S+$", s):
            raise ValueError(t("{field}: an http(s):// address, not “{value}”", field=p["key"], value=s))
        return s[:2000]
    if kind == "select":
        vals = [o["value"] for o in p["options"]]
        if s in vals or p.get("free"):
            return s[:80]
        # an option's label in any language counts too ("国考" for national)
        for o in p["options"]:
            if s.casefold() in {x.casefold() for x in o["label"].values()}:
                return o["value"]
        raise ValueError(t("{field}: one of {values}, not “{value}”", field=p["key"], values=", ".join(vals), value=s))
    if kind in ("title", "text"):
        return " ".join(s.split())[:300]
    return s[:20000]   # longtext
