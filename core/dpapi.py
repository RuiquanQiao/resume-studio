"""Bytes only this Windows user can read back: DPAPI (CryptProtectData), as Chrome keeps its own cookies.

Used for what must not lie on disk in the clear (the session cookies Resume Studio keeps across a browser
restart, core/browser.py). Elsewhere than Windows there is no DPAPI: the bytes are kept as they are, in a file
only the user may read (the profile folder's own protection, as Chrome on Linux without a keyring).
"""
from __future__ import annotations

import os

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _UI_FORBIDDEN = 0x1   # CRYPTPROTECT_UI_FORBIDDEN: never a prompt

    def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _take(out: _Blob) -> bytes:
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    def protect(data: bytes) -> bytes:
        src, _keep = _blob(data)
        out = _Blob()
        if not _crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError("CryptProtectData failed")
        return b"DPAPI" + _take(out)

    def unprotect(data: bytes) -> bytes:
        if not data.startswith(b"DPAPI"):
            raise ValueError("not DPAPI data")
        src, _keep = _blob(data[5:])
        out = _Blob()
        if not _crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError("CryptUnprotectData failed (another user's, or another computer's)")
        return _take(out)
else:
    def protect(data: bytes) -> bytes:
        return b"PLAIN" + data

    def unprotect(data: bytes) -> bytes:
        if not data.startswith(b"PLAIN"):
            raise ValueError("not data of this kind")
        return data[5:]
