"""HTTP routes for the resume UI."""
from __future__ import annotations

import threading
from pathlib import Path

from core.chat import ChatRunner
from core.server import App, FileResponse, HttpError, Request

from . import model, prompts
from .service import Conflict, Studio


def register(app: App, studio: Studio, chats: ChatRunner) -> None:
    render_lock = threading.Lock()
    studio.extra_state = lambda: {"claude": {"available": chats.available(), "running": chats.running()}}

    def body(req: Request) -> dict:
        data = req.json()
        if not isinstance(data, dict):
            raise HttpError(400, "body must be a JSON object")
        return data

    # ---- data ------------------------------------------------------------
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

    # ---- rendering -------------------------------------------------------
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

    @app.route("GET", "/api/versions/{vid}/page/{n}")
    def page(req: Request):
        try:
            n = int(req.params["n"])
        except ValueError:
            raise HttpError(400, "bad page")
        path = studio.page_image(req.params["vid"], n)
        if path is None:
            raise HttpError(404, "no such page")
        return FileResponse(path, "image/png")

    @app.route("POST", "/api/versions/{vid}/export")
    def export(req: Request):
        with render_lock:
            return studio.export(req.params["vid"])

    # ---- chat (real Claude Code sessions) --------------------------------
    @app.route("GET", "/api/chats")
    def list_chats(req: Request):
        return {"chats": chats.store.list(), "running": chats.running(), "available": chats.available()}

    @app.route("POST", "/api/chats")
    def new_chat(req: Request):
        return chats.store.create()

    @app.route("GET", "/api/chats/{cid}")
    def get_chat(req: Request):
        meta = chats.store.meta(req.params["cid"])
        if meta is None:
            raise HttpError(404, "对话不存在")
        return {"chat": meta, "messages": chats.store.messages(meta["id"]), "running": chats.busy(meta["id"])}

    @app.route("DELETE", "/api/chats/{cid}")
    def delete_chat(req: Request):
        if chats.busy(req.params["cid"]):
            raise HttpError(409, "这段对话还在回复中")
        chats.store.delete(req.params["cid"])
        return {"chats": chats.store.list()}

    @app.route("POST", "/api/chats/{cid}/send")
    def send(req: Request):
        b = body(req)
        cid = req.params["cid"]
        doc, _ = studio.store.load()
        version = model.get_item(doc, "version", b.get("version_id")) if b.get("version_id") else None
        entry = model.get_item(doc, "entry", b.get("entry_id")) if b.get("entry_id") else None

        if b.get("polish"):
            if entry is None:
                raise HttpError(404, f"{b.get('entry_id')} 不存在")
            text = prompts.polish_message(str(entry.get("id")), str(b.get("text") or ""))
        else:
            text = str(b.get("text") or "").strip()
            if not text:
                raise HttpError(400, "消息是空的")
        add_dirs = []
        if entry is not None:  # let Claude read this entry's evidence folders
            for p in entry.get("evidence") or []:
                path = Path(str(p))
                add_dirs.append(str(path.parent if path.is_file() else path))
        context = prompts.ui_context(str(studio.store.path), version, entry)
        try:
            chats.send(cid, text, context=context, add_dirs=add_dirs,
                       meta={"polish": bool(b.get("polish")), "entry_id": b.get("entry_id")})
        except KeyError:
            raise HttpError(404, "对话不存在")
        except RuntimeError as e:
            raise HttpError(409, str(e))
        return {"ok": True, "text": text}

    @app.route("POST", "/api/chats/{cid}/cancel")
    def cancel(req: Request):
        chats.cancel(req.params["cid"])
        return {"ok": True}
