"""Where a place is on the map (docs/JOB_HUNT.md, 2026-10-21: the world map), worked out on this computer.

jobhunt/data/cities.tsv.gz (GeoNames, CC BY 4.0, built by scripts/build_geo.py) knows every place of 15,000
people or more, by its own name, its Chinese, Japanese and Korean names, and the other names of big cities.
"杭州·余杭区" is found by its parts, "北京市海淀区" by its start. Of two places with one name, the bigger wins
(Sydney is Australia's). What it can't place, or places wrong, the user or Claude pins by hand:

    .studio/hunt/geo.json   {"<area as written>": [lat, lon]}
"""
from __future__ import annotations

import gzip
import json
import re
import threading
from pathlib import Path
from typing import Any

from core.i18n import t

DATA = Path(__file__).resolve().parent / "data" / "cities.tsv.gz"
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_PARTS = re.compile(r"[·・,，、/|;；()（）\[\]【】\-–—]+|\s{2,}")
_index: dict[str, tuple] | None = None
_lock = threading.Lock()


def _key(s: str) -> str:
    k = re.sub(r"[\s.'’]+", "", str(s or "")).casefold()
    return re.sub(r"(市|特别行政区|特別行政區)$", "", k) if _CJK.search(k) else k


def _cities() -> dict[str, tuple]:
    """name key -> (lat, lon, population, country, English name), the biggest place of each name."""
    global _index
    with _lock:
        if _index is None:
            idx: dict[str, tuple] = {}
            with gzip.open(DATA, "rt", encoding="utf-8") as f:
                for line in f:
                    lat, lon, pop, cc, names = line.rstrip("\n").split("\t")
                    names = names.split("|")
                    hit = (float(lat), float(lon), int(pop), cc, names[0])
                    for n in names:
                        k = _key(n)
                        if k and (k not in idx or idx[k][2] < hit[2]):
                            idx[k] = hit
            _index = idx
    return _index


def find(area: str) -> dict | None:
    """{lat, lon, name, country} for a place as someone wrote it, or None."""
    idx = _cities()
    whole = _key(area)
    if not whole:
        return None
    tries = [whole] + [_key(p) for p in _PARTS.split(str(area)) if _key(p)]
    for k in tries:
        if k in idx:
            return _hit(idx[k])
    # "北京市海淀区", "上海浦东": the longest start that is a place (CJK only: Latin starts mislead)
    for k in tries:
        if _CJK.search(k):
            for n in range(len(k) - 1, 1, -1):
                if k[:n] in idx:
                    return _hit(idx[k[:n]])
    return None


def _hit(h: tuple) -> dict:
    return {"lat": h[0], "lon": h[1], "name": h[4], "country": h[3]}


class Geo:
    def __init__(self, hunt_dir: Path) -> None:
        self.path = Path(hunt_dir) / "geo.json"
        self.lock = threading.Lock()

    def pins(self) -> dict[str, list[float]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 2} if isinstance(data, dict) else {}

    def lookup(self, areas: Any) -> dict[str, dict | None]:
        """Every area -> where it is (pinned by hand first), or None when it can't be placed."""
        pins = self.pins()
        out: dict[str, dict | None] = {}
        for a in areas:
            a = str(a or "").strip()
            if not a or a in out:
                continue
            if a in pins:
                out[a] = {"lat": pins[a][0], "lon": pins[a][1], "name": a, "country": "", "pinned": True}
            else:
                out[a] = find(a)
        return out

    def pin(self, area: str, lat: Any, lon: Any) -> dict:
        """Put an area where the user says (or, with lat None, back to where the city list puts it)."""
        area = " ".join(str(area or "").split())[:80]
        if not area:
            raise ValueError(t("Say which place"))
        with self.lock:
            pins = self.pins()
            if lat is None:
                pins.pop(area, None)
            else:
                try:
                    la, lo = round(float(lat), 4), round(float(lon), 4)
                except (TypeError, ValueError):
                    raise ValueError(t("“{value}” isn't a latitude and longitude", value=f"{lat}, {lon}"))
                if not (-90 <= la <= 90 and -180 <= lo <= 180):
                    raise ValueError(t("“{value}” isn't a latitude and longitude", value=f"{lat}, {lon}"))
                pins[area] = [la, lo]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(pins, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        return self.lookup([area])[area] or {}
