"""What the user's Claude Code offers: models, effort levels, slash commands, account.

Claude Code answers an `initialize` control request on its stream-json channel with the
same lists its own UI shows (models with their supported effort levels, every slash
command and skill, output styles, the account). No model is called, so this costs nothing.
The answer is cached in `.studio/claude-info.json` so the UI has it at once on the next start.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .i18n import t

EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Claude Code's own model menu (desktop app) lists these; the CLI's aliases can lag behind
# (`sonnet` may still mean Sonnet 5), so the newest ids come first and the CLI's own list
# fills "More models". Descriptions stay English; the page translates them. Update this table when a new model ships; nothing else depends on it.
LATEST = [
    {"value": "claude-opus-5-5", "name": "Opus 5.5", "desc": "Best for everyday, complex tasks", "effort": True},
    {"value": "claude-sonnet-5-5", "name": "Sonnet 5.5", "desc": "Efficient for routine tasks", "effort": True},
    {"value": "claude-fable-5-1", "name": "Fable 5.1", "desc": "Most capable for your hardest and longest-running tasks", "effort": True, "tag": "Requires usage credits"},
    {"value": "claude-haiku-4-5-20251001", "name": "Haiku 4.5", "desc": "Fastest for quick answers", "effort": False},
]

CREDITS = "Requires usage credits"

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,80}$")


def valid_model_id(value: Any) -> bool:
    """Passed to the CLI as one argv item; `claude` may be a .cmd shim, so keep it shell-inert."""
    return isinstance(value, str) and bool(_MODEL_ID.match(value))


def model_name(model_id: str | None) -> str:
    """claude-opus-5-5 -> Opus 5.5, claude-haiku-4-5-20251001 -> Haiku 4.5, sonnet -> Sonnet."""
    if not model_id:
        return ""
    s = str(model_id)
    one_m = s.endswith("[1m]")
    s = s.removesuffix("[1m]")
    m = re.match(r"^(?:claude-)?([a-z]+)((?:-\d+)*)(?:-\d{8})?$", s)
    if not m:
        return str(model_id)
    family = m.group(1).capitalize()
    nums = [n for n in m.group(2).split("-") if n]
    if nums and len(nums[-1]) == 8:   # a date suffix
        nums = nums[:-1]
    name = family + (" " + ".".join(nums) if nums else "")
    return name + (" · 1M context" if one_m else "")


def normalize(raw: dict) -> dict:
    """The initialize answer, shaped for the UI."""
    cli_models = raw.get("models") or []
    default = next((m for m in cli_models if m.get("value") == "default"), None)
    by_resolved: dict[str, dict] = {}
    for m in cli_models:
        if m.get("value") != "default" and m.get("resolvedModel"):
            by_resolved.setdefault(str(m["resolvedModel"]), m)

    main: list[dict] = []
    for x in LATEST:
        item = dict(x)
        cli = by_resolved.pop(x["value"], None)
        if cli:   # the CLI knows this model: use its value (it may carry a context suffix) and effort list
            item["value"] = cli["value"]
            item["efforts"] = cli.get("supportedEffortLevels") or (EFFORTS if cli.get("supportsEffort") else [])
            item["auto_mode"] = bool(cli.get("supportsAutoMode"))
        else:
            item["efforts"] = EFFORTS if x["effort"] else []
            item["auto_mode"] = x["effort"]
        item["resolved"] = x["value"]
        item.pop("effort", None)
        main.append(item)

    more: list[dict] = []
    for resolved, m in by_resolved.items():
        name, *parts = [p.strip() for p in str(m.get("description") or "").split(" · ")]
        more.append({"value": m["value"], "name": name or model_name(resolved), "resolved": resolved,
                     "desc": ", ".join(p for p in parts if p != CREDITS) or f"Claude Code alias {m['value']}",
                     "tag": CREDITS if CREDITS in parts else None,
                     "efforts": m.get("supportedEffortLevels") or (EFFORTS if m.get("supportsEffort") else []),
                     "auto_mode": bool(m.get("supportsAutoMode"))})

    default_resolved = str((default or {}).get("resolvedModel") or "")
    commands = []
    for c in raw.get("commands") or []:
        desc = str(c.get("description") or "")
        source = ""
        hit = re.search(r"\s\((user|project|plugin|bundled|managed)\)$", desc)
        if hit:
            source, desc = hit.group(1), desc[: hit.start()]
        commands.append({"name": c.get("name"), "desc": desc, "hint": c.get("argumentHint") or "",
                         "aliases": c.get("aliases") or [], "source": source})
    account = raw.get("account") or {}
    return {
        "models": main, "more_models": more,
        "default_model": {"resolved": default_resolved, "name": model_name(default_resolved) or "Claude Code default"},
        "efforts": EFFORTS,
        "commands": commands,
        "agents": [{"name": a.get("name"), "desc": a.get("description", "")} for a in raw.get("agents") or []],
        "output_style": raw.get("output_style"),
        "output_styles": raw.get("available_output_styles") or [],
        "permission_mode": raw.get("current_permission_mode") or "default",
        "account": {"email": account.get("email"), "plan": account.get("subscriptionType"),
                    "provider": account.get("apiProvider")},
    }


class ClaudeInfo:
    def __init__(self, command, cwd: Path, cache: Path):
        self.command = command          # callable -> argv prefix or None
        self.cwd = cwd
        self.cache = cache
        self.lock = threading.Lock()
        self.data: dict | None = None
        self.error: str | None = None
        self.fetched = 0.0
        self.loading = False
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.data, self.fetched = saved["data"], float(saved.get("fetched") or 0)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def get(self) -> dict:
        return {"info": self.data, "error": self.error, "fetched": self.fetched, "loading": self.loading}

    def refresh_async(self, on_done=None) -> None:
        if self.loading:
            return
        self.loading = True

        def run():
            try:
                self.refresh()
            finally:
                if on_done:
                    on_done()
        threading.Thread(target=run, daemon=True).start()

    def refresh(self, timeout: float = 20) -> dict:
        with self.lock:
            self.loading = True
            try:
                prefix = self.command()
                if not prefix:
                    self.error = t("The claude command was not found")
                    return self.get()
                raw = probe(prefix, self.cwd, timeout)
                self.data, self.error, self.fetched = normalize(raw), None, time.time()
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps({"data": self.data, "fetched": self.fetched}, ensure_ascii=False),
                                      encoding="utf-8")
            except Exception as e:  # noqa: BLE001 - the UI falls back to the cached / built-in list
                self.error = t("Couldn't read Claude Code's info: {error}", error=e)
            finally:
                self.loading = False
            return self.get()


def probe(prefix: list[str], cwd: Path, timeout: float) -> dict:
    proc = subprocess.Popen(prefix + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
                            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert proc.stdin and proc.stdout
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        req = {"type": "control_request", "request_id": "studio-init", "request": {"subtype": "initialize"}}
        proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        proc.stdin.flush()
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            resp = msg.get("response") or {}
            if msg.get("type") == "control_response" and resp.get("request_id") == "studio-init":
                if resp.get("subtype") != "success":
                    raise RuntimeError(resp.get("error") or "initialize failed")
                return resp.get("response") or {}
        raise RuntimeError(t("Claude Code didn't answer"))
    finally:
        timer.cancel()
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
