"""A small Chrome DevTools Protocol client on the standard library.

Chrome (or Edge) started with `--remote-debugging-port` speaks CDP over a WebSocket. This is the
same channel Playwright and Puppeteer use; we need only a handful of commands, so a ~200-line
client keeps the core layer dependency-free (see core/server.py).

One connection goes to the browser target; pages are reached through flattened sessions
(`Target.attachToTarget` with `flatten: true`), so every command and event names its `sessionId`.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse


class CdpError(Exception):
    """A command failed, or the connection is gone."""


def version_info(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> dict:
    """GET /json/version: the browser's name and its WebSocket address."""
    with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class WebSocket:
    """Client side of RFC 6455: masked frames out, text/continuation/ping/close in."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        # no Origin header: Chrome refuses WebSocket clients whose Origin it wasn't told to allow
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("the browser closed the connection during the handshake")
            head += chunk
        status, _, rest = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise CdpError("WebSocket handshake refused: " + status.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = rest
        self.sock.settimeout(None)
        self.wlock = threading.Lock()

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise CdpError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        with self.wlock:
            self.sock.sendall(head + mask + _mask(payload, mask))

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self) -> str:
        """The next whole text message (pings are answered on the way)."""
        parts: list[bytes] = []
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else b""
            data = self._read(n)
            if mask:
                data = _mask(data, mask)
            if opcode == 0x8:
                raise CdpError("connection closed by the browser")
            if opcode == 0x9:
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _mask(data: bytes, mask: bytes) -> bytes:
    """XOR with the 4-byte mask, fast enough for screenshots (a few MB)."""
    n = len(data)
    key = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(data, "big") ^ key).to_bytes(n, "big") if n else b""


class Connection:
    """Commands with ids and their answers, plus events for whoever listens."""

    def __init__(self, ws_url: str):
        self.ws = WebSocket(ws_url)
        self.next_id = 0
        self.lock = threading.Lock()
        self.pending: dict[int, dict] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self.closed = False
        threading.Thread(target=self._loop, name="cdp", daemon=True).start()

    def _loop(self) -> None:
        try:
            while True:
                msg = json.loads(self.ws.recv())
                if "id" in msg:
                    slot = self.pending.get(msg["id"])
                    if slot is not None:
                        slot["msg"] = msg
                        slot["ev"].set()
                    continue
                for fn in list(self.listeners):
                    try:
                        fn(msg)
                    except Exception:  # noqa: BLE001 - a listener must not stop the reader
                        pass
        except (CdpError, OSError, ValueError):
            pass
        finally:
            self.closed = True
            for slot in list(self.pending.values()):
                slot["ev"].set()

    def send(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 30.0) -> dict:
        if self.closed:
            raise CdpError("the browser is not connected")
        with self.lock:
            self.next_id += 1
            cid = self.next_id
        slot = {"ev": threading.Event(), "msg": None}
        self.pending[cid] = slot
        msg: dict[str, Any] = {"id": cid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            self.ws.send(json.dumps(msg))
            if not slot["ev"].wait(timeout):
                raise CdpError(f"{method} timed out")
        except OSError as e:
            raise CdpError(f"{method}: {e}") from e
        finally:
            self.pending.pop(cid, None)
        reply = slot["msg"]
        if reply is None:
            raise CdpError("the browser closed the connection")
        if "error" in reply:
            raise CdpError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result") or {}

    def close(self) -> None:
        self.closed = True
        self.ws.close()
