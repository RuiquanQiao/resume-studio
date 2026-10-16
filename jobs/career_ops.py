"""career-ops (https://github.com/santifer/career-ops, MIT) as Resume Studio's job-posting tool.

career-ops is a set of prompt files and Node scripts that Claude Code runs in its own folder. This
adapter never copies its logic. It:
  - installs a checkout inside the app's folder (git clone + its own npm dependencies, with npm's
    cache and Playwright's browsers kept in the app's folder too) or uses one the user has;
  - updates it with career-ops' own updater (which never touches the user's data) and checks
    afterwards that what this adapter reads is still where it expects (`checks`);
  - reads the files career-ops documents as canonical: the tracker table, the reports and
    their `## Machine Summary` YAML, the states list, the tracker's column aliases;
  - writes only through career-ops' own scripts (set-status.mjs), plus `cv.md` /
    `article-digest.md`, which career-ops reads as the CV and which are generated from resume.yaml;
  - runs everything else as Claude Code chats in the career-ops folder, asked in plain language,
    so its modes keep working however they are implemented.

Everything read here is parsed tolerantly: a missing column, key or section just goes missing in the
UI, and the raw report is always available.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from core.i18n import Prefs, t
from core.store import to_plain

from .base import JobTool, Progress, ToolError
from .cv_markdown import cv_markdown, proof_points

REPO_URL = "https://github.com/santifer/career-ops.git"
NODE_MIN = (22, 13)
CV_FILES = ("cv.md", "article-digest.md")
# work career-ops does in every evaluation (its own scripts); edits stay under the permission mode
TOOLS = ["Bash(node *)", "Bash(npm run *)", "PowerShell(node *)", "PowerShell(npm run *)"]

_yaml = YAML(typ="safe", pure=True)
_FENCE = re.compile(r"^\s*(```|~~~)")
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_FIELD = re.compile(r"^\*\*([^*:]+):\*\*\s*(.*)$")
_SCORE = re.compile(r"(\d+(?:[.,]\d+)?)\s*/\s*(\d+)")
_NUM = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sha(text: str) -> str:
    return hashlib.sha1(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_yaml(text: str) -> Any:
    try:
        return _yaml.load(text)
    except Exception:  # noqa: BLE001 - someone else's file: unreadable is just "nothing"
        return None


def _href(cell: str) -> str:
    m = _LINK.search(cell or "")
    return m.group(2) if m else (cell or "").strip()


def _plain(cell: str) -> str:
    """A table cell or header value without markdown links or bold."""
    s = _LINK.sub(lambda m: m.group(1), cell or "")
    return s.replace("**", "").strip()


def _score(value: Any) -> tuple[float | None, float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), 5.0
    s = str(value or "").strip()
    m = _SCORE.search(s)
    if m:
        return float(m.group(1).replace(",", ".")), float(m.group(2))
    if _NUM.match(s):
        return float(s.replace(",", ".")), 5.0
    return None, 5.0


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def find_table(text: str, aliases: dict[str, str], need: tuple[str, ...] = ("company", "role")
               ) -> tuple[list[str], list[dict]] | None:
    """(canonical header, rows) of the first table whose header names every column in `need`
    (by default the tracker: a company and a role)."""
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        if "|" not in line or not _SEP.match(lines[i + 1]):
            continue
        head = [aliases.get(_plain(c).lower(), _plain(c).lower()) for c in _cells(line)]
        if any(n not in head for n in need):
            continue
        rows = []
        for raw in lines[i + 2:]:
            if "|" not in raw or not raw.strip():
                break
            rows.append(dict(zip(head, _cells(raw))))
        return head, rows
    return None


def parse_tracker(text: str, aliases: dict[str, str]) -> list[dict]:
    """Rows of the tracker table keyed by canonical column (tracker-aliases.json names them)."""
    found = find_table(text, aliases)
    return found[1] if found else []


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(text before the first `## `, [(heading, body)]); headings inside code fences don't count."""
    head: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        (sections[-1][1] if sections else head).append(line)
    return "\n".join(head), [(h, "\n".join(b).strip()) for h, b in sections]


def _fenced_yaml(body: str) -> dict:
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)\n\s*```", body, re.S)
    data = _load_yaml(m.group(1)) if m else None
    return data if isinstance(data, dict) else {}


def _strs(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x or "").strip()]
    return [str(value).strip()] if str(value or "").strip() else []


def parse_report(text: str) -> dict:
    """What the window shows from one evaluation report (see jobs/base.py for the shape)."""
    head, sections = split_sections(text)
    title = next((l[2:].strip() for l in head.splitlines() if l.startswith("# ")), "")
    fields = {}
    for line in head.splitlines():
        m = _FIELD.match(line.strip())
        if m:
            fields[m.group(1).strip().lower()] = m.group(2).strip()
    ms: dict = {}
    verdict, posting, rest = "", "", []
    for h, body in sections:
        low = h.lower()
        if low.startswith("machine summary"):
            ms = _fenced_yaml(body)
        elif "(lead)" in low:
            verdict = body
        elif low.startswith("job description"):
            posting = body
        else:
            rest.append({"title": h, "markdown": body})
    score, score_max = _score(ms.get("score") if ms.get("score") is not None else fields.get("score"))
    reqs = []
    for r in ms.get("requirement_importance") or []:
        if isinstance(r, dict) and r.get("requirement"):
            reqs.append({"requirement": str(r.get("requirement")), "importance": str(r.get("importance") or ""),
                         "match": str(r.get("match") or ""), "evidence": str(r.get("evidence") or ""),
                         "signal": "" if r.get("jd_signal") in (None, "null") else str(r.get("jd_signal"))})
    summary = {
        "verdict": verdict.strip(),
        "decision": str(ms.get("final_decision") or ""),
        "legitimacy": str(ms.get("legitimacy_tier") or _plain(fields.get("legitimacy", ""))),
        "archetype": str(ms.get("archetype") or _plain(fields.get("archetype", ""))),
        "risk": str(ms.get("risk_level") or ""),
        "confidence": str(ms.get("confidence") or ""),
        "strengths": _strs(ms.get("top_strengths")),
        "hard_stops": _strs(ms.get("hard_stops")),
        "soft_gaps": _strs(ms.get("soft_gaps")),
        "next_action": str(ms.get("next_action") or ""),
        "comp": str(ms.get("advertised_comp") or ""),
        "work_auth": str(ms.get("work_auth") or _plain(fields.get("work auth", ""))),
        "location": str(ms.get("posting_location") or ""),
        "requirements": reqs,
    }
    return {"title": title, "fields": fields, "summary": {k: v for k, v in summary.items() if v},
            "company": str(ms.get("company") or ""), "role": str(ms.get("role") or ""),
            "score": score, "score_max": score_max, "url": _href(fields.get("url", "")),
            "date": _plain(fields.get("date", "")), "sections": rest, "posting": posting.strip(),
            "structured": bool(ms)}


def node_version() -> tuple[int, ...] | None:
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10,
                             creationflags=_NO_WINDOW).stdout.strip()
        return tuple(int(x) for x in out.lstrip("v").split(".")[:3])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class CareerOps(JobTool):
    id = "career-ops"
    name = "career-ops"
    homepage = "https://github.com/santifer/career-ops"
    license = "MIT"
    author = "Santiago Fernández de Valderrama (santifer)"

    def __init__(self, prefs: Prefs, home: Path, cache: Path, skill_md: Path):
        """home: where an install goes (inside the app's own folder); cache: npm's cache."""
        self.prefs = prefs
        self.home = Path(home)
        self.cache = Path(cache)
        self.skill_md = skill_md
        self.busy: str | None = None          # "install" | "update" while one runs
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._doctor: tuple[Any, dict | None] = (None, None)
        self._sync = Path(prefs.path).parent / "career-ops-sync.json"
        self.last_sync: dict = {}

    # ---- settings (this computer's, next to the app's other preferences) ----
    def settings(self) -> dict:
        s = self.prefs.load().get("career_ops")
        s = s if isinstance(s, dict) else {}
        return {"path": s.get("path") or None, "cv_lang": s.get("cv_lang") or None,
                "notes": s.get("notes", True) is not False, "sync": s.get("sync", True) is not False}

    def save_settings(self, **fields: Any) -> dict:
        cur = {k: v for k, v in self.settings().items() if v is not None}
        for k, v in fields.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        self.prefs.save(career_ops=cur)
        return self.settings()

    @property
    def root(self) -> Path:
        p = self.settings()["path"]
        return Path(p) if p else self.home

    def installed(self) -> bool:
        return (self.root / "AGENTS.md").is_file() and (self.root / "modes").is_dir()

    def data_folder(self) -> Path:
        return self.data_root()

    def data_root(self) -> Path:
        """Where career-ops keeps the user's files: path-resolver.mjs's order, in Python."""
        root = self.root
        env = (os.environ.get("CAREER_OPS_ROOT") or os.environ.get("CAREER_OPS_DATA_DIR") or "").strip()
        if env:
            return (root / env).resolve()
        marker = root / ".career-ops-data"
        if marker.is_file():
            content = _read(marker).strip()
            if content:
                return (root / content).resolve()
        return root

    def tracker_path(self) -> Path:
        env = (os.environ.get("CAREER_OPS_TRACKER") or "").strip()
        if env:
            return (self.root / env).resolve()
        data = self.data_root()
        return data / "data" / "applications.md" if (data / "data" / "applications.md").exists() else data / "applications.md"

    def version(self) -> str:
        try:
            return _read(self.root / "VERSION").split()[0]
        except (OSError, IndexError):
            return ""

    # ---- running its scripts ------------------------------------------------
    def env(self) -> dict[str, str]:
        """For every process started in the career-ops folder (scripts and chats): npm's cache and
        Playwright's browsers stay inside the app's folder instead of the user profile."""
        return {"npm_config_cache": str(self.cache / "npm"),
                "PLAYWRIGHT_BROWSERS_PATH": str(self.home.parent / "ms-playwright"),
                "NO_COLOR": "1", "FORCE_COLOR": "0"}

    def _run(self, cmd: list[str], timeout: float = 120, cwd: Path | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, cwd=str(cwd or self.root), env={**os.environ, **self.env()},
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, creationflags=_NO_WINDOW)
        except FileNotFoundError:
            raise ToolError(t("{program} was not found on this computer.", program=cmd[0]))
        except subprocess.TimeoutExpired:
            raise ToolError(t("{program} took too long and was stopped.", program=" ".join(cmd[:2])))

    def node(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        exe = shutil.which("node")
        if not exe:
            raise ToolError(t("Node.js was not found. career-ops needs Node.js 22 or newer (nodejs.org)."))
        return self._run([exe, *args], timeout=timeout)

    @staticmethod
    def _json_out(proc: subprocess.CompletedProcess) -> Any:
        for line in reversed((proc.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    continue
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    @staticmethod
    def _tail(proc: subprocess.CompletedProcess, n: int = 12) -> str:
        text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        return "\n".join(text.splitlines()[-n:])

    # ---- status ---------------------------------------------------------------
    def _setup_files(self) -> tuple:
        data = self.data_root()
        sig = []
        for rel in ("cv.md", "config/profile.yml", "modes/_profile.md", "portals.yml"):
            try:
                st = (data / rel).stat()
                sig.append((rel, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((rel, None))
        return tuple(sig)

    def doctor(self) -> dict | None:
        """career-ops' own read-only setup check (`doctor.mjs --json`), cached until its files change."""
        key = (str(self.root), self._setup_files())
        if self._doctor[0] == key:
            return self._doctor[1]
        try:
            res = self._json_out(self.node("doctor.mjs", "--json", timeout=60))
        except ToolError:
            res = None
        res = res if isinstance(res, dict) else None
        self._doctor = (key, res)
        return res

    def _skill_stubs(self) -> list[str]:
        """Skill entrypoints that are still git's text stand-ins for symlinks (Windows without symlinks)."""
        out = []
        for p in self.root.glob(".*/skills/career-ops/SKILL.md"):
            try:
                if p.stat().st_size < 300 and "SKILL.md" in _read(p) and "---" not in _read(p):
                    out.append(str(p.relative_to(self.root)).replace("\\", "/"))
            except OSError:
                continue
        return out

    def repair_skills(self) -> bool:
        """career-ops' own fix for symlink stubs (scaffolder/bin/skill-entrypoints.mjs)."""
        script = self.root / "scaffolder" / "bin" / "skill-entrypoints.mjs"
        if not self._skill_stubs() or not script.exists():
            return False
        code = ("const m = await import(new URL('file:///' + process.argv[1].replace(/\\\\/g, '/')).href);"
                "console.log(JSON.stringify(m.materializeSkillEntrypoints(process.argv[2])));")
        self.node("--input-type=module", "-e", code, str(script), str(self.root), timeout=60)
        return True

    def checks(self) -> list[dict]:
        """What this adapter relies on, checked against the installed version. After an update a
        failing check says exactly what changed; everything else keeps working."""
        root, out = self.root, []

        def add(cid: str, ok: bool, detail: str = "") -> None:
            out.append({"id": cid, "ok": bool(ok), "detail": detail})

        nv = node_version()
        add("node", nv is not None and nv[:2] >= NODE_MIN,
            t("Node.js {version}", version=".".join(map(str, nv))) if nv else t("not found"))
        add("deps", (root / "node_modules" / "js-yaml").is_dir(), "node_modules")
        tracker = self.tracker_path()
        if tracker.exists():
            found = find_table(_read(tracker), self.aliases())
            add("tracker", found is not None, t("{n} application", "{n} applications", n=len(found[1])) if found
                else t("no table with Company and Role columns"))
        else:
            add("tracker", True, t("no applications yet"))
        add("states", bool(self.states()), "templates/states.yml")
        oferta = root / "modes" / "oferta.md"
        add("report", oferta.exists() and "Machine Summary" in _read(oferta), "modes/oferta.md · ## Machine Summary")
        add("status", (root / "set-status.mjs").exists(), "set-status.mjs")
        add("updater", (root / "update-system.mjs").exists(), "update-system.mjs")
        stubs = self._skill_stubs()
        add("skill", not stubs, ", ".join(stubs) if stubs else ".claude/skills/career-ops")
        add("web", (root / "web" / "package.json").is_file() and (root / "web" / "server.mjs").is_file(), "web/server.mjs")
        add("modes", bool(self.modes()), "modes/README.md")
        return out

    def status(self) -> dict:
        base = {"tool": {"id": self.id, "name": self.name, "homepage": self.homepage, "license": self.license,
                         "author": self.author},
                "installed": self.installed(), "root": str(self.root), "home": str(self.home),
                "custom_root": self.settings()["path"] is not None, "busy": self.busy, "error": self.last_error,
                "settings": self.settings(), "git": bool(shutil.which("git")), "node": bool(shutil.which("node"))}
        if not base["installed"]:
            return base
        doc = self.doctor() or {}
        missing = [str(x) for x in doc.get("missing") or [] if str(x) not in CV_FILES or not self.settings()["sync"]]
        return {**base, "version": self.version(), "data_root": str(self.data_root()),
                "needs_setup": bool(missing) or bool(doc.get("unpersonalized")),
                "setup_missing": missing, "unpersonalized": [str((x or {}).get("path") or x) for x in doc.get("unpersonalized") or []],
                "doctor": bool(doc), "checks": self.checks(), "cv": self.cv_state()}

    # ---- install, update ---------------------------------------------------------
    def _deps(self) -> None:
        npm = shutil.which("npm")
        if not npm:
            raise ToolError(t("npm was not found. career-ops needs Node.js 22 or newer (nodejs.org)."))
        # --ignore-scripts: no Playwright browser download into the user profile; scripts that need a
        # browser say so, and its browsers would go to PLAYWRIGHT_BROWSERS_PATH (inside this folder)
        p = self._run([npm, "install", "--ignore-scripts", "--no-package-lock", "--no-audit", "--no-fund"], timeout=900)
        if p.returncode != 0:
            raise ToolError(t("Installing career-ops' dependencies failed:") + "\n" + self._tail(p))

    def _job(self, kind: str, progress: Progress, fn) -> Any:
        with self._lock:
            if self.busy:
                raise ToolError(t("career-ops is busy ({task}); wait for it to finish.", task=self.busy))
            self.busy = kind
        self.last_error = None
        try:
            return fn()
        except ToolError as e:
            self.last_error = str(e)
            raise
        finally:
            self.busy = None
            self._doctor = (None, None)

    def install(self, progress: Progress) -> None:
        def run() -> None:
            target = self.home
            if not (target / "AGENTS.md").exists():
                if target.exists() and any(target.iterdir()):
                    raise ToolError(t("{path} already exists and isn't career-ops.", path=target))
                git = shutil.which("git")
                if not git:
                    raise ToolError(t("git was not found. Install Git for Windows (git-scm.com) first."))
                progress("download", "running")
                target.parent.mkdir(parents=True, exist_ok=True)
                p = self._run([git, "clone", "--quiet", REPO_URL, str(target)], timeout=900, cwd=target.parent)
                if p.returncode != 0:
                    raise ToolError(t("Downloading career-ops failed:") + "\n" + self._tail(p))
            progress("download", "done")
            self.save_settings(path=None)
            progress("deps", "running")
            self._deps()
            progress("deps", "done")
            progress("repair", "running")
            self.repair_skills()
            progress("repair", "done")
        self._job("install", progress, run)

    def use_folder(self, path: Path) -> None:
        path = Path(path)
        if not ((path / "AGENTS.md").is_file() and (path / "modes").is_dir()):
            raise ToolError(t("{path} isn't a career-ops folder (it has no AGENTS.md and modes/).", path=path))
        self.save_settings(path=None if path.resolve() == self.home.resolve() else str(path))
        self._doctor = (None, None)

    def check_update(self) -> dict:
        if not self.installed():
            raise ToolError(t("career-ops isn't installed"))
        res = self._json_out(self.node("update-system.mjs", "check", "--force", timeout=90))
        if not isinstance(res, dict):
            return {"status": "error", "local": self.version()}
        return res

    def update(self, progress: Progress) -> dict:
        def run() -> dict:
            progress("update", "running")
            p = self.node("update-system.mjs", "apply", "--confirm", timeout=900)
            if p.returncode != 0:
                raise ToolError(t("career-ops' updater stopped:") + "\n" + self._tail(p))
            progress("update", "done")
            progress("deps", "running")
            self._deps()
            progress("deps", "done")
            progress("repair", "running")
            self.repair_skills()
            progress("repair", "done")
            return {"output": self._tail(p, 20)}
        return self._job("update", progress, run)

    def repair(self, progress: Progress) -> dict:
        """Dependencies and skill entrypoints again (e.g. after an update made inside a chat)."""
        def run() -> dict:
            progress("deps", "running")
            self._deps()
            progress("deps", "done")
            progress("repair", "running")
            self.repair_skills()
            progress("repair", "done")
            return {}
        return self._job("repair", progress, run)

    def rollback(self) -> dict:
        def run() -> dict:
            p = self.node("update-system.mjs", "rollback", timeout=300)
            if p.returncode != 0:
                raise ToolError(t("career-ops' rollback stopped:") + "\n" + self._tail(p))
            self._deps()
            self.repair_skills()
            return {"output": self._tail(p, 20)}
        return self._job("rollback", lambda *_: None, run)

    # ---- its own web interface (web/, a Next.js app shipped with career-ops) ------------
    def web_dir(self) -> Path:
        return self.root / "web"

    def has_web(self) -> bool:
        w = self.web_dir()
        return self.installed() and (w / "package.json").is_file() and (w / "server.mjs").is_file()

    def web_routes(self) -> dict[str, str]:
        return {"application": "/pipeline/{id}", "home": "/", "applications": "/pipeline?tab=ALL"}

    def _web_keys(self) -> tuple[str, str]:
        """(dependencies, build): change when its lockfile / its source changes (an update), so the
        next start installs or builds again, and only then."""
        w = self.web_dir()
        h = hashlib.sha1()
        for name in ("package-lock.json", "package.json"):
            try:
                h.update((w / name).read_bytes())
            except OSError:
                h.update(b"-")
        nv = node_version()
        h.update(str(nv[:1] if nv else "").encode())
        deps = h.hexdigest()
        b = hashlib.sha1(deps.encode())
        skip = {"node_modules", "out", "dist", ".turbo", "tests", ".claude", ".playwright-mcp"}
        for dirpath, dirnames, filenames in os.walk(w):
            dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith(".next"))
            for f in sorted(filenames):
                p = Path(dirpath) / f
                try:
                    st = p.stat()
                except OSError:
                    continue
                b.update(f"{p.relative_to(w)}|{st.st_mtime_ns}|{st.st_size}\n".encode("utf-8", "replace"))
        return deps, b.hexdigest()

    def _web_state(self) -> dict:
        try:
            data = json.loads(self._web_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def _web_file(self) -> Path:
        return Path(self.prefs.path).parent / "career-ops-web.json"

    def web(self, progress: Progress, *, rebuild: bool = False) -> Any:
        """Install and build career-ops' web app when its source changed, then say how to run it
        (its own launcher, server.mjs, which binds loopback only)."""
        from core.webhost import StartError, WebSpec

        if not self.has_web():
            raise StartError(t("This version of career-ops has no web interface (web/)."))
        npm, node = shutil.which("npm"), shutil.which("node")
        if not npm or not node:
            raise StartError(t("Node.js was not found. career-ops needs Node.js 22 or newer (nodejs.org)."))
        w = self.web_dir()
        deps, build = self._web_keys()
        saved = self._web_state().get(str(w)) or {}
        env = {**self.env(), "NEXT_TELEMETRY_DISABLED": "1"}
        if rebuild or saved.get("deps") != deps or not (w / "node_modules" / "next").is_dir():
            progress("web_deps", "running")
            # its lockfile, exactly; no install scripts (nothing lands outside this folder)
            p = self._run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], timeout=1200, cwd=w)
            if p.returncode != 0:
                raise StartError(t("Installing the web interface's dependencies failed:") + "\n" + self._tail(p))
            saved = {"deps": deps}
            self._save_web_state(w, saved)
        progress("web_deps", "done")
        if rebuild or saved.get("build") != build or not (w / ".next" / "BUILD_ID").is_file():
            progress("web_build", "running")
            p = subprocess.run([npm, "run", "build"], cwd=str(w), env={**os.environ, **env}, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1200, creationflags=_NO_WINDOW)
            if p.returncode != 0:
                raise StartError(t("Building the web interface failed:") + "\n" + self._tail(p, 25))
            saved = {"deps": deps, "build": self._web_keys()[1]}   # the build itself touches files under web/
            self._save_web_state(w, saved)
        progress("web_build", "done")
        return WebSpec(argv=[node, "server.mjs", "start", "-p", "{port}"], cwd=w,
                       env={**env, "CAREER_OPS_ROOT": str(self.data_root())}, ready_path="/", ready_timeout=120)

    def _save_web_state(self, w: Path, entry: dict) -> None:
        data = self._web_state()
        data[str(w)] = entry
        self._web_file.parent.mkdir(parents=True, exist_ok=True)
        self._web_file.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def modes(self) -> list[dict]:
        """career-ops' mode catalog (modes/README.md), read each time: new modes appear by themselves."""
        try:
            text = _read(self.root / "modes" / "README.md")
        except OSError:
            text = ""
        found = find_table(text, {}, need=("file", "mode"))
        out = []
        for row in found[1] if found else []:
            mode = _plain(row.get("mode", "")).strip("` ")
            file = _plain(row.get("file", "")).strip("` ")
            if not mode or mode == "auto" or not file.endswith(".md"):
                continue
            out.append({"id": mode, "file": "modes/" + file, "purpose": _plain(row.get("purpose", ""))})
        # modes that have a file but no catalog row yet: their heading says what they do
        known = {m["file"] for m in out} | {"modes/" + f for f in ("README.md", "auto-pipeline.md")}
        # (plus interview/: the plan / practice / debrief skills AGENTS.md routes to as interview/<name>)
        for p in sorted((self.root / "modes").glob("*.md")) + sorted((self.root / "modes" / "interview").glob("*.md")):
            rel = "modes/" + p.relative_to(self.root / "modes").as_posix()
            if rel in known or p.name.startswith("_") or p.name == "README.md":
                continue
            try:
                first = next((l for l in _read(p).splitlines() if l.startswith("# ")), "")
            except OSError:
                continue
            m = re.match(r"#\s*Mode:\s*`?([\w/-]+)`?\s*[—–-]+\s*(.+)", first)
            mid = rel[len("modes/"):-len(".md")]   # the routable name (interview/plan), whatever the heading says
            out.append({"id": mid, "file": rel, "purpose": m.group(2).strip() if m else first.lstrip("# ").strip()})
        return out

    # ---- data -----------------------------------------------------------------------
    def aliases(self) -> dict[str, str]:
        try:
            data = json.loads(_read(self.root / "tracker-aliases.json"))
            return {str(k).lower(): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {"#": "num", "company": "company", "role": "role", "score": "score", "status": "status",
                    "report": "report", "date": "date", "notes": "notes", "url": "url", "pdf": "pdf"}

    def states(self) -> list[dict]:
        try:
            data = _load_yaml(_read(self.root / "templates" / "states.yml")) or {}
        except OSError:
            return []
        out = []
        for s in data.get("states") or [] if isinstance(data, dict) else []:
            if isinstance(s, dict) and s.get("id") and s.get("label"):
                out.append({"id": str(s["id"]), "label": str(s["label"]),
                            "aliases": [str(a).lower() for a in s.get("aliases") or []],
                            "terminal": bool(s.get("terminal"))})
        return out

    def _state_of(self, text: str, states: list[dict]) -> str:
        key = _plain(text).lower()
        for s in states:
            if key in (s["id"].lower(), s["label"].lower(), *s["aliases"]):
                return s["id"]
        return ""

    def _report_rel(self, cell: str) -> str:
        href = _href(cell)
        if not href or not href.lower().endswith(".md"):
            return ""
        data = self.data_root()
        for base in (self.tracker_path().parent, data):
            p = (base / href).resolve()
            if p.exists():
                try:
                    return str(p.relative_to(data)).replace("\\", "/")
                except ValueError:
                    return str(p)
        return href.lstrip("./").replace("\\", "/")

    def applications(self) -> list[dict]:
        path = self.tracker_path()
        if not path.exists():
            return []
        states = self.states()
        out = []
        for i, row in enumerate(parse_tracker(_read(path), self.aliases())):
            num = _plain(row.get("num", ""))
            score, smax = _score(_plain(row.get("score", "")))
            status_text = _plain(row.get("status", ""))
            out.append({"id": num if num and num not in ("—", "-", "N/A") else f"row{i + 1}",
                        "company": _plain(row.get("company", "")), "role": _plain(row.get("role", "")),
                        "date": _plain(row.get("date", "")), "score": score, "score_max": smax,
                        "status": self._state_of(status_text, states), "status_label": status_text,
                        "url": _href(row.get("url", "") or row.get("applylink", "")),
                        "notes": _plain(row.get("notes", "")), "report": self._report_rel(row.get("report", "")),
                        "pdf": _plain(row.get("pdf", ""))})
        out.sort(key=lambda a: (a["date"], int(a["id"]) if a["id"].isdigit() else 0), reverse=True)
        return out

    def application(self, app_id: str) -> dict:
        app = next((a for a in self.applications() if a["id"] == str(app_id)), None)
        if app is None:
            raise KeyError(app_id)
        if not app["report"]:
            return {**app, "title": f"{app['company']} — {app['role']}", "summary": {}, "sections": [], "posting": "", "raw": ""}
        path = (self.data_root() / app["report"]).resolve()
        try:
            text = _read(path)
        except OSError:
            return {**app, "title": f"{app['company']} — {app['role']}", "summary": {}, "sections": [], "posting": "",
                    "raw": "", "report_missing": True}
        rep = parse_report(text)
        return {**app, "title": rep["title"] or f"{app['company']} — {app['role']}", "summary": rep["summary"],
                "sections": rep["sections"], "posting": rep["posting"], "raw": text, "structured": rep["structured"],
                "score": app["score"] if app["score"] is not None else rep["score"],
                "url": app["url"] or rep["url"], "report_path": str(path)}

    def set_status(self, app_id: str, state: str) -> None:
        st = next((s for s in self.states() if s["id"] == state), None)
        if st is None:
            raise ToolError(t("Unknown status: {name}", name=state))
        if not str(app_id).isdigit():
            raise ToolError(t("This row has no number in the tracker, so career-ops can't update it."))
        p = self.node("set-status.mjs", str(app_id), st["label"], timeout=60)
        if p.returncode != 0:
            raise ToolError(t("career-ops couldn't change the status:") + "\n" + self._tail(p, 6))

    def signature(self) -> Any:
        sig: list = []
        for p in (self.tracker_path(), self.data_root() / "cv.md", self.data_root() / "config" / "profile.yml"):
            try:
                st = p.stat()
                sig.append((st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append(None)
        reports = self.data_root() / "reports"
        try:
            sig.append(tuple(sorted((p.name, p.stat().st_mtime_ns) for p in reports.glob("*.md"))))
        except OSError:
            sig.append(None)
        return (str(self.root), self.installed(), tuple(sig))

    # ---- the CV career-ops reads ---------------------------------------------------
    def _sync_state(self) -> dict:
        try:
            data = json.loads(self._sync.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_sync_state(self, data: dict) -> None:
        self._sync.parent.mkdir(parents=True, exist_ok=True)
        self._sync.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def cv_lang(self, doc: Any) -> str:
        langs = [str(x) for x in ((doc.get("settings") or {}).get("languages") or ["en"])] if doc is not None else ["en"]
        want = self.settings()["cv_lang"]
        return want if want in langs else ("en" if "en" in langs else langs[0])

    def cv_files(self, doc: Any, source: str) -> dict[str, str]:
        lang = self.cv_lang(doc)
        files = {"cv.md": cv_markdown(doc, lang, source)}
        if self.settings()["notes"]:
            files["article-digest.md"] = proof_points(doc, lang, source)
        return files

    def sync_cv(self, doc: Any, *, lang: str | None = None, notes: bool | None = None, force: bool = False,
                source: str = "resume.yaml") -> dict:
        """Write cv.md (and article-digest.md) from resume.yaml. A file changed on career-ops' side
        since Resume Studio last wrote it is left alone (a conflict) unless `force`."""
        if not self.installed():
            return {"state": "off"}
        if not self.settings()["sync"]:
            self.last_sync = {"state": "off"}
            return self.last_sync
        data = self.data_root()
        files = self.cv_files(doc, source)
        state = self._sync_state()
        mine = dict(state.get(str(data)) or {})
        wrote, conflicts = [], []
        for name in CV_FILES:
            path = data / name
            cur = _sha(_read(path)) if path.exists() else None
            if name not in files:   # proof points switched off: remove ours if nobody changed it
                if cur is not None and cur == mine.get(name):
                    path.unlink()
                    mine.pop(name, None)
                continue
            new = _sha(files[name])
            if cur == new:
                mine[name] = new
                continue
            if cur is not None and cur != mine.get(name) and not force:
                conflicts.append(name)
                continue
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(files[name], encoding="utf-8", newline="\n")
            os.replace(tmp, path)
            mine[name] = new
            wrote.append(name)
        state[str(data)] = mine
        self._save_sync_state(state)
        self.last_sync = {"state": "conflict" if conflicts else "written" if wrote else "same",
                          "conflicts": conflicts, "wrote": wrote, "at": time.time()}
        return self.last_sync

    # ---- its targeting profile, filled from the resume data by a chat ------------------
    @staticmethod
    def profile_source(doc: Any) -> str:
        """What the profile chat reads: who (name, contacts), the targets (every subset's label and
        headline) and the fact layer about the search (profile notes)."""
        prof = to_plain((doc or {}).get("profile") or {})
        jobs = [(str(v.get("label") or ""), to_plain(v.get("headline") or ""))
                for v in (doc or {}).get("subsets") or [] if v.get("id") != "ALL"]
        return json.dumps({"name": prof.get("name"), "contacts": prof.get("contacts"), "notes": prof.get("notes"),
                           "jobs": jobs}, ensure_ascii=False, sort_keys=True, default=str)

    def profile_state(self, doc: Any) -> dict:
        seen = self._sync_state().get("profile:" + str(self.data_root()))
        return {"filled": bool(seen), "stale": bool(seen) and seen != _sha(self.profile_source(doc))}

    def profile_used(self, doc: Any) -> None:
        state = self._sync_state()
        state["profile:" + str(self.data_root())] = _sha(self.profile_source(doc))
        self._save_sync_state(state)

    def cv_state(self) -> dict:
        s = self.settings()
        data = self.data_root()
        mine = self._sync_state().get(str(data)) or {}
        conflicts = []
        if s["sync"]:
            for name in CV_FILES:
                path = data / name
                if path.exists() and name in mine and _sha(_read(path)) != mine[name]:
                    conflicts.append(name)
                elif path.exists() and name not in mine and (name == "cv.md" or s["notes"]):
                    conflicts.append(name)   # the user's own file from before: never overwritten silently
        return {"sync": s["sync"], "notes": s["notes"], "lang": s["cv_lang"], "conflicts": conflicts,
                "exists": (data / "cv.md").exists(), "path": str(data / "cv.md")}

    def cv_diff(self, doc: Any, name: str, source: str) -> str:
        """How career-ops' copy differs from what Resume Studio would write (unified diff)."""
        path = self.data_root() / name
        theirs = _read(path).splitlines() if path.exists() else []
        ours = self.cv_files(doc, source).get(name, "").splitlines()
        return "\n".join(difflib.unified_diff(ours, theirs, f"Resume Studio ({name})", f"career-ops ({name})", lineterm="", n=1))

    # ---- chats in the career-ops folder ------------------------------------------------
    def workspace_cwd(self) -> Path:
        return self.root

    def chat_context(self, data_path: str) -> str:
        return "\n".join([
            "# Resume Studio · career-ops",
            "",
            "The user is talking to you from the Resume Studio window (a resume editor built on Claude Code),",
            "not from a terminal. The window shows career-ops' own web interface (its web/ app) in its Job search",
            "workspace, with this chat beside it, and its evaluations in an Applications tab next to the resume.",
            "",
            "- This folder is career-ops: follow its AGENTS.md and modes as usual.",
            f"- `cv.md` and `article-digest.md` are written by Resume Studio from the user's resume library",
            f"  (`{data_path}`; its format and editing rules: `{self.skill_md}`) before each message, so edits",
            "  to them would not last. When a mode would add to or change the CV (add, expand, intake…),",
            "  propose the change and, once the user agrees, make it in the resume library instead.",
            "- The resumes the user sends are the subsets (`subsets`) of the resume library: each is a resume for a",
            "  kind of role, sent to many similar postings, and never names a company. For a posting, recommend",
            "  the subset that fits best (the window links it: a `postings` entry on that subset), or suggest",
            "  adjusting one or adding one for that kind of role. PDFs come from Resume Studio (its templates,",
            "  one page): skip career-ops' PDF / HTML / LaTeX CV generation unless the user asks for it.",
            "- The user's targets, constraints and preferences are in the library's `profile.notes` (the private",
            "  fact layer) and its subsets' labels and headlines; career-ops' profile files are filled from them.",
            "- The window shows the tracker and the reports by reading `data/applications.md` and `reports/`:",
            "  keep writing them through career-ops' own scripts so they stay in its canonical format.",
            "- The user can't see a terminal; when they need to decide something, ask with AskUserQuestion.",
        ]) + "\n"

    def actions(self) -> list[dict]:
        return [
            {"id": "interview", "label": t("Interview prep"), "desc": t("Likely questions and your stories for them"), "icon": "chat"},
            {"id": "research", "label": t("Company research"), "desc": t("Strategy, culture, recent moves, your angle"), "icon": "external"},
            {"id": "cover", "label": t("Cover letter"), "desc": t("A draft in the chat"), "icon": "file"},
            {"id": "outreach", "label": t("Outreach message"), "desc": t("A short LinkedIn note to the team or a recruiter"), "icon": "send"},
        ]

    def prompt(self, kind: str, **kw: Any) -> str:
        if kind == "evaluate":
            return (t("Evaluate this job posting with career-ops: its full evaluation (the A–G blocks, the report in "
                      "reports/ with its Machine Summary, and the tracker entry). Skip the PDF and the cover letter: "
                      "the tailored resume is made in Resume Studio.") + "\n\n" + t("The posting:") + "\n" + str(kw["posting"]).strip())
        if kind in ("setup", "profile_update"):
            first = (t("Set up career-ops' profile for me (its first-run onboarding), filled from my Resume Studio data "
                       "instead of asking me everything again.") if kind == "setup" else
                     t("My Resume Studio data changed since career-ops' profile was last filled from it: update the "
                       "profile to match, and keep what I told you in earlier chats unless the data now says otherwise."))
            return (first + "\n"
                    + t("The data file `{data}` (format: `{skill}`) has: my name and contacts under `profile`; my "
                        "job-search background, constraints and preferences in `profile.notes` (the fact layer); the kinds "
                        "of role I apply for as the subsets in `subsets` (each one's `label` and `headline`; ignore `ALL`); and "
                        "the real story of each experience in `entries[].notes`.", data=kw["data_path"], skill=self.skill_md) + "\n"
                    + "- " + t("From them, fill career-ops' profile files the way its onboarding describes (config/profile.yml, "
                               "modes/_profile.md, and the title filters in portals.yml for my target roles).") + "\n"
                    + "- " + t("cv.md and article-digest.md are written by Resume Studio from my library: read them, don't "
                               "edit them.") + "\n"
                    + "- " + t("Ask me with AskUserQuestion, one question at a time, only for what the data doesn't say "
                               "(a salary range, for example).") + "\n"
                    + "- " + t("The fact layer is private: use it to target and score postings, and never copy it word for "
                               "word into anything an employer could read.") + "\n"
                    + "- " + t("At the end, list what you filled in and where each part came from."))
        if kind == "mode":
            m = kw["mode"]
            return t("Run career-ops' {mode} mode ({file}: {purpose}). Ask me with AskUserQuestion for anything it "
                     "needs from me.", mode=m["id"], file=m["file"], purpose=m.get("purpose") or m["id"])
        if kind == "scan":
            return t("Scan for new job postings with career-ops (its scan mode, using portals.yml), then tell me "
                     "which new ones look worth evaluating and why.")
        if kind == "cv_conflict":
            return (t("career-ops' copy of {file} was edited outside Resume Studio. Here is how it differs from what "
                      "Resume Studio writes from my resume library (lines with + exist only in career-ops' copy). "
                      "Tell me which of those changes are real facts worth keeping, and once I agree, put them into "
                      "the resume library; then Resume Studio can write {file} again.", file=kw["file"])
                    + "\n\n```diff\n" + str(kw["diff"]).strip() + "\n```")
        if kind == "action":
            app, action = kw["app"], kw["action"]
            who = f"{app.get('company') or '?'} — {app.get('role') or '?'} (#{app.get('id')}"
            who += f", {app['report']})" if app.get("report") else ")"
            msg = {
                "interview": t("Prepare me for the interviews for {job} with career-ops (its interview-prep mode), building on the evaluation report.", job=who),
                "research": t("Research the company behind {job} with career-ops (its deep mode), building on the evaluation report.", job=who),
                "cover": t("Write a cover letter for {job} with career-ops (its cover mode), from the evaluation report. Show me the text here in the chat; skip the PDF.", job=who),
                "outreach": t("Draft an outreach message for {job} with career-ops (its contacto mode): find who to write to and keep it within LinkedIn's limit.", job=who),
            }.get(action)
            if not msg:
                raise ToolError(t("Unknown action: {name}", name=action))
            return msg
        raise ValueError(kind)
