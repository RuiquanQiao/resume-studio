"""The contract between the window and a job-posting tool.

The window only ever calls these methods and only reads these shapes, so swapping career-ops for
another tool means writing one more class like `career_ops.CareerOps`, not changing the UI.

Shapes (plain dicts, JSON-safe):

Application, one row of the tool's tracker:
    id            str     the tool's own stable id (career-ops: the tracker's `#`)
    company, role str
    date          str     YYYY-MM-DD or ''
    score         float | None   the tool's fit score
    score_max     float   what a perfect score is (career-ops: 5)
    status        str     a state id from states(), or '' when the tool's value isn't one
    status_label  str     how the tool writes it
    url           str     the posting
    notes         str
    report        str     the evaluation file, relative to the tool's data folder ('' = none)

Application detail = Application plus
    title         str
    summary       dict    what the window shows on top (every key optional): verdict, decision,
                          legitimacy, archetype, risk, confidence, strengths[], hard_stops[],
                          soft_gaps[], next_action, comp, work_auth, location,
                          requirements[{requirement, importance, match, evidence}]
    sections      list    [{title, markdown}] the rest of the report, in order
    posting       str     the posting's text when the report archived it
    raw           str     the whole report, for when nothing above could be read

Anything a tool doesn't provide is simply missing; the window shows what it gets and falls back to
the raw report. That is what keeps the window working when a tool changes its formats.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

Progress = Callable[[str, str], None]   # (step, state: running | done | error, detail via the step text)


class ToolError(Exception):
    """Something the user should read (in their interface language)."""


class JobTool(ABC):
    id: str = ""
    name: str = ""
    homepage: str = ""
    license: str = ""
    author: str = ""

    busy: str | None = None    # the install / update task running now, if any

    # ---- where it is, and whether it is usable ----------------------------
    @abstractmethod
    def installed(self) -> bool:
        """Cheap: is there a usable copy on this computer?"""

    @abstractmethod
    def data_folder(self) -> Path:
        """Where the tool keeps the user's files (reports, tracker)."""

    @abstractmethod
    def status(self) -> dict:
        """{installed, root, version, needs_setup, setup_missing[], checks[{id, ok, detail}],
        cv{...}, busy}"""

    @abstractmethod
    def install(self, progress: Progress) -> None:
        """Get the tool onto this computer (inside the app's own folder)."""

    @abstractmethod
    def use_folder(self, path: Path) -> None:
        """Use a copy the user already has."""

    @abstractmethod
    def check_update(self) -> dict:
        """{status: up-to-date | update-available | offline | error, local, remote, changelog}"""

    @abstractmethod
    def update(self, progress: Progress) -> dict:
        """Update with the tool's own updater; returns status() afterwards."""

    @abstractmethod
    def rollback(self) -> dict:
        """Undo the last update with the tool's own rollback."""

    @abstractmethod
    def repair(self, progress: Progress) -> dict:
        """Redo the install steps that don't download the tool (dependencies and the like)."""

    # ---- data ------------------------------------------------------------
    @abstractmethod
    def states(self) -> list[dict]:
        """[{id, label}] in lifecycle order."""

    @abstractmethod
    def applications(self) -> list[dict]:
        """Every tracked application, newest first."""

    @abstractmethod
    def application(self, app_id: str) -> dict:
        """One application with its report (see the module docstring). KeyError if unknown."""

    @abstractmethod
    def set_status(self, app_id: str, state: str) -> None:
        """Through the tool's own write path."""

    @abstractmethod
    def signature(self) -> Any:
        """Something cheap that changes whenever the tracker or a report changes."""

    # ---- the resume the tool reads -----------------------------------------
    @abstractmethod
    def sync_cv(self, doc: Any, *, lang: str, notes: bool, force: bool = False) -> dict:
        """Write the tool's CV input from resume.yaml. Never overwrites edits made on the tool's
        side unless `force`: returns {state: written | same | conflict | off, ...}."""

    # ---- work done by Claude in the tool's folder --------------------------
    @abstractmethod
    def workspace_cwd(self) -> Path:
        """Where a chat for this tool runs."""

    def env(self) -> dict[str, str]:
        """Added to the environment of everything started in the tool's folder."""
        return {}

    @abstractmethod
    def chat_context(self, data_path: str) -> str:
        """The short note appended to Claude's system prompt in this tool's chats."""

    @abstractmethod
    def actions(self) -> list[dict]:
        """What can be asked about one application: [{id, label, desc}] (labels in the UI language)."""

    @abstractmethod
    def prompt(self, kind: str, **kw: Any) -> str:
        """The message for `kind`: evaluate (posting=), setup, action (app=, action=), scan, mode (mode=)."""

    def profile_state(self, doc: Any) -> dict:
        """Whether the tool's targeting profile was filled from the resume data, and whether that data
        changed since: {filled: bool, stale: bool}."""
        return {}

    def profile_used(self, doc: Any) -> None:
        """A profile chat was started from this resume data: remember what it saw."""

    def modes(self) -> list[dict]:
        """Everything the tool can do, as [{id, file, purpose}]: each one can be started as a chat."""
        return []

    # ---- the tool's own web interface (optional) -------------------------------
    def has_web(self) -> bool:
        """Does the installed version ship a web interface the window can host?"""
        return False

    def web(self, progress: Progress, *, rebuild: bool = False) -> Any:
        """Prepare the web interface (install, build) and return a core.webhost.WebSpec to run it."""
        raise ToolError("no web interface")

    def web_routes(self) -> dict[str, str]:
        """Paths of the web interface the window links to: {"application": "/…/{id}", "home": "/"}."""
        return {}
