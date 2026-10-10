"""Interface language: what the window speaks, separate from what a resume is written in.

The catalogs live in web/i18n/<lang>.json and are shared with the page. Keys are the English
text itself (English needs no catalog); a value is a string, or [one, other] for a count.
The language follows the computer unless the user picks one; that choice is a per-machine
preference kept next to the chats, not in resume.yaml.
"""
from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

WEB_I18N = Path(__file__).resolve().parent.parent / "web" / "i18n"

# the interface languages, by their own names
UI_LANGS = {"en": "English", "zh": "简体中文", "ja": "日本語", "ko": "한국어",
            "es": "Español", "fr": "Français", "de": "Deutsch"}

_lock = threading.Lock()
_catalogs: dict[str, dict] = {}
_current = {"lang": "en"}


def normalize(tag: str | None) -> str | None:
    """'zh-Hans-CN', 'zh_CN.UTF-8', 'de-AT' -> a UI language we have, or None."""
    if not tag:
        return None
    base = str(tag).replace("_", "-").split(".")[0].split("-")[0].lower()
    return base if base in UI_LANGS else None


def _windows_locale() -> str | None:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(85)
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        lcid = k32.GetUserDefaultUILanguage()
        if k32.LCIDToLocaleName(lcid, buf, 85, 0):
            return buf.value
    except (OSError, AttributeError):
        pass
    return None


def _mac_locale() -> str | None:
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            line = line.strip().strip(",").strip('"')
            if line and line not in "()":
                return line
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def system_locale() -> str:
    """The computer's language tag (e.g. 'zh-CN', 'de-DE'); 'en' when nothing says otherwise.
    RESUME_STUDIO_LANG overrides it (tests, or anyone who wants a fixed language)."""
    forced = os.environ.get("RESUME_STUDIO_LANG")
    if forced:
        return forced
    tag = _windows_locale() if sys.platform.startswith("win") else _mac_locale() if sys.platform == "darwin" else None
    if not tag:
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            if os.environ.get(var) and os.environ[var] not in ("C", "POSIX"):
                tag = os.environ[var]
                break
    if not tag:
        try:
            tag = locale.getlocale()[0]
        except ValueError:
            tag = None
    return (tag or "en").replace("_", "-").split(".")[0]


def system_lang() -> str:
    """The interface language the computer asks for; English when we don't have it."""
    return normalize(system_locale()) or "en"


def catalog(lang: str) -> dict:
    with _lock:
        if lang not in _catalogs:
            path = WEB_I18N / f"{lang}.json"
            try:
                _catalogs[lang] = json.loads(path.read_text(encoding="utf-8")) if lang != "en" else {}
            except (OSError, ValueError):
                _catalogs[lang] = {}
        return _catalogs[lang]


def set_lang(lang: str) -> None:
    _current["lang"] = lang if lang in UI_LANGS else "en"


def current() -> str:
    return _current["lang"]


def _pick(value: Any, n: Any, lang: str) -> str:
    if isinstance(value, list):
        one = n == 1 or (lang == "fr" and n in (0, 1))
        return str(value[0] if one or len(value) == 1 else value[1])
    return str(value)


def t(key: str, other: str | None = None, *, ui: str | None = None, **vars: Any) -> str:
    """Translate English `key` into the interface language (or `ui`); `other` is the English
    plural, chosen by vars['n']. Every other keyword fills a {placeholder}."""
    lang = ui or _current["lang"]
    n = vars.get("n")
    hit = catalog(lang).get(key) if lang != "en" else None
    if hit is not None:
        text = _pick(hit, n, lang)
    else:
        text = other if other is not None and n != 1 else key
    for k, v in vars.items():
        text = text.replace("{" + k + "}", str(v))
    return text


class Prefs:
    """This computer's app preferences (interface language, which data file to open), kept in
    `folder`/prefs.json: they belong to the app, not to any one data file."""

    def __init__(self, folder: Path):
        self.path = Path(folder) / "prefs.json"

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, **fields: Any) -> dict:
        data = {**self.load(), **fields}
        data = {k: v for k, v in data.items() if v is not None}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def ui_lang(self) -> str:
        """The chosen language, or the computer's when the user hasn't picked one."""
        chosen = self.load().get("ui_lang")
        return chosen if chosen in UI_LANGS else system_lang()
