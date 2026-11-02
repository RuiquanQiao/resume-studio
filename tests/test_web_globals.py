"""The window's scripts (web/*.js) share one global scope: a top-level name declared twice means the later
file's one silently replaces the other (2026-11-02: a second `openPosting` in hunt.js broke the 求职 page's)."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = re.compile(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)|^(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)


def test_no_top_level_name_is_declared_twice():
    where = defaultdict(list)
    for path in sorted((ROOT / "web").glob("*.js")):
        for m in TOP.finditer(path.read_text(encoding="utf-8")):
            line = path.read_text(encoding="utf-8").count("\n", 0, m.start()) + 1
            where[m.group(1) or m.group(2)].append(f"{path.name}:{line}")
    twice = {name: at for name, at in where.items() if len(at) > 1}
    assert not twice, "declared more than once: " + "; ".join(f"{n} ({', '.join(a)})" for n, a in sorted(twice.items()))
