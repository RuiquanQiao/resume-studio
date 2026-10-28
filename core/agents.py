"""The coding agents a chat can run on, as the user has them installed and signed in.

Resume Studio never signs anyone in and never keeps a key: each agent is the user's own
command line tool, run the way its own SDK or editor integration runs it, with the login
that tool keeps for itself (`~/.codex/auth.json`, Claude Code's keychain entry, Pi's
`auth.json`…). This module knows, for each agent:
  - how to find the command (`STUDIO_<ID>_CMD`, a JSON list, overrides it for tests);
  - whether it looks signed in, from the files and variables that tool itself reads
    (a guess only where the tool has no way to ask; the first reply settles it);
  - what to tell the user when it is missing or signed out;
  - which protocol drives it (see core/drivers.py):
      claude  Claude Code's stream-json SDK channel (core/chat.py)
      codex   `codex app-server`, Codex's JSON-RPC interface (the one its IDE extension uses)
      pi      `pi --mode rpc`
      acp     the Agent Client Protocol (agentclientprotocol.com), an open standard that
              Qwen Code, DeepSeek Harness and many others speak over stdio
`AgentHub` holds what each agent reports about itself (models, effort levels, modes,
account), refreshed in the background and cached next to the data file like
`claude-info.json` always was.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .i18n import t

HOME = Path.home()


def _t(text: str) -> str:
    """Marks a string for the catalogs (scripts/i18n_keys.py); t() translates it where it is shown."""
    return text


@dataclass
class Agent:
    id: str
    name: str            # "Claude Code": menus, messages
    short: str           # "Claude": the top bar's button, "Claude is replying"
    exe: str             # the command on PATH
    protocol: str        # claude | codex | pi | acp
    args: list[str] = field(default_factory=list)   # what puts the command in its machine mode
    install: str = ""    # the command that installs it
    login: str = ""      # how to sign in (markdown, English, goes through t())
    fork: bool = False   # a chat can go on from the middle (fork / rewind at a message)
    images: bool = True

    def command(self) -> list[str] | None:
        override = os.environ.get(f"STUDIO_{self.id.upper()}_CMD")
        if override:
            return list(json.loads(override))
        exe = shutil.which(self.exe)
        return [exe] if exe else None

    def installed(self) -> bool:
        return self.command() is not None

    def signed_in(self) -> bool | None:
        """From the files the tool keeps its own login in. None: can't tell without asking it."""
        if os.environ.get(f"STUDIO_{self.id.upper()}_CMD"):
            return True
        check = _SIGNED_IN.get(self.id)
        return check() if check else None


def _env(*names: str) -> bool:
    return any(os.environ.get(n) for n in names)


def _codex_signed_in() -> bool:
    home = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
    return (home / "auth.json").is_file() or _env("OPENAI_API_KEY", "CODEX_API_KEY")


def _qwen_signed_in() -> bool:
    try:
        s = json.loads((HOME / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    picked = ((s.get("security") or {}).get("auth") or {}).get("selectedType")
    return bool(picked) or (HOME / ".qwen" / "oauth_creds.json").is_file()


def _pi_signed_in() -> bool:
    folder = Path(os.environ.get("PI_CODING_AGENT_DIR") or HOME / ".pi" / "agent")
    return (folder / "auth.json").is_file() or _env("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                                                     "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY")


def _dsh_signed_in() -> bool:
    if _env("DEEPSEEK_API_KEY"):
        return True
    home = Path(os.environ.get("DSH_HOME") or HOME / ".dsh")
    try:
        return "DEEPSEEK_API_KEY" in (home / ".env").read_text(encoding="utf-8")
    except OSError:
        return False


_SIGNED_IN: dict[str, Callable[[], bool]] = {
    "codex": _codex_signed_in, "qwen": _qwen_signed_in, "pi": _pi_signed_in, "dsh": _dsh_signed_in,
}

AGENTS: list[Agent] = [
    Agent("claude", "Claude Code", "Claude", "claude", "claude", install="npm install -g @anthropic-ai/claude-code",
          login=_t("Open a terminal, run `claude`, type `/login` and finish signing in in the browser."), fork=True),
    Agent("codex", "Codex", "Codex", "codex", "codex", args=["app-server"], install="npm install -g @openai/codex",
          login=_t("Open a terminal and run `codex login` (a ChatGPT account or an API key)."), fork=True),
    Agent("qwen", "Qwen Code", "Qwen", "qwen", "acp", args=["--acp"], install="npm install -g @qwen-code/qwen-code",
          login=_t("Open a terminal, run `qwen` and pick how to sign in (`/auth`).")),
    Agent("pi", "Pi", "Pi", "pi", "pi", args=["--mode", "rpc"], install="npm install -g @earendil-works/pi-coding-agent",
          login=_t("Open a terminal, run `pi` and type `/login` (a subscription or an API key).")),
    Agent("dsh", "DeepSeek Harness", "DeepSeek", "dsh", "acp", args=["--profile", "acp"], install="npm install -g @deepseek-ai/dsh",
          login=_t("Set `DEEPSEEK_API_KEY` in your environment, or put it in the `.env` file in DeepSeek Harness's home folder (`DSH_HOME`).")),
]
BY_ID = {a.id: a for a in AGENTS}
DEFAULT = "claude"


def get(agent_id: Any) -> Agent:
    return BY_ID.get(str(agent_id or DEFAULT)) or BY_ID[DEFAULT]


def valid(agent_id: Any) -> bool:
    return agent_id is None or agent_id in BY_ID


def public(agent: Agent) -> dict:
    return {"id": agent.id, "name": agent.name, "short": agent.short, "exe": agent.exe, "installed": agent.installed(),
            "signed_in": agent.signed_in(), "install": agent.install, "login": t(agent.login) if agent.login else "",
            "fork": agent.fork, "protocol": agent.protocol}


class AgentInfo:
    """What one agent reports about itself (models, effort levels, modes, commands, account).

    `fetch(prefix, cwd, timeout)` asks the agent (no model call, so it costs nothing) and returns
    the shape the UI reads (see claude_info.normalize). Kept in `cache` so the UI has it at once
    on the next start. `learn()` adds what only shows up in a conversation (an ACP agent's
    models and modes come with its first session)."""

    def __init__(self, agent: Agent, fetch: Callable[[list[str], Path, float], dict], cwd: Path, cache: Path):
        self.agent = agent
        self.fetch = fetch
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

    # the same interface the chat code has always used for Claude Code
    def command(self) -> list[str] | None:
        return self.agent.command()

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
                prefix = self.agent.command()
                if not prefix:
                    self.error = t("The {cmd} command was not found", cmd=self.agent.exe)
                    return self.get()
                fresh = self.fetch(prefix, self.cwd, timeout)
                learned = {k: v for k, v in (self.data or {}).items() if k in ("models", "modes", "commands", "default_model")
                           and v and not fresh.get(k)}
                self.data, self.error, self.fetched = {**fresh, **learned}, None, time.time()
                self._save()
            except Exception as e:  # noqa: BLE001 - the UI falls back to the cached / built-in list
                self.error = t("Couldn't read {name}'s info: {error}", name=self.agent.name, error=e)
            finally:
                self.loading = False
            return self.get()

    def learn(self, **fields: Any) -> bool:
        """Merge what a conversation revealed; True when something changed."""
        with self.lock:
            data = dict(self.data or empty_info(self.agent))
            changed = {k: v for k, v in fields.items() if v is not None and data.get(k) != v}
            if not changed:
                return False
            data.update(changed)
            self.data = data
            self._save()
            return True

    def _save(self) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps({"data": self.data, "fetched": self.fetched}, ensure_ascii=False), encoding="utf-8")


def empty_info(agent: Agent) -> dict:
    return {"models": [], "more_models": [], "default_model": {"resolved": "", "name": t("{name}'s default", name=agent.name)},
            "efforts": [], "commands": [], "agents": [], "output_style": None, "output_styles": [],
            "permission_mode": None, "modes": [], "account": {}, "signed_in": None}


class AgentHub:
    """Every agent with its info. One per data file (the caches live in its .studio folder)."""

    def __init__(self, cwd: Path, state_dir: Path, fetchers: dict[str, Callable] | None = None):
        from . import drivers   # drivers imports this module
        self.cwd = cwd
        fetchers = fetchers or {}
        self.infos: dict[str, AgentInfo] = {}
        for a in AGENTS:
            name = "claude-info.json" if a.id == "claude" else f"agent-{a.id}.json"
            self.infos[a.id] = AgentInfo(a, fetchers.get(a.id) or drivers.fetcher(a), cwd, state_dir / name)

    def info(self, agent_id: Any) -> AgentInfo:
        return self.infos[get(agent_id).id]

    def listing(self) -> list[dict]:
        out = []
        for a in AGENTS:
            row = public(a)
            data = self.infos[a.id].data or {}
            if data.get("signed_in") is not None and row["installed"]:
                row["signed_in"] = data["signed_in"]   # what the agent itself said beats the file check
            if (data.get("account") or {}).get("email"):
                row["account"] = data["account"]["email"]
            out.append(row)
        return out
