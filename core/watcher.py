"""Poll the data file and report changes, whoever made them.

Polling (not OS notifications) keeps this dependency-free and reliable on Windows,
where editors and agents often replace files instead of writing in place.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class FileWatcher:
    def __init__(self, path: Path, digest: Callable[[str], str],
                 on_change: Callable[[str], None], interval: float = 0.5):
        self.path = path
        self.digest = digest
        self.on_change = on_change
        self.interval = interval
        self._last_stat: tuple[int, int] | None = None
        self._last_digest: str | None = None
        self._stop = threading.Event()

    def _check(self) -> None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return
        key = (st.st_mtime_ns, st.st_size)
        if key == self._last_stat:
            return
        self._last_stat = key
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return  # mid-write; next tick will see the finished file
        d = self.digest(text)
        if d != self._last_digest:
            first = self._last_digest is None
            self._last_digest = d
            if not first:
                self.on_change(d)

    def start(self) -> None:
        self._check()

        def loop() -> None:
            while not self._stop.wait(self.interval):
                self._check()

        threading.Thread(target=loop, name="file-watcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


class SignatureWatcher:
    """Poll any cheap signature (e.g. a folder's file names, sizes and times) and report when it
    changes. For data another program writes, where a whole folder is the thing to watch."""

    def __init__(self, signature: Callable[[], object], on_change: Callable[[], None], interval: float = 1.5):
        self.signature = signature
        self.on_change = on_change
        self.interval = interval
        self._last: object = None
        self._stop = threading.Event()

    def _check(self, first: bool = False) -> None:
        try:
            sig = self.signature()
        except OSError:
            return
        if sig != self._last:
            self._last = sig
            if not first:
                self.on_change()

    def start(self) -> None:
        self._check(first=True)

        def loop() -> None:
            while not self._stop.wait(self.interval):
                self._check()

        threading.Thread(target=loop, name="signature-watcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
