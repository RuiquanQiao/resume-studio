"""One line appended to a log several fake agent processes share (tests/fake_agents.py, tests/fake_claude.py).

Windows has no atomic append: the C runtime seeks to the end, then writes, so two processes writing at once
overwrote each other's lines and a test read half a line now and then (2026-11-04). Each line is written under a
lock on the file's first byte.
"""
from __future__ import annotations

import json
import os


def append_line(path: str, obj: object) -> None:
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                os.lseek(fd, 0, os.SEEK_END)
                os.write(fd, data)
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, data)
    finally:
        os.close(fd)
