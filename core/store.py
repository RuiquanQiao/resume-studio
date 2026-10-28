"""Round-trip YAML store shared by the UI and Claude Code.

The file on disk is the single source of truth. Every read goes to disk, so edits
made outside the UI (by Claude Code or by hand) are never lost. Writes are atomic
and the previous version is kept under .studio/history/ as a local safety net
between git commits.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import io
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def to_plain(node: Any) -> Any:
    """Convert ruamel nodes to JSON-safe plain Python values."""
    if isinstance(node, dict):
        return {str(k): to_plain(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [to_plain(v) for v in node]
    if isinstance(node, (_dt.date, _dt.datetime)):
        return node.isoformat()
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return str(node)


def fingerprint(value: Any) -> str:
    """Stable short hash of a plain value, used for optimistic concurrency."""
    raw = json.dumps(to_plain(value), sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def merge_into(target: Any, new: Any) -> Any:
    """Update a ruamel container in place so untouched keys keep their comments.

    Returns the value that should be stored (the same container when merged).
    """
    if isinstance(target, dict) and isinstance(new, dict):
        for key in [k for k in target.keys() if k not in new]:
            del target[key]
        for key, value in new.items():
            if key in target:
                target[key] = merge_into(target[key], value)
            else:
                target[key] = value
        return target
    if isinstance(target, list) and isinstance(new, list):
        # update in place, position by position: comments attached to items survive
        for i, value in enumerate(new):
            if i >= len(target):
                target.append(value)
            elif isinstance(target[i], dict) and isinstance(value, dict):
                merge_into(target[i], value)
            elif target[i] != value:
                target[i] = value
        while len(target) > len(new):
            del target[-1]
        return target
    return new


class YamlStore:
    def __init__(self, path: str | os.PathLike, history_keep: int = 50, upgrade=None):
        self.path = Path(path).resolve()
        self.upgrade = upgrade  # brings an older document up to date in memory, on every read
        self.state_dir = self.path.parent / ".studio"
        self.history_dir = self.state_dir / "history"
        self.history_keep = history_keep
        self.lock = threading.RLock()
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._yaml.width = 4096
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        self._yaml.allow_unicode = True
        self._parsed: tuple[str, Any] | None = None   # (digest, document) of the text parsed last; never handed out

    # ---- reading -------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @staticmethod
    def digest_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

    def load(self) -> tuple[Any, str]:
        """Return (round-trip document, digest of the file text).

        The text is always read from disk; only parsing it is skipped when it is the text parsed
        last time (most requests read an unchanged file, and a round-trip parse is the slow part).
        Callers get their own copy, free to change and save."""
        with self.lock:
            text = self.read_text()
            digest = self.digest_text(text)
            if self._parsed is None or self._parsed[0] != digest:
                self._parsed = (digest, self.parse(text))
            return copy.deepcopy(self._parsed[1]), digest

    def parse(self, text: str) -> Any:
        doc = self._yaml.load(text)
        if self.upgrade:
            self.upgrade(doc)
        return doc

    def dump(self, doc: Any) -> str:
        buf = io.StringIO()
        self._yaml.dump(doc, buf)
        return buf.getvalue()

    # ---- writing -------------------------------------------------------
    def save(self, doc: Any) -> str:
        with self.lock:
            text = self.dump(doc)
            self._snapshot()
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            self._replace(tmp)
            return self.digest_text(text)

    def write_text(self, text: str) -> str:
        with self.lock:
            self._snapshot()
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            self._replace(tmp)
            return self.digest_text(text)

    def _replace(self, tmp: Path) -> None:
        """Move the new text into place. On Windows a reader that has the file open for a moment (the
        window's watcher, an editor, a virus scanner) makes the swap fail, so it is retried briefly."""
        for wait in (0.02, 0.05, 0.1, 0.2, 0.4):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(wait)
        os.replace(tmp, self.path)

    def _snapshot(self) -> None:
        if not self.path.exists():
            return
        self.history_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        (self.history_dir / f"{stamp}.yaml").write_bytes(self.path.read_bytes())
        snaps = sorted(self.history_dir.glob("*.yaml"))
        for old in snaps[: max(0, len(snaps) - self.history_keep)]:
            try:
                old.unlink()
            except OSError:
                pass
