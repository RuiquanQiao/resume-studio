"""Derived data kept between starts: a value worked out from the user's files, saved with the key it was worked out
for and used again until that key changes (a persisted, invalidated cache).

The key names everything the value depends on (the files' modification times and sizes, the settings, the day), so
a saved value is never stale: any change gives another key and the value is worked out again, once, on its next
use. The file lives in the cache folder and can be deleted at any time; it is then worked out again.

Why (2026-11-13): each start worked out the job-hunt table's per-company counts from the 29 MB postings file again,
matching 86,000 role titles against the tracks: 9 s before the first table showed, although nothing had changed.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable


class Derived:
    """One value, in memory and in `path` (None: in memory only). Two callers at once work it out once."""

    def __init__(self, path: Path | None):
        self.path = path
        self._kept: tuple[str, Any] | None = None
        self._lock = threading.Lock()

    def get(self, key: str, work: Callable[[], Any]) -> Any:
        """The value for this key: the one in hand, else the saved one, else `work()` (then saved). Never changed by
        a caller: what is handed out is shared."""
        with self._lock:
            if self._kept and self._kept[0] == key:
                return self._kept[1]
            saved = self._read()
            if isinstance(saved, dict) and saved.get("key") == key and "data" in saved:
                self._kept = (key, saved["data"])
                return saved["data"]
            data = work()
            # what is kept is what a later start reads back, so a value is the same whichever way it was had
            text = json.dumps({"key": key, "data": data}, ensure_ascii=False, separators=(",", ":"))
            data = json.loads(text)["data"]
            self._kept = (key, data)
            self._write(text)
            return data

    def _read(self) -> Any:
        if not self.path:
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, text: str) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:   # kept in memory all the same; the next start works it out again
            pass


def stat_key(path: Path) -> list[int]:
    """A file's modification time and size, for a key: [0, 0] while there is none."""
    try:
        s = path.stat()
        return [s.st_mtime_ns, s.st_size]
    except OSError:
        return [0, 0]


@functools.cache
def code_key(*modules: str) -> str:
    """A fingerprint of the code that works a value out (these modules' source), for its key: a new version of the
    app that counts differently never takes a value the old one saved."""
    h = hashlib.blake2b(digest_size=8)
    for name in modules:
        f = getattr(sys.modules.get(name), "__file__", None)
        try:
            h.update(Path(f).read_bytes() if f else name.encode())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()
