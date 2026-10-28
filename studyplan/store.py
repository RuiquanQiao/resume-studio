"""The plan (docs/PLAN.md): one Markdown document Claude helps the user write, with no fixed shape, and
its versions. It lives in the data folder:

    resume-studio-data/plan/plan.md         the plan as it is now (plain Markdown, opens in any editor)
    resume-studio-data/plan/versions.json   [{n, at, why, by, text, edited?}]: every time the plan changed course

A rewrite (Claude's, with why; the user's own edit in the window) keeps a version. A small change
(a box ticked, a line of progress) goes into the latest version instead, so the list shows only the
times the plan changed course. Nothing is ever deleted: going back to a version keeps it as a new one.
Standard library only: scripts/plan.py uses it without the window."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from core.i18n import t

MAX_TEXT = 200_000
BY = ("you", "claude")   # and "outside": a version of what another editor left in the file
TASK = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+\[)([ xX])(\])(?=\s|$)")   # as web/app.js md() reads them
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^```")
# the section a line of progress goes under when Claude names none (the plan's own words for it)
PROGRESS_WORDS = ("进展", "进度", "记录", "日志", "打卡", "追踪", "progress", "log", "journal", "tracking",
                  "進捗", "記録", "진행", "기록", "fortschritt", "progreso", "progrès")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _clean(text: Any) -> str:
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(s) > MAX_TEXT:
        raise ValueError(t("The plan is too long ({n} characters at most)", n=MAX_TEXT))
    return s.rstrip() + "\n" if s.strip() else ""


def _lines_outside_code(lines: list[str]) -> list[int]:
    """Indexes of the lines that are not inside a fenced code block."""
    out, inside = [], False
    for i, line in enumerate(lines):
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            out.append(i)
    return out


def tasks(text: str) -> list[int]:
    """The line of each checkbox (- [ ] / - [x]), in order, the way the page counts them."""
    lines = text.split("\n")
    return [i for i in _lines_outside_code(lines) if TASK.match(lines[i])]


def set_task(text: str, index: int, done: bool) -> str:
    lines = text.split("\n")
    at = tasks(text)
    if not 0 <= index < len(at):
        raise KeyError(t("No checkbox {n} in the plan", n=index + 1))
    i = at[index]
    lines[i] = TASK.sub(lambda m: m.group(1) + ("x" if done else " ") + m.group(3), lines[i], count=1)
    return "\n".join(lines)


def add_line(text: str, line: str, under: str = "", on: str = "") -> str:
    """A line of progress at the end of a section: the one named `under`, else the last section whose
    heading sounds like progress, else a new one at the end."""
    line = " ".join(str(line or "").split())
    if not line:
        raise ValueError(t("Write what happened"))
    day = on or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError(t("Dates are written YYYY-MM-DD, not “{value}”", value=day))
    item = f"- {day} {line}"
    lines = text.rstrip("\n").split("\n") if text.strip() else []
    heads = [(i, len(m.group(1)), m.group(2)) for i in _lines_outside_code(lines) if (m := HEADING.match(lines[i]))]
    want = under.strip().casefold()
    pick = None
    for h in heads:
        name = h[2].casefold()
        if (want and want in name) or (not want and any(w in name for w in PROGRESS_WORDS)):
            pick = h   # the last one wins: progress usually sits at the end
    if pick is None:
        title = under.strip() or t("Progress log")
        return "\n".join([*lines, *([""] if lines else []), f"## {title}", "", item]) + "\n"
    at, level = pick[0], pick[1]
    end = next((i for i, lv, _ in heads if i > at and lv <= level), len(lines))
    while end > at + 1 and not lines[end - 1].strip():   # after the section's last line, not its blank tail
        end -= 1
    if end == at + 1:
        lines[end:end] = ["", item]
    else:
        lines[end:end] = [item]
    return "\n".join(lines) + "\n"


class Plan:
    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir) / "plan"
        self.md = self.dir / "plan.md"
        self.vpath = self.dir / "versions.json"
        self.lock = threading.RLock()

    # ---- reading ----
    def text(self) -> str:
        try:
            return self.md.read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            return ""

    def versions(self) -> list[dict]:
        try:
            data = json.loads(self.vpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [v for v in data if isinstance(v, dict) and isinstance(v.get("n"), int) and isinstance(v.get("text"), str)] \
            if isinstance(data, list) else []

    def get(self, n: int) -> dict:
        for v in self.versions():
            if v["n"] == n:
                return v
        raise KeyError(t("The plan has no version {n}", n=n))

    def updated(self) -> str:
        try:
            return datetime.fromtimestamp(self.md.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            return ""

    def state(self) -> dict:
        text, vs = self.text(), self.versions()
        last = vs[-1] if vs else None
        return {"text": text, "digest": digest(text), "updated": self.updated(),
                "kept": bool(last) and last["text"] == text,
                "versions": [{k: v.get(k) for k in ("n", "at", "why", "by", "edited")} for v in vs]}

    def signature(self) -> tuple:
        out = []
        for p in (self.md, self.vpath):
            try:
                st = p.stat()
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    # ---- writing ----
    @staticmethod
    def _put(path: Path, text: str) -> None:
        """Write through a temporary file. On Windows a reader that has the file open for a moment (the
        window reading it, another editor, a virus scanner) makes the swap fail, so it is retried briefly."""
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for wait in (0.02, 0.05, 0.1, 0.2, 0.4, 0.8):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(wait)
        os.replace(tmp, path)

    def _save(self, text: str, versions: list[dict] | None = None) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if versions is not None:
            self._put(self.vpath, json.dumps(versions, ensure_ascii=False, indent=1))
        self._put(self.md, text)

    def _check_base(self, base: str | None) -> None:
        if base is not None and base != digest(self.text()):
            raise ConflictError(t("The plan changed while you were editing it"))

    def _append(self, vs: list[dict], text: str, why: str, by: str, **extra: Any) -> dict:
        """A new version. The plan as it is in the file goes first when it was changed outside the
        window since the latest version (in another editor): nothing anyone wrote is ever lost."""
        current = self.text()
        if current.strip() and (not vs or vs[-1]["text"] != current) and current != text:
            vs.append({"n": (vs[-1]["n"] + 1) if vs else 1, "at": now(), "why": t("Edited outside the window"),
                       "by": "outside", "text": current})
        made = {"n": (vs[-1]["n"] + 1) if vs else 1, "at": now(), "why": why, "by": by, "text": text, **extra}
        vs.append(made)
        self._save(text, vs)
        return {k: made[k] for k in ("n", "at", "why", "by")}

    def write(self, text: Any, why: str = "", by: str = "you", base: str | None = None) -> dict:
        """A rewrite: the plan changed course, kept as a version (unless nothing changed)."""
        text, why = _clean(text), " ".join(str(why or "").split())[:300]
        by = by if by in BY else "you"
        with self.lock:
            self._check_base(base)
            vs = self.versions()
            made = None
            if not vs or vs[-1]["text"] != text:
                made = self._append(vs, text, why, by)
            elif text != self.text():
                self._save(text)
            return {"made": made, **self.state()}

    def touch(self, text: Any, by: str = "you", base: str | None = None) -> dict:
        """A small change (a box ticked, a line of progress): it goes into the latest version, which
        stays what the plan says now. When the plan was changed elsewhere since that version (an edit in
        another editor), the latest version is left as it was and only the file changes."""
        text = _clean(text)
        with self.lock:
            self._check_base(base)
            before, vs = self.text(), self.versions()
            if not vs:
                return self.write(text, by=by)
            if vs[-1]["text"] == before:
                vs[-1] = {**vs[-1], "text": text, "edited": now()}
                self._save(text, vs)
            else:
                self._save(text)
            return {"made": None, **self.state()}

    def tick(self, index: int, done: bool, by: str = "you", base: str | None = None) -> dict:
        with self.lock:
            self._check_base(base)
            return self.touch(set_task(self.text(), index, done), by=by)

    def tick_item(self, words: str, done: bool = True, by: str = "claude") -> dict:
        """The checkbox whose text holds `words` (one only), ticked or unticked."""
        want = " ".join(str(words or "").split()).casefold()
        if not want:
            raise ValueError(t("Say which item"))
        with self.lock:
            text = self.text()
            lines = text.split("\n")
            hits = [k for k, i in enumerate(tasks(text)) if want in " ".join(TASK.sub("", lines[i]).split()).casefold()]
            if not hits:
                raise KeyError(t("No checkbox in the plan says “{words}”", words=words))
            if len(hits) > 1:
                raise ValueError(t("{n} checkboxes say “{words}”; use more of their words", n=len(hits), words=words))
            return self.touch(set_task(text, hits[0], done), by=by)

    def note(self, line: str, under: str = "", on: str = "", by: str = "claude") -> dict:
        with self.lock:
            return self.touch(add_line(self.text(), line, under, on), by=by)

    def restore(self, n: int, by: str = "you") -> dict:
        """Back to version n: kept as a new version, nothing deleted."""
        with self.lock:
            v = self.get(n)
            if v["text"] == self.text():
                return {"made": None, **self.state()}
            made = self._append(self.versions(), v["text"], t("Went back to v{n}", n=n), by, **{"from": n})
            return {"made": made, **self.state()}


class ConflictError(Exception):
    pass
