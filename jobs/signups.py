"""What Resume Studio adds on top of the tracker: sign-ups and which resume went out.

A **sign-up** is one account on one company's careers site (China's 网申): the user fills a profile
there once and applies to several roles from it, in an order of preference (志愿). Each role becomes a
row of the tool's tracker when the sign-up is sent; until then it lives only here.

A **send** is the moment a resume went out: the subset and the version of it (resume/versions.py)
are pinned on every application it reached, for a sign-up's roles and for an ordinary posting alike.
This is kept apart from the tool (one JSON file next to the data file), so it survives switching tools.

    resume-studio-data/applications.json
    {"signups": [{id, company, site, subset, note, created,
                  roles: [{key, role, url, app}],       # app: the tracker row's id once sent
                  sent: [{at, subset, n, roles: [key]}]}],
     "sent": {"<tool>:<app id>": [{at, subset, n, signup}]}}     # oldest first
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.i18n import t


def _text(v: Any, limit: int = 200) -> str:
    return " ".join(str(v or "").split())[:limit]


class Signups:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "applications.json"
        self.lock = threading.RLock()

    # ---- the file --------------------------------------------------------------
    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("signups", [])
        data.setdefault("sent", {})
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        os.replace(tmp, self.path)

    # ---- sign-ups ----------------------------------------------------------------
    def all(self) -> list[dict]:
        return self._read()["signups"]

    def get(self, sid: str) -> dict:
        s = next((x for x in self.all() if x.get("id") == sid), None)
        if s is None:
            raise KeyError(t("No such sign-up"))
        return s

    @staticmethod
    def _roles(given: Any, old: list[dict]) -> list[dict]:
        """The roles in the order given; a role keeps its key (and tracker row) by key, else by name."""
        by_key = {r["key"]: r for r in old}
        by_name = {r["role"]: r for r in old}
        used = {r["key"] for r in old}
        out, seen = [], set()
        for x in given or []:
            x = x if isinstance(x, dict) else {"role": x}
            name = _text(x.get("role"), 120)
            if not name or name in seen:
                continue
            seen.add(name)
            prev = by_key.get(str(x.get("key") or "")) or by_name.get(name)
            if prev is None:
                n = 1
                while f"r{n}" in used:
                    n += 1
                used.add(f"r{n}")
                prev = {"key": f"r{n}", "app": None}
            elif prev.get("app") and prev["role"] != name:
                name = prev["role"]   # a role already in the tracker keeps its name there
            out.append({"key": prev["key"], "role": name, "url": _text(x.get("url", prev.get("url")), 500), "app": prev.get("app")})
        return out

    def create(self, fields: dict) -> dict:
        with self.lock:
            data = self._read()
            nums = [int(m.group(1)) for s in data["signups"] if (m := re.fullmatch(r"S(\d+)", str(s.get("id"))))]
            s = {"id": f"S{(max(nums) + 1) if nums else 1}", "company": _text(fields.get("company"), 80),
                 "site": _text(fields.get("site"), 500), "subset": _text(fields.get("subset"), 80) or None,
                 "note": _text(fields.get("note"), 300), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "roles": self._roles(fields.get("roles"), []), "sent": []}
            if not s["company"]:
                raise ValueError(t("Name the company first"))
            data["signups"].insert(0, s)
            self._write(data)
            return s

    def update(self, sid: str, fields: dict) -> dict:
        with self.lock:
            data = self._read()
            s = next((x for x in data["signups"] if x.get("id") == sid), None)
            if s is None:
                raise KeyError(t("No such sign-up"))
            for k, limit in (("company", 80), ("site", 500), ("note", 300)):
                if k in fields:
                    s[k] = _text(fields[k], limit)
            if not s["company"]:
                raise ValueError(t("Name the company first"))
            if "subset" in fields:
                s["subset"] = _text(fields["subset"], 80) or None
            if "roles" in fields:
                s["roles"] = self._roles(fields["roles"], s["roles"])
            self._write(data)
            return s

    def delete(self, sid: str) -> None:
        """Only Resume Studio's record goes: the tracker's rows (and what was sent) stay."""
        with self.lock:
            data = self._read()
            data["signups"] = [x for x in data["signups"] if x.get("id") != sid]
            self._write(data)

    def mark_sent(self, sid: str, subset: str, n: int, apps: dict[str, str], tool: str) -> dict:
        """The sign-up went out with version n of `subset`; `apps` maps role keys to their tracker rows."""
        with self.lock:
            data = self._read()
            s = next((x for x in data["signups"] if x.get("id") == sid), None)
            if s is None:
                raise KeyError(t("No such sign-up"))
            at = time.strftime("%Y-%m-%dT%H:%M:%S")
            for r in s["roles"]:
                if r["key"] in apps:
                    r["app"] = apps[r["key"]]
            s["subset"] = subset
            s["sent"].append({"at": at, "subset": subset, "n": n, "roles": [r["key"] for r in s["roles"] if r.get("app")]})
            for r in s["roles"]:
                if r.get("app"):
                    data["sent"].setdefault(f"{tool}:{r['app']}", []).append({"at": at, "subset": subset, "n": n, "signup": sid})
            self._write(data)
            return s

    # ---- what went out, per application -------------------------------------------------
    def sent_to(self, tool: str, app_id: str) -> list[dict]:
        return self._read()["sent"].get(f"{tool}:{app_id}", [])

    def mark_app_sent(self, tool: str, app_id: str, subset: str, n: int) -> dict:
        with self.lock:
            data = self._read()
            v = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "subset": subset, "n": n, "signup": None}
            data["sent"].setdefault(f"{tool}:{app_id}", []).append(v)
            self._write(data)
            return v

    def signup_of(self, app_id: str) -> str | None:
        for s in self.all():
            if any(r.get("app") == str(app_id) for r in s["roles"]):
                return s["id"]
        return None

    def rename_subset(self, old: str, new: str) -> None:
        """A renamed subset keeps what was sent with it."""
        with self.lock:
            data = self._read()
            hit = False
            for s in data["signups"]:
                if s.get("subset") == old:
                    s["subset"], hit = new, True
                for x in s["sent"]:
                    if x.get("subset") == old:
                        x["subset"], hit = new, True
            for xs in data["sent"].values():
                for x in xs:
                    if x.get("subset") == old:
                        x["subset"], hit = new, True
            if hit:
                self._write(data)
