"""HTTP routes for the resume UI."""
from __future__ import annotations

import threading
from pathlib import Path

from core.claude import ClaudeRunner
from core.server import App, FileResponse, HttpError, Request

from . import model, prompts
from .service import Conflict, Studio


def register(app: App, studio: Studio, runner: ClaudeRunner) -> None:
    render_lock = threading.Lock()
    studio.extra_state = lambda: {"claude": {"available": runner.available(), "running": runner.running()}}

    def body(req: Request) -> dict:
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "body must be a JSON object")
        return data

    @app.route("GET", "/api/state")
    def state(req: Request):
        return studio.state()

    @app.route("PUT", "/api/item")
    def put_item(req: Request):
        b = body(req)
        kind = b.get("kind")
        if kind not in model.KINDS:
            raise HttpError(400, f"unknown kind {kind}")
        try:
            return studio.update_item(kind, b.get("id"), b.get("data"), b.get("base"))
        except Conflict as c:
            raise HttpError(409, "这一项刚被别处（Claude 或另一个窗口）修改过", {"current": c.current})
        except (KeyError, ValueError) as e:
            raise HttpError(400, str(e))

    @app.route("POST", "/api/entries")
    def new_entry(req: Request):
        b = body(req)
        new_id, s = studio.create_entry(str(b.get("section") or "projects"))
        return {"id": new_id, **s}

    @app.route("DELETE", "/api/entries/{entry_id}")
    def del_entry(req: Request):
        return studio.delete_entry(req.params["entry_id"])

    @app.route("POST", "/api/versions")
    def new_version(req: Request):
        b = body(req)
        vid, s = studio.create_version(b.get("copy_from"), b.get("id"))
        return {"id": vid, **s}

    @app.route("DELETE", "/api/versions/{vid}")
    def del_version(req: Request):
        return studio.delete_version(req.params["vid"])

    @app.route("POST", "/api/versions/{vid}/render")
    def render(req: Request):
        with render_lock:
            try:
                return studio.render(req.params["vid"])
            except KeyError as e:
                raise HttpError(404, str(e))

    @app.route("GET", "/api/versions/{vid}/pdf")
    def pdf(req: Request):
        path = studio.pdf_path(req.params["vid"])
        if not path.exists():
            raise HttpError(404, "还没有渲染过")
        return FileResponse(path, "application/pdf")

    @app.route("POST", "/api/versions/{vid}/export")
    def export(req: Request):
        with render_lock:
            return studio.export(req.params["vid"])

    @app.route("POST", "/api/claude")
    def claude(req: Request):
        b = body(req)
        instruction = str(b.get("instruction") or "")
        entry_id = b.get("entry_id")
        doc, _ = studio.store.load()
        add_dirs: list[str] = []
        if b.get("mode") == "polish" and entry_id:
            entry = model.get_item(doc, "entry", entry_id)
            if entry is None:
                raise HttpError(404, f"{entry_id} 不存在")
            add_dirs = [str(p) for p in entry.get("evidence") or []]
            langs = [str(x) for x in (doc.get("settings") or {}).get("languages") or []]
            prompt = prompts.polish_entry(str(studio.store.path), entry_id, langs, instruction)
        else:
            if not instruction.strip():
                raise HttpError(400, "请写下要 Claude 做什么")
            prompt = prompts.free_task(str(studio.store.path), b.get("version_id"), entry_id, instruction)
        # evidence may be a file; give Claude its folder
        add_dirs = [str(Path(p).parent if Path(p).is_file() else Path(p)) for p in add_dirs]
        try:
            job = runner.start(prompt, meta={"mode": b.get("mode") or "free", "entry_id": entry_id},
                               add_dirs=add_dirs)
        except RuntimeError as e:
            raise HttpError(400, str(e))
        return {"job": job}

    @app.route("POST", "/api/claude/{job}/cancel")
    def cancel(req: Request):
        runner.cancel(req.params["job"])
        return {"ok": True}
