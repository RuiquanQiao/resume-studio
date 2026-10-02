"""Build Resume Studio.exe and assets/icon.ico (Windows only).

    python scripts/build_launcher.py

Compiles launcher/Launcher.cs with the C# compiler that ships with Windows
(.NET Framework 4, C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe), so
nothing has to be installed. The icon is drawn here with the standard library and
matches the favicon in web/index.html. Both outputs are committed; rerun after
changing either source.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "icon.ico"
EXE = ROOT / "Resume Studio.exe"
SOURCE = ROOT / "launcher" / "Launcher.cs"
SIZES = (16, 24, 32, 48, 64, 128, 256)

# favicon geometry on a 32-unit grid: (x, y, w, h, radius, colour)
TOP, BOTTOM = (0x3D, 0x5A, 0xFE), (0x8B, 0x5C, 0xF6)
SHAPES = [
    (9, 7, 14, 18, 2, (255, 255, 255)),
    (12, 11, 8, 2, 1, (0x3D, 0x5A, 0xFE)),
    (12, 15, 8, 1.5, 0.75, (0xA5, 0xB4, 0xFC)),
    (12, 18.5, 6, 1.5, 0.75, (0xA5, 0xB4, 0xFC)),
]


def inside(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> bool:
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def draw(size: int) -> bytes:
    ss = 4  # supersampling per axis
    rows = []
    for j in range(size):
        row = bytearray([0])  # PNG filter: none
        for i in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sj in range(ss):
                for si in range(ss):
                    u = (i + (si + 0.5) / ss) * 32 / size
                    v = (j + (sj + 0.5) / ss) * 32 / size
                    if not inside(u, v, 0, 0, 32, 32, 8):
                        continue
                    t = (u + v) / 64
                    colour = tuple(a + (b - a) * t for a, b in zip(TOP, BOTTOM))
                    for x, y, w, h, r, c in SHAPES:
                        if inside(u, v, x, y, w, h, r):
                            colour = c
                    for k in range(3):
                        acc[k] += colour[k]
                    acc[3] += 1
            n = ss * ss
            if acc[3]:
                row += bytes(round(acc[k] / acc[3]) for k in range(3)) + bytes([round(255 * acc[3] / n)])
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def write_icon() -> None:
    images = [draw(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    ICON.parent.mkdir(parents=True, exist_ok=True)
    ICON.write_bytes(out + b"".join(images))
    print(f"wrote {ICON.relative_to(ROOT)}")


def find_csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for fw in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if csc.exists():
            return csc
    sys.exit("找不到 .NET Framework 4 的 csc.exe（Windows 自带）。")


def build_exe() -> None:
    cmd = [str(find_csc()), "/nologo", "/target:winexe", "/optimize+", f"/win32icon:{ICON}",
           "/r:System.Windows.Forms.dll", f"/out:{EXE}", str(SOURCE)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        sys.exit(proc.stdout + proc.stderr)
    print(f"wrote {EXE.name}")


if __name__ == "__main__":
    write_icon()
    if os.name == "nt":
        build_exe()
