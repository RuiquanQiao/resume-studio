"""HTTP routes for the resume UI."""
from __future__ import annotations

import base64
import mimetypes
import threading
from pathlib import Path

from core import agents
from core.agents import AgentHub
from core.chat import IMAGE_TYPES, MODES, ChatRunner, valid_effort, valid_mode, valid_model
from core.i18n import t
from core.server import App, FileResponse, HttpError, Request

from . import evidence, model, prompts
from .evidence import EvidenceTracker
from .service import Conflict, Studio


def register(app: App, studio: Studio, chats: ChatRunner, tracker: EvidenceTracker, hub: AgentHub,
             prefs=None) -> None:
    render_lock = threading.Lock()

    def default_agent() -> str:
        """The agent a new chat runs on: the last one picked in this app (Claude Code at first)."""
        picked = ((prefs.load().get("chat_defaults") if prefs else None) or {}).get("agent")
        return agents.get(picked).id

    chats.default_agent = default_agent
    studio.extra_state = lambda: {"claude": {"available": chats.available(default_agent()), "running": chats.running()}}

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
            raise HttpError(409, t("This item was just changed elsewhere (the AI or another window)"), {"current": c.current})
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

    @app.route("POST", "/api/subsets")
    def new_subset(req: Request):
        b = body(req)
        try:
            vid, s = studio.create_subset(b.get("copy_from"), b.get("id"), b.get("label"))
        except ValueError as e:
            raise HttpError(400, str(e))
        return {"id": vid, **s}

    @app.route("DELETE", "/api/subsets/{vid}")
    def del_subset(req: Request):
        try:
            return studio.delete_subset(req.params["vid"])
        except ValueError as e:
            raise HttpError(400, str(e))

    # ---- rendering -------------------------------------------------------
    @app.route("POST", "/api/subsets/{vid}/render")
    def render(req: Request):
        with render_lock:
            try:
                return studio.render(req.params["vid"])
            except KeyError as e:
                raise HttpError(404, str(e))

    @app.route("POST", "/api/subsets/{vid}/measure")
    def measure(req: Request):
        """Line counts for the language being edited when the preview shows the other one."""
        lang = str(body(req).get("lang") or "")
        if not lang:
            raise HttpError(400, t("Missing language"))
        with render_lock:
            try:
                r = studio.render(req.params["vid"], lang=lang, images=False)
            except KeyError as e:
                raise HttpError(404, str(e))
        return {"ok": r["ok"], "lang": r["lang"], "lines": r["lines"], "missing": r["missing"]}

    @app.route("GET", "/api/subsets/{vid}/pdf")
    def pdf(req: Request):
        path = studio.pdf_path(req.params["vid"])
        if not path.exists():
            raise HttpError(404, t("Not rendered yet"))
        return FileResponse(path, "application/pdf")

    @app.route("GET", "/api/subsets/{vid}/page/{n}")
    def page(req: Request):
        try:
            n = int(req.params["n"])
        except ValueError:
            raise HttpError(400, "bad page")
        path = studio.page_image(req.params["vid"], n)
        if path is None:
            raise HttpError(404, "no such page")
        return FileResponse(path, "image/png")

    @app.route("POST", "/api/subsets/{vid}/export")
    def export(req: Request):
        with render_lock:
            return studio.export(req.params["vid"])

    # ---- a subset's versions ---------------------------------------------
    def number(req: Request) -> int:
        try:
            return int(req.params["n"])
        except ValueError:
            raise HttpError(400, "bad version")

    def versions_call(fn):
        try:
            return fn()
        except KeyError as e:
            raise HttpError(404, str(e).strip("'\""))
        except ValueError as e:
            raise HttpError(400, str(e))

    @app.route("GET", "/api/subsets/{vid}/versions")
    def subset_versions(req: Request):
        return versions_call(lambda: studio.subset_versions(req.params["vid"]))

    @app.route("POST", "/api/subsets/{vid}/versions")
    def commit_subset(req: Request):
        """Keep what the subset says now as its next version, with the user's note."""
        note = str(body(req).get("note") or "")[:300]
        return versions_call(lambda: studio.commit_subset(req.params["vid"], note))

    @app.route("PUT", "/api/subsets/{vid}/versions/{n}")
    def version_note(req: Request):
        note = str(body(req).get("note") or "")[:300]
        return versions_call(lambda: studio.set_version_note(req.params["vid"], number(req), note))

    @app.route("GET", "/api/subsets/{vid}/versions/{n}/compare")
    def compare_versions(req: Request):
        """From version n to `?to=<m>`, or to now."""
        to = (req.query.get("to") or [""])[0]
        try:
            to = int(to) if to else None
        except ValueError:
            raise HttpError(400, "bad version")
        return versions_call(lambda: studio.compare_versions(req.params["vid"], number(req), to))

    @app.route("GET", "/api/subsets/{vid}/versions/{n}/restore")
    def restore_impact(req: Request):
        """What going back to version n would change beyond the subset (shared entries, profile)."""
        return versions_call(lambda: studio.restore_impact(req.params["vid"], number(req)))

    @app.route("POST", "/api/subsets/{vid}/versions/{n}/restore")
    def restore_version(req: Request):
        return versions_call(lambda: studio.restore_version(req.params["vid"], number(req)))

    @app.route("POST", "/api/subsets/{vid}/versions/{n}/render")
    def render_version(req: Request):
        with render_lock:
            return versions_call(lambda: studio.render_version(req.params["vid"], number(req)))

    @app.route("GET", "/api/subsets/{vid}/versions/{n}/pdf")
    def version_pdf(req: Request):
        path = studio.pdf_path(req.params["vid"], number(req))
        if not path.exists():
            raise HttpError(404, t("Not rendered yet"))
        return FileResponse(path, "application/pdf")

    @app.route("GET", "/api/subsets/{vid}/versions/{n}/page/{p}")
    def version_page(req: Request):
        try:
            p = int(req.params["p"])
        except ValueError:
            raise HttpError(400, "bad page")
        path = studio.page_image(req.params["vid"], p, number(req))
        if path is None:
            raise HttpError(404, "no such page")
        return FileResponse(path, "image/png")

    @app.route("POST", "/api/subsets/{vid}/versions/{n}/export")
    def export_version(req: Request):
        with render_lock:
            return versions_call(lambda: studio.export(req.params["vid"], number(req)))

    # ---- chat (real sessions of the user's own coding agent) -------------
    def mode_list(agent: agents.Agent, data: dict) -> list:
        """The permission modes the picker offers: Claude Code's; Codex's as its approval policy and
        sandbox (core.drivers.CODEX_MODES, described here); an ACP agent's own ({id, label, desc})."""
        if agent.protocol == "claude":
            return MODES
        if agent.protocol == "codex":
            desc = {"plan": t("Read-only sandbox: Codex can look but not change anything"),
                    "default": t("Asks before running commands that aren't known to be safe"),
                    "acceptEdits": t("Edits and runs commands inside this folder; asks before going outside it"),
                    "auto": t("Codex's reviewer decides; asks only when something is risky")}
            return [{"id": m, "desc": desc[m]} for m in data.get("modes") or desc]
        return data.get("modes") or []

    @app.route("GET", "/api/claude")
    def claude_info(req: Request):
        """Models, effort levels, modes, commands and account, as the user's agent reports them
        (`?agent=codex`; the default is the agent new chats start on), and every agent with whether
        it is installed and signed in."""
        agent = agents.get((req.query.get("agent") or [default_agent()])[0])
        info = hub.info(agent.id)
        if req.query.get("refresh"):
            info.refresh()
        elif info.data is None and not info.loading and agent.installed():
            info.refresh_async(lambda: app.bus.publish("claude.info", {"agent": agent.id}))
        data = info.data or {}
        return {**info.get(), "agent": agent.id, "agents": hub.listing(), "available": agent.installed(),
                "limits": chats.limits.get(agent.id) or data.get("limits") or {}, "modes": mode_list(agent, data),
                "defaults": (prefs.load().get("chat_defaults") if prefs else None)}

    @app.route("PUT", "/api/chat-defaults")
    def set_chat_defaults(req: Request):
        """What a new chat starts with: the last agent / model / effort / mode picked in this app. Kept in
        the app's own prefs (not the page's storage, which a new port would lose) and never in the agent's
        settings: null for a field means "follow the agent's own setting"."""
        b = body(req)
        f = chat_fields({k: v for k, v in b.items() if k in ("model", "effort", "mode", "agent")})
        kept = ((prefs.load().get("chat_defaults") if prefs else None) or {}).get("agent")
        saved = {"model": f.get("model_choice"), "effort": f.get("effort"), "mode": f.get("mode"),
                 "agent": f.get("agent") if "agent" in b else kept}   # a model pick leaves the agent as it was
        if prefs:
            prefs.save(chat_defaults=saved)
        return {"defaults": saved}

    @app.route("GET", "/api/chats")
    def list_chats(req: Request):
        return {"chats": chats.store.list(), "running": chats.running(), "waiting": chats.waiting(),
                "available": chats.available(default_agent())}

    def chat_fields(b: dict, agent: str | None = None) -> dict:
        """`agent`: the chat's agent, whose own effort levels and modes are the valid ones."""
        fields = {}
        agent = b.get("agent") or agent or default_agent()
        if "model" in b:
            if not valid_model(b["model"]):
                raise HttpError(400, t("Unknown model: {name}", name=b["model"]))
            fields["model_choice"] = b["model"] or None
        if "effort" in b:
            if not valid_effort(b["effort"], agent):
                raise HttpError(400, t("Unknown effort level: {name}", name=b["effort"]))
            fields["effort"] = b["effort"] or None
        if "mode" in b:
            if not valid_mode(b["mode"], agent):
                raise HttpError(400, t("Unknown permission mode: {name}", name=b["mode"]))
            fields["mode"] = b["mode"] or None
        if "agent" in b:
            if not agents.valid(b["agent"]):
                raise HttpError(400, t("Unknown agent: {name}", name=b["agent"]))
            fields["agent"] = b["agent"] or None
        if "title" in b:
            title = " ".join(str(b["title"] or "").split())[:60]
            if not title:
                raise HttpError(400, t("The title can't be empty"))
            fields["title"] = title
        return fields

    @app.route("POST", "/api/chats")
    def new_chat(req: Request):
        return chats.store.create(**chat_fields(body(req)))

    @app.route("PUT", "/api/chats/{cid}")
    def update_chat(req: Request):
        cid = req.params["cid"]
        before = chats.store.meta(cid)
        if before is None:
            raise HttpError(404, t("Chat not found"))
        fields = chat_fields(body(req), before.get("agent"))
        if "agent" in fields and agents.get(fields["agent"]).id != agents.get(before.get("agent") or default_agent()).id \
                and (before.get("session_id") or before.get("fork") or chats.store.messages(cid)):
            # the conversation lives in the agent it ran on; another agent can't pick it up
            raise HttpError(409, t("This chat already talks to {name}; start a new chat for another agent",
                                   name=agents.get(before.get("agent")).name))
        if "agent" in fields and fields["agent"] != before.get("agent"):   # another agent's model / mode mean nothing here
            fields.update(model_choice=None, effort=None, mode=None)
        meta = chats.store.update_meta(cid, touch=False, **fields) if fields else before
        # a reply in progress follows the new model / mode too, like switching in Claude Code mid-turn
        chats.set_live(cid, model=fields["model_choice"] or "" if "model_choice" in fields else None,
                       mode=(fields["mode"] or info_mode(meta)) if "mode" in fields else None)
        return meta

    def info_mode(meta: dict) -> str:
        return str((hub.info(meta.get("agent") or default_agent()).data or {}).get("permission_mode") or "default")

    @app.route("GET", "/api/chats/{cid}")
    def get_chat(req: Request):
        meta = chats.store.meta(req.params["cid"])
        if meta is None:
            raise HttpError(404, t("Chat not found"))
        return {"chat": meta, "messages": chats.store.messages(meta["id"]), "running": chats.busy(meta["id"]),
                "live": chats.live(meta["id"]), "available": chats.available(meta.get("agent") or default_agent())}

    @app.route("DELETE", "/api/chats/{cid}")
    def delete_chat(req: Request):
        if chats.busy(req.params["cid"]):
            raise HttpError(409, t("This chat is still replying"))
        chats.store.delete(req.params["cid"])
        return {"chats": chats.store.list()}

    @app.route("POST", "/api/chats/{cid}/answer")
    def answer(req: Request):
        """The user's answer to a permission card, one of Claude's questions, or its plan."""
        b = body(req)
        try:
            return {"ask": chats.answer(req.params["cid"], str(b.get("request_id") or ""), b)}
        except KeyError:
            raise HttpError(409, t("This request has already ended"))

    def cut_point(cid: str, req: Request, verb: str) -> tuple[dict, list, int, dict | None]:
        """Where a fork / rewind starts: just before user message `index`, as the agent's resume point."""
        meta = chats.store.meta(cid)
        if meta is None:
            raise HttpError(404, t("Chat not found"))
        msgs = chats.store.messages(cid)
        try:
            index = int(body(req).get("index"))
        except (TypeError, ValueError):
            raise HttpError(400, t("index must be a number"))
        if not 0 <= index < len(msgs) or msgs[index].get("role") != "user":
            raise HttpError(400, t("You can only {action} from a message you sent", action=verb))
        before = msgs[:index]
        at = next((m.get("uuid") for m in reversed(before) if m.get("role") == "assistant"), None)
        if before and not at:
            raise HttpError(409, t("This chat was recorded by an older version, so it can't {action} from the middle; start from the first message", action=verb))
        agent = agents.get(meta.get("agent"))
        if before and not agent.fork:
            raise HttpError(409, t("{name} can't go back to the middle of a chat; you can only start again from the first message",
                                   name=agent.name))
        # a chat that was itself forked and has not replied since still points at its source session
        sid = meta.get("session_id") or (meta.get("fork") or {}).get("session_id")
        return meta, msgs, index, ({"session_id": sid, "at": at} if before and sid else None)

    @app.route("POST", "/api/chats/{cid}/fork")
    def fork(req: Request):
        """A new chat that continues from just before message `index` (that message can be rewritten)."""
        meta, msgs, index, point = cut_point(req.params["cid"], req, t("fork"))
        new = chats.store.create(title=t("{title} (fork)", title=meta.get("title") or t("Chat")), model_choice=meta.get("model_choice"),
                                 effort=meta.get("effort"), mode=meta.get("mode"), tasks=None, fork=point,
                                 workspace=meta.get("workspace"), agent=meta.get("agent"))
        chats.store.save_messages(new["id"], msgs[:index])
        return {"chat": new, "text": msgs[index].get("text") or "", "files": msgs[index].get("files") or []}

    @app.route("POST", "/api/chats/{cid}/rewind")
    def rewind(req: Request):
        """Go back to just before message `index` in this same chat; later messages are dropped.
        The agent continues from that point (Claude Code: `--fork-session --resume-session-at`, Codex: `thread/fork`)."""
        cid = req.params["cid"]
        if chats.busy(cid):
            raise HttpError(409, t("This chat is still replying; stop it before rewinding"))
        meta, msgs, index, point = cut_point(cid, req, t("rewind"))
        chats.store.save_messages(cid, msgs[:index])
        chat = chats.store.update_meta(cid, session_id=None, fork=point, tasks=None, context=None)
        return {"chat": chat, "text": msgs[index].get("text") or "", "files": msgs[index].get("files") or [],
                "dropped": len(msgs) - index}

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
            raise HttpError(404, t("No such attachment"))
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
                    raise HttpError(400, t("Can't read the attachment {name}", name=name))
                if len(raw) > 30 * 1024 * 1024:
                    raise HttpError(413, t("The attachment {name} is over 30 MB", name=name))
                path = chats.store.save_file(cid, name, raw)
            elif it.get("path"):
                path = Path(str(it["path"]))
                if not path.is_file():
                    raise HttpError(400, t("File not found: {path}", path=path))
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
            raise HttpError(404, t("Entry not found"))
        tracker.mark_synced(str(entry.get("id")), [str(p) for p in entry.get("evidence") or []])
        return {"status": tracker.status([entry], force=True)}

    def subset_entries(doc, subset) -> list:
        if subset is None:
            return []
        if model.is_all(subset):
            return list(doc.get("entries") or [])
        by_id = {str(e.get("id")): e for e in doc.get("entries") or []}
        return [by_id[str(i)] for i in subset.get("entries") or [] if str(i) in by_id]

    def long_bullets(subset_id: str, lang: str) -> list[dict]:
        with render_lock:
            r = studio.render(subset_id, lang=lang, images=False)
        if not r["ok"]:
            raise HttpError(409, t("Rendering failed; see the error above the preview first"))
        return studio.bullet_report(r)

    ACTIONS = ("polish", "translate", "fit", "sync", "mock", "probe", "answer", "batch_translate", "batch_fit", "batch_sync")
    ONE_ENTRY = ("polish", "translate", "fit", "sync", "mock", "probe", "answer")

    @app.route("POST", "/api/chats/{cid}/send")
    def send(req: Request):
        """A chat message: typed by the user, or one of the editor's buttons (`action`)."""
        b = body(req)
        cid = req.params["cid"]
        doc, _ = studio.store.load()
        data_path = str(studio.store.path)
        subset = model.get_subset(doc, b.get("subset_id")) if b.get("subset_id") else None
        vid = str((subset or {}).get("id") or model.ALL_ID)
        vlang = str((subset or {}).get("lang") or "en")
        langs = [str(l) for l in (doc.get("settings") or {}).get("languages") or ["en"]]
        entry = model.get_item(doc, "entry", b.get("entry_id")) if b.get("entry_id") else None
        action = "polish" if b.get("polish") else b.get("action")
        if action and action not in ACTIONS:
            raise HttpError(400, f"unknown action {action}")
        if action in ONE_ENTRY and entry is None:
            raise HttpError(404, t("Entry not found"))
        lang = str(b.get("lang") or vlang)
        name = lambda e: model.tr(e.get("title"), lang) or model.tr(e.get("title"), "en") or t("this entry")  # noqa: E731
        instruction = str(b.get("text") or "")
        involved = [entry] if entry is not None else []
        synced: list = []   # entries whose evidence baseline moves forward when the turn succeeds

        if action == "polish":
            text = prompts.polish_message(name(entry), instruction, lang, vid, data_path)
            synced = [entry]
        elif action == "translate":
            target = str(b.get("target") or "")
            if target not in langs:
                raise HttpError(400, t("{lang} isn't one of this file's resume languages", lang=target or "?"))
            text = prompts.translate_message(name(entry), target, [x for x in langs if x != target], vid, data_path, instruction)
        elif action == "fit":
            untranslated = set(model.missing_text(entry, lang).get("bullets", []))   # shown in the other language
            mine = [x for x in long_bullets(vid, lang) if x["id"] == str(entry.get("id")) and x["index"] not in untranslated]
            if not mine:
                raise HttpError(409, t("Every bullet of this entry already fits on one line in the current preview"))
            text = prompts.fit_message(name(entry), lang, vid, data_path, mine)
        elif action == "sync":
            st = tracker.status([entry], force=True).get(str(entry.get("id")))
            rows = [r for r in (st or {}).get("paths", []) if r["state"] in ("changed", "new")]
            if not rows:
                raise HttpError(409, t("This project hasn't changed since the last sync"))
            text = prompts.sync_message(name(entry), "\n".join(evidence.describe(r) if r["state"] == "changed"
                                                               else "- " + t("{path} (not read yet, please go through all of it)", path=r["path"])
                                                               for r in rows), langs, vid, data_path)
            synced = [entry]
        elif action == "mock":
            text = prompts.mock_message(name(entry), lang, instruction)
        elif action in ("probe", "answer"):
            path = b.get("path")
            q = model.interview_question(entry, path) if isinstance(path, list) else None
            if q is None:
                raise HttpError(404, t("That question isn't there any more"))
            msg = prompts.probe_message if action == "probe" else prompts.answer_message
            text = msg(name(entry), str(q.get("q") or "").strip(), model.interview_path(path), lang, instruction)
        elif action == "batch_translate":
            target = str(b.get("target") or "")
            if target not in langs:
                raise HttpError(400, t("{lang} isn't one of this file's resume languages", lang=target or "?"))
            involved = [e for e in subset_entries(doc, subset) if model.missing_text(e, target)]
            if not involved:
                raise HttpError(409, t("Every entry in the current subset already has {lang}", lang=prompts.lang_name(target)))
            text = prompts.batch_message("translate", subset, data_path, [name(e) for e in involved], target,
                                         [x for x in langs if x != target])
        elif action == "batch_fit":
            ids = []
            for x in long_bullets(vid, vlang):
                if x["id"] not in ids:
                    ids.append(x["id"])
            involved = [model.get_item(doc, "entry", i) for i in ids]
            if not involved:
                raise HttpError(409, t("Every bullet in the current preview already fits on one line"))
            text = prompts.batch_message("fit", subset, data_path, [name(e) for e in involved])
        elif action == "batch_sync":
            st = tracker.status(doc.get("entries") or [], force=True)
            involved = [e for e in doc.get("entries") or [] if (st.get(str(e.get("id"))) or {}).get("state") == "changed"]
            if not involved:
                raise HttpError(409, t("No project has changed since the last sync"))
            parts = []
            for e in involved:
                rows = [r for r in st[str(e.get("id"))]["paths"] if r["state"] == "changed"]
                parts.append(f"## {name(e)}\n" + "\n".join(evidence.describe(r) for r in rows))
            text = prompts.batch_sync_message(parts, langs, vid, data_path)
            synced = involved
        else:
            text = instruction.strip()
            if not text and not b.get("attachments"):
                raise HttpError(400, t("The message is empty"))

        add_dirs = []
        for e in involved:  # let Claude read the involved entries' evidence folders
            for p in e.get("evidence") or []:
                path = Path(str(p))
                add_dirs.append(str(path.parent if path.is_file() else path))
        roots = [str(p) for p in (doc.get("settings") or {}).get("project_roots") or []]
        # what the window shows (the workspace and the company / platform / page open there), one line
        screen = " ".join(str(b.get("screen") or "").split())[:300]
        context = prompts.ui_context(data_path, subset, entry, roots, screen)
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
            prompt += prompts.attachments_note([f["path"] for f in others])
        try:
            r = chats.send(cid, prompt, context=context, add_dirs=add_dirs, on_done=done if targets else None,
                           meta={"action": action, "polish": action == "polish", "entry_id": b.get("entry_id"),
                                 "entries": [str(e.get("id")) for e in involved]},
                           images=images, files=files, shown=text if action is None else None)
        except KeyError:
            raise HttpError(404, t("Chat not found"))
        except RuntimeError as e:
            raise HttpError(409, str(e))
        return {"ok": True, "text": text, "files": files, **r}

    @app.route("POST", "/api/chats/{cid}/cancel")
    def cancel(req: Request):
        chats.cancel(req.params["cid"])
        return {"ok": True}
