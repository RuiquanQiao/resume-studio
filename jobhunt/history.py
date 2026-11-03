"""What the AI changed in the tracker, each change undoable.

    resume-studio-data/hunt/history.json     {"next": 4, "history": [{id: "H3", at, chat, cmd, kind, target, name,
                                              before, after, undone}, ...]}   newest last, the latest 1000
    resume-studio-data/hunt/history/          the logos a change replaced, by their sha1

A change keeps only what it changed: `before` and `after` hold those fields of the company or application (both
whole when it was made: `before` is null). A company's logo picture counts as the field "logo_file" (its sha1).
Undoing puts back a field only while it still holds what the change left there: a field changed since (by the
user or a later chat) stays as it is, and the undo says so. An AI once wrote one company's research over
another's (2026-11-03), and the only way back was the agent's own log: this is the way back.
"""
from __future__ import annotations

import copy
import hashlib

from core.i18n import t

from .store import Hunt, _now

KEEP = 1000
VOLATILE = {"updated"}   # changes with every write, never a change of its own


class History:
    def __init__(self, hunt: Hunt):
        self.hunt = hunt
        self.dir = hunt.dir / "history"

    # ---- snapshots -------------------------------------------------------------------
    def _logo(self, c: dict) -> str:
        """The sha1 of a company's logo picture ("" for none), a copy kept so an undo can put it back."""
        p = self.hunt.dir / "logos" / c["logo"] if c.get("logo") else None
        if not p or not p.is_file():
            return ""
        data = p.read_bytes()
        sha = hashlib.sha1(data).hexdigest()
        keep = self.dir / f"{sha}{p.suffix}"
        if not keep.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(data)
        return sha

    def snapshot(self, kind: str, rid: str) -> dict | None:
        """The record as it is now (None when there is none)."""
        if kind == "company":
            c = next((x for x in self.hunt.companies() if x["id"] == rid), None)
            return {**copy.deepcopy(c), "logo_file": self._logo(c)} if c else None
        a = next((x for x in self.hunt.applications() if x["id"] == rid), None)
        return copy.deepcopy(a) if a else None

    # ---- the log ---------------------------------------------------------------------
    def _data(self) -> dict:
        return self.hunt._read("history.json", "history")

    def record(self, kind: str, rid: str, before: dict | None, after: dict | None, *, cmd: str, chat: str = "") -> dict | None:
        """Keep a change, if it changed anything. Returns the entry."""
        if after is None or before == after:
            return None
        if before is None:
            b, a = None, {k: v for k, v in after.items() if k not in VOLATILE}
        else:
            keys = [k for k in {**before, **after} if k not in VOLATILE and before.get(k) != after.get(k)]
            if not keys:
                return None
            b, a = {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}
        with self.hunt.lock:
            data = self._data()
            e = {"id": f"H{data['next']}", "at": _now(), "chat": str(chat or "")[:40], "cmd": cmd, "kind": kind,
                 "target": rid, "name": self._name(kind, after), "before": b, "after": a, "undone": ""}
            data["next"] += 1
            data["history"] = (data["history"] + [e])[-KEEP:]
            self.hunt._write("history.json", data)
        self._prune(data["history"])
        return e

    def _name(self, kind: str, rec: dict) -> str:
        if kind == "company":
            return rec.get("name") or rec["id"]
        c = next((x for x in self.hunt.companies() if x["id"] == rec.get("company")), None)
        return f"{c['name'] if c else rec.get('company', '')} / {rec.get('role', '')}"

    def _prune(self, entries: list[dict]) -> None:
        """Drop the kept logos no entry needs any more."""
        if not self.dir.is_dir():
            return
        used = {str(side.get("logo_file") or "") for e in entries for side in (e.get("before") or {}, e.get("after") or {})}
        for f in self.dir.iterdir():
            if f.stem not in used:
                f.unlink(missing_ok=True)

    def entries(self, target: str = "", chat: str = "") -> list[dict]:
        """Newest first."""
        out = [e for e in self._data()["history"] if (not target or e["target"] == target) and (not chat or e["chat"] == chat)]
        return out[::-1]

    def entry(self, hid: str) -> dict:
        e = next((x for x in self._data()["history"] if x["id"] == str(hid).strip().upper()), None)
        if not e:
            raise KeyError(t("No change “{id}” in the history", id=hid))
        return e

    # ---- undo ------------------------------------------------------------------------
    def undo(self, hid: str, *, by: str = "window") -> dict:
        """Put back what change `hid` replaced. Returns {"entry", "restored": [fields], "kept": [fields changed since]}."""
        e = self.entry(hid)
        if e["undone"]:
            raise ValueError(t("That change was undone already ({at})", at=e["undone"][:16].replace("T", " ")))
        kind, rid = e["kind"], e["target"]
        with self.hunt.lock:
            cur = self.snapshot(kind, rid)
            if e["before"] is None:   # it was made: it goes, unless it changed since
                if cur is None:
                    restored, kept = [], []
                else:
                    changed = [k for k, v in e["after"].items() if cur.get(k) != v]
                    if changed or (kind == "company" and self.hunt.applications(rid)):
                        raise ValueError(t("It changed since it was made, so it stays: remove it yourself if it should go"))
                    (self.hunt.delete_company if kind == "company" else self.hunt.delete_application)(rid)
                    restored, kept = ["*"], []
            else:
                if cur is None:
                    raise ValueError(t("It was removed since, so there is nothing to put back"))
                restored = [k for k, v in e["after"].items() if cur.get(k) == v]
                kept = [k for k in e["after"] if k not in restored]
                self._put(kind, rid, {k: e["before"][k] for k in restored}, cur)
            data = self._data()
            for x in data["history"]:
                if x["id"] == e["id"]:
                    x["undone"] = _now()
                    x["undone_by"] = by
                    e = x
            self.hunt._write("history.json", data)
        self.hunt._changed()
        return {"entry": e, "restored": restored, "kept": kept}

    def _put(self, kind: str, rid: str, fields: dict, cur: dict) -> None:
        if not fields:
            return
        fields = dict(fields)
        logo = fields.pop("logo_file", None)
        name = "companies.json" if kind == "company" else "applications.json"
        data = self.hunt._read(name, "companies" if kind == "company" else "applications")
        rec = next(x for x in data["companies" if kind == "company" else "applications"] if x["id"] == rid)
        for k, v in fields.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = copy.deepcopy(v)
        if logo is not None:
            folder = self.hunt.dir / "logos"
            for old in folder.glob(rid + ".*"):
                old.unlink(missing_ok=True)
            kept = next(iter(self.dir.glob(logo + ".*")), None) if logo else None
            if kept:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{rid}{kept.suffix}").write_bytes(kept.read_bytes())
                rec["logo"] = f"{rid}{kept.suffix}"
            else:
                rec["logo"] = ""
        rec["updated"] = _now()
        self.hunt._write(name, data)
        if kind == "application" and "status" in fields and fields["status"] != cur.get("status"):
            self.hunt._log(rid, "status", frm=str(cur.get("status") or ""), to=str(fields["status"] or ""), source="undo")


def describe(e: dict) -> str:
    """One line for the chat's command: what the change did."""
    what = "made" if e["before"] is None else "changed " + ", ".join(k for k in e["after"] if k not in ("kept",))
    return (f"{e['id']}  {e['at'][:16].replace('T', ' ')}  {e['cmd']} {e['name']} ({e['target']}): {what}"
            + (f"  [undone {e['undone'][:16].replace('T', ' ')}]" if e["undone"] else ""))
