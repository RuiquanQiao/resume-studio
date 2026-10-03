"""HTTP routes for the resume UI."""
from __future__ import annotations

import base64
import mimetypes
import threading
from pathlib import Path

from core.chat import IMAGE_TYPES, MODES, ChatRunner, valid_effort, valid_mode, valid_model
from core.claude_info import ClaudeInfo
from core.server import App, FileResponse, HttpError, Request

from . import evidence, model, prompts
from .evidence import EvidenceTracker
from .service import Conflict, Studio


def register(app: App, studio: Studio, chats: ChatRunner, tracker: EvidenceTracker, info: ClaudeInfo) -> None:
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

    @app.route("POST", "/api/entries/{entry_id}/move")
    def move_entry(req: Request):
        try:
            return studio.move_entry(req.params["entry_id"], int(body(req).get("dir") or 0))
        except KeyError as e:
            raise HttpError(404, str(e))

    @app.route("DELETE", "/api/entries/{entry_id}")
    def del_entry(req: Request):
        return studio.delete_entry(req.params["entry_id"])

    @app.route("POST", "/api/versions")
    def new_version(req: Request):
        b = body(req)
        try:
            vid, s = studio.create_version(b.get("copy_from"), b.get("id"), b.get("label"))
        except ValueError as e:
            raise HttpError(400, str(e))
        return {"id": vid, **s}

    @app.route("DELETE", "/api/versions/{vid}")
    def del_version(req: Request):
        try:
            return studio.delete_version(req.params["vid"])
        except ValueError as e:
            raise HttpError(400, str(e))

    # ---- rendering -------------------------------------------------------
    @app.route("POST", "/api/versions/{vid}/render")
    def render(req: Request):
        with render_lock:
            try:
                return studio.render(req.params["vid"])
            except KeyError as e:
                raise HttpError(404, str(e))

    @app.route("POST", "/api/versions/{vid}/measure")
    def measure(req: Request):
        """Line counts for the language being edited when the preview shows the other one."""
        lang = str(body(req).get("lang") or "")
        if lang not in prompts.LANG_ZH:
            raise HttpError(400, "lang 只能是 en 或 zh")
        with render_lock:
            try:
                r = studio.render(req.params["vid"], lang=lang, images=False)
            except KeyError as e:
                raise HttpError(404, str(e))
        return {"ok": r["ok"], "lang": r["lang"], "lines": r["lines"], "missing": r["missing"]}

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
    @app.route("GET", "/api/claude")
    def claude_info(req: Request):
        """Models, effort levels, slash commands and account, as the user's Claude Code reports them."""
        if req.query.get("refresh"):
            info.refresh()
        elif info.data is None and not info.loading:
            info.refresh_async(lambda: app.bus.publish("claude.info", {}))
        return {**info.get(), "available": chats.available(), "limits": chats.limits, "modes": MODES}

    @app.route("GET", "/api/chats")
    def list_chats(req: Request):
        return {"chats": chats.store.list(), "running": chats.running(), "waiting": chats.waiting(),
                "available": chats.available()}

    def chat_fields(b: dict) -> dict:
        fields = {}
        if "model" in b:
            if not valid_model(b["model"]):
                raise HttpError(400, f"不认识的模型：{b['model']}")
            fields["model_choice"] = b["model"] or None
        if "effort" in b:
            if not valid_effort(b["effort"]):
                raise HttpError(400, f"不认识的推理强度：{b['effort']}")
            fields["effort"] = b["effort"] or None
        if "mode" in b:
            if not valid_mode(b["mode"]):
                raise HttpError(400, f"不认识的权限模式：{b['mode']}")
            fields["mode"] = b["mode"] or None
        if "title" in b:
            title = " ".join(str(b["title"] or "").split())[:60]
            if not title:
                raise HttpError(400, "标题不能为空")
            fields["title"] = title
        return fields

    @app.route("POST", "/api/chats")
    def new_chat(req: Request):
        return chats.store.create(**chat_fields(body(req)))

    @app.route("PUT", "/api/chats/{cid}")
    def update_chat(req: Request):
        cid = req.params["cid"]
        fields = chat_fields(body(req))
        meta = chats.store.update_meta(cid, touch=False, **fields) if fields else chats.store.meta(cid)
        if meta is None:
            raise HttpError(404, "对话不存在")
        # a reply in progress follows the new model / mode too, like switching in Claude Code mid-turn
        chats.set_live(cid, model=fields["model_choice"] or "" if "model_choice" in fields else None,
                       mode=(fields["mode"] or info_mode()) if "mode" in fields else None)
        return meta

    def info_mode() -> str:
        return str((info.data or {}).get("permission_mode") or "default")

    @app.route("GET", "/api/chats/{cid}")
    def get_chat(req: Request):
        meta = chats.store.meta(req.params["cid"])
        if meta is None:
            raise HttpError(404, "对话不存在")
        return {"chat": meta, "messages": chats.store.messages(meta["id"]), "running": chats.busy(meta["id"]),
                "live": chats.live(meta["id"])}

    @app.route("DELETE", "/api/chats/{cid}")
    def delete_chat(req: Request):
        if chats.busy(req.params["cid"]):
            raise HttpError(409, "这段对话还在回复中")
        chats.store.delete(req.params["cid"])
        return {"chats": chats.store.list()}

    @app.route("POST", "/api/chats/{cid}/answer")
    def answer(req: Request):
        """The user's answer to a permission card, one of Claude's questions, or its plan."""
        b = body(req)
        try:
            return {"ask": chats.answer(req.params["cid"], str(b.get("request_id") or ""), b)}
        except KeyError:
            raise HttpError(409, "这个请求已经结束了")

    @app.route("POST", "/api/chats/{cid}/fork")
    def fork(req: Request):
        """A new chat that continues from just before message `index` (that message can be rewritten)."""
        cid = req.params["cid"]
        meta = chats.store.meta(cid)
        if meta is None:
            raise HttpError(404, "对话不存在")
        msgs = chats.store.messages(cid)
        try:
            index = int(body(req).get("index"))
        except (TypeError, ValueError):
            raise HttpError(400, "index 必须是数字")
        if not 0 <= index < len(msgs) or msgs[index].get("role") != "user":
            raise HttpError(400, "只能从你发的消息处分叉")
        before = msgs[:index]
        at = next((m.get("uuid") for m in reversed(before) if m.get("role") == "assistant"), None)
        if before and not at:
            raise HttpError(409, "这段对话是旧版本记录的，没法从中间分叉；可以从第一条开始")
        new = chats.store.create(title=f"{meta.get('title') or '对话'}（分叉）", model_choice=meta.get("model_choice"),
                                 effort=meta.get("effort"), mode=meta.get("mode"), tasks=None,
                                 fork={"session_id": meta.get("session_id"), "at": at} if before else None)
        chats.store.save_messages(new["id"], before)
        return {"chat": new, "text": msgs[index].get("text") or ""}

    @app.route("DELETE", "/api/chats/{cid}/queue/{qid}")
    def unqueue(req: Request):
        return {"ok": chats.unqueue(req.params["cid"], req.params["qid"])}

    @app.route("GET", "/api/chats/{cid}/file")
    def chat_file(req: Request):
        """An attachment of this chat (for thumbnails) — only paths recorded in its messages."""
        cid = req.params["cid"]
        want = (req.query.get("path") or [""])[0]
        recorded = {f.get("path") for m in chats.store.messages(cid) for f in m.get("files") or []}
        live = chats.live(cid) or {}
        recorded |= {f.get("path") for q in live.get("queue") or [] for f in q["msg"].get("files") or []}
        path = Path(want)
        if want not in recorded or not path.is_file():
            raise HttpError(404, "没有这个附件")
        return FileResponse(path)

    def attachments(cid: str, items: list) -> tuple[list[dict], list[dict]]:
        """Pasted / dropped files (data URLs) are saved next to the chat; picked files keep their path.
        Images up to 5 MB go to Claude inline; everything else is referenced by path for it to read."""
        images, files = [], []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "file")
            if it.get("data"):
                head, _, b64 = str(it["data"]).partition(",")
                mime = head.removeprefix("data:").split(";")[0] or "application/octet-stream"
                try:
                    raw = base64.b64decode(b64, validate=False)
                except ValueError:
                    raise HttpError(400, f"附件 {name} 读不出来")
                if len(raw) > 30 * 1024 * 1024:
                    raise HttpError(413, f"附件 {name} 超过 30 MB")
                path = chats.store.save_file(cid, name, raw)
            elif it.get("path"):
                path = Path(str(it["path"]))
                if not path.is_file():
                    raise HttpError(400, f"找不到文件：{path}")
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            else:
                continue
            is_image = mime in IMAGE_TYPES and path.stat().st_size <= 5 * 1024 * 1024
            if is_image:
                images.append({"path": str(path), "media_type": mime})
            files.append({"name": name, "path": str(path).replace("\\", "/"), "image": is_image})
        return images, files

    # ---- evidence: has a project changed since Claude last read it? ---------
    @app.route("GET", "/api/evidence")
    def evidence_status(req: Request):
        doc, _ = studio.store.load()
        return {"status": tracker.status(doc.get("entries") or [], force=bool(req.query.get("force")))}

    @app.route("POST", "/api/evidence/{eid}/synced")
    def evidence_synced(req: Request):
        """The user says the entry already reflects the project as it is now."""
        doc, _ = studio.store.load()
        entry = model.get_item(doc, "entry", req.params["eid"])
        if entry is None:
            raise HttpError(404, "经历不存在")
        tracker.mark_synced(str(entry.get("id")), [str(p) for p in entry.get("evidence") or []])
        return {"status": tracker.status([entry], force=True)}

    def version_entries(doc, version) -> list:
        if version is None:
            return []
        if model.is_all(version):
            return list(doc.get("entries") or [])
        by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
        return [by_id[str(i)] for i in version.get("entries") or [] if str(i) in by_id]

    def long_bullets(version_id: str, lang: str) -> list[dict]:
        with render_lock:
            r = studio.render(version_id, lang=lang, images=False)
        if not r["ok"]:
            raise HttpError(409, "渲染失败，先看预览上方的错误信息")
        return studio.bullet_report(r)

    ACTIONS = ("polish", "translate", "fit", "sync", "batch_translate", "batch_fit", "batch_sync")

    @app.route("POST", "/api/chats/{cid}/send")
    def send(req: Request):
        """A chat message: typed by the user, or one of the editor's buttons (`action`)."""
        b = body(req)
        cid = req.params["cid"]
        doc, _ = studio.store.load()
        data_path = str(studio.store.path)
        version = model.get_version(doc, b.get("version_id")) if b.get("version_id") else None
        vid = str((version or {}).get("id") or model.ALL_ID)
        vlang = str((version or {}).get("lang") or "en")
        langs = [str(l) for l in (doc.get("settings") or {}).get("languages") or ["en"]]
        entry = model.get_item(doc, "entry", b.get("entry_id")) if b.get("entry_id") else None
        action = "polish" if b.get("polish") else b.get("action")
        if action and action not in ACTIONS:
            raise HttpError(400, f"unknown action {action}")
        if action in ("polish", "translate", "fit", "sync") and entry is None:
            raise HttpError(404, "这条经历不存在")
        lang = str(b.get("lang") or vlang)
        name = lambda e: model.tr(e.get("title"), lang) or model.tr(e.get("title"), "en") or "这条经历"  # noqa: E731
        instruction = str(b.get("text") or "")
        involved = [entry] if entry is not None else []
        synced: list = []   # entries whose evidence baseline moves forward when the turn succeeds

        if action == "polish":
            text = prompts.polish_message(name(entry), instruction, lang, vid, data_path)
            synced = [entry]
        elif action == "translate":
            target = str(b.get("target") or "zh")
            if target not in prompts.LANG_ZH:
                raise HttpError(400, "目标语言只能是 en 或 zh")
            text = prompts.translate_message(name(entry), target, vid, data_path, instruction)
        elif action == "fit":
            untranslated = set(model.missing_text(entry, lang).get("bullets", []))   # shown in the other language
            mine = [x for x in long_bullets(vid, lang) if x["id"] == str(entry.get("id")) and x["index"] not in untranslated]
            if not mine:
                raise HttpError(409, "这一条的要点在当前预览里都已经是一行了")
            text = prompts.fit_message(name(entry), lang, vid, data_path, mine)
        elif action == "sync":
            st = tracker.status([entry], force=True).get(str(entry.get("id")))
            rows = [r for r in (st or {}).get("paths", []) if r["state"] in ("changed", "new")]
            if not rows:
                raise HttpError(409, "这个项目自上次同步后没有变化")
            text = prompts.sync_message(name(entry), "\n".join(evidence.describe(r) if r["state"] == "changed"
                                                               else f"- {r['path']}（还没读过，请完整看一遍）"
                                                               for r in rows), langs, vid, data_path)
            synced = [entry]
        elif action == "batch_translate":
            target = str(b.get("target") or "zh")
            involved = [e for e in version_entries(doc, version) if model.missing_text(e, target)]
            if not involved:
                raise HttpError(409, f"当前岗位里的经历都已经有{prompts.LANG_ZH.get(target, target)}了")
            text = prompts.batch_message("translate", version, data_path, [name(e) for e in involved], target)
        elif action == "batch_fit":
            ids = []
            for x in long_bullets(vid, vlang):
                if x["id"] not in ids:
                    ids.append(x["id"])
            involved = [model.get_item(doc, "entry", i) for i in ids]
            if not involved:
                raise HttpError(409, "当前预览里所有要点都已经是一行了")
            text = prompts.batch_message("fit", version, data_path, [name(e) for e in involved])
        elif action == "batch_sync":
            st = tracker.status(doc.get("entries") or [], force=True)
            involved = [e for e in doc.get("entries") or [] if (st.get(str(e.get("id"))) or {}).get("state") == "changed"]
            if not involved:
                raise HttpError(409, "没有项目在上次同步后有变化")
            parts = []
            for e in involved:
                rows = [r for r in st[str(e.get("id"))]["paths"] if r["state"] == "changed"]
                parts.append(f"## {name(e)}\n" + "\n".join(evidence.describe(r) for r in rows))
            text = ("这些经历的项目文件夹自上次同步后有更新，请逐条处理：\n\n" + "\n\n".join(parts) + "\n\n"
                    + prompts.sync_rules(langs, vid, data_path))
            synced = involved
        else:
            text = instruction.strip()
            if not text and not b.get("attachments"):
                raise HttpError(400, "消息是空的")

        add_dirs = []
        for e in involved:  # let Claude read the involved entries' evidence folders
            for p in e.get("evidence") or []:
                path = Path(str(p))
                add_dirs.append(str(path.parent if path.is_file() else path))
        roots = [str(p) for p in (doc.get("settings") or {}).get("project_roots") or []]
        context = prompts.ui_context(data_path, version, entry, roots)
        targets = [(str(e.get("id")), [str(p) for p in e.get("evidence") or []]) for e in synced]

        def done(ok: bool) -> None:
            if ok:
                for eid, paths in targets:
                    tracker.mark_synced(eid, paths)
                app.bus.publish("evidence", {"entries": [eid for eid, _ in targets]})

        images, files = attachments(cid, b.get("attachments") or [])
        prompt = text
        others = [f for f in files if not f["image"]]
        if others:   # Claude reads these itself (Read handles PDFs, docs, code…)
            prompt += "\n\n附件（请用 Read 查看）：\n" + "\n".join(f"- {f['path']}" for f in others)
        try:
            r = chats.send(cid, prompt, context=context, add_dirs=add_dirs, on_done=done if targets else None,
                           meta={"action": action, "polish": action == "polish", "entry_id": b.get("entry_id"),
                                 "entries": [str(e.get("id")) for e in involved]},
                           images=images, files=files, shown=text if action is None else None)
        except KeyError:
            raise HttpError(404, "对话不存在")
        except RuntimeError as e:
            raise HttpError(409, str(e))
        return {"ok": True, "text": text, "files": files, **r}

    @app.route("POST", "/api/chats/{cid}/cancel")
    def cancel(req: Request):
        chats.cancel(req.params["cid"])
        return {"ok": True}
