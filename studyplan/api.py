"""The plan's routes (docs/PLAN.md). Claude writes the plan with scripts/plan.py, straight to the file;
a watcher tells the window, so a change from anywhere (Claude, another editor) shows up by itself."""
from __future__ import annotations

from typing import Any, Callable

from core.chat import ChatRunner
from core.i18n import t
from core.server import App, HttpError, Request
from core.watcher import SignatureWatcher
from resume.service import Studio

from . import prompts
from .store import ConflictError, Plan


def register(app: App, studio: Studio, chats: ChatRunner, *, watch_interval: float = 0.5) -> tuple[Plan, SignatureWatcher]:
    plan = Plan(studio.store.state_dir)
    publish = lambda: app.bus.publish("plan", {})  # noqa: E731
    watcher = SignatureWatcher(plan.signature, publish, interval=max(watch_interval, 0.5))
    watcher.start()
    chats.notes.append(lambda: prompts.tools_note(str(studio.store.path), plan))

    def body(req: Request) -> dict:
        b = req.json()
        if not isinstance(b, dict):
            raise HttpError(400, "body must be a JSON object")
        return b

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            out = fn()
        except ConflictError as e:
            raise HttpError(409, str(e))
        except KeyError as e:
            raise HttpError(404, str(e).strip("'\""))
        except ValueError as e:
            raise HttpError(400, str(e))
        publish()
        return out

    def num(req: Request) -> int:
        try:
            return int(req.params["n"])
        except ValueError:
            raise HttpError(404, t("The plan has no version {n}", n=req.params["n"]))

    @app.route("GET", "/api/plan")
    def get_plan(req: Request):
        return plan.state()

    @app.route("PUT", "/api/plan")
    def put_plan(req: Request):
        """The user's own edit, kept as a version ({text, why?, base}); `small`: a change that goes into
        the latest version. `base` is the digest the window started from (null: overwrite anyway)."""
        b = body(req)
        base = b.get("base") if isinstance(b.get("base"), str) else None
        if b.get("small"):
            return guard(lambda: plan.touch(b.get("text"), base=base))
        return guard(lambda: plan.write(b.get("text"), why=str(b.get("why") or ""), base=base))

    @app.route("POST", "/api/plan/tick")
    def tick(req: Request):
        b = body(req)
        try:
            index = int(b.get("index"))
        except (TypeError, ValueError):
            raise HttpError(400, "index must be a number")
        base = b.get("base") if isinstance(b.get("base"), str) else None
        return guard(lambda: plan.tick(index, bool(b.get("done")), base=base))

    @app.route("GET", "/api/plan/versions/{n}")
    def get_version(req: Request):
        n = num(req)
        try:
            return plan.get(n)
        except KeyError as e:
            raise HttpError(404, str(e).strip("'\""))

    @app.route("POST", "/api/plan/versions/{n}/restore")
    def restore(req: Request):
        n = num(req)
        return guard(lambda: plan.restore(n))

    return plan, watcher
