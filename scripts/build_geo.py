"""Build the map's data from public sources (docs/JOB_HUNT.md, 2026-10-21: the world map). Run once; the output is committed.

    python scripts/build_geo.py

Reads (downloaded into .cache/geo/ if missing):
    GeoNames cities15000 (CC BY 4.0, https://www.geonames.org): every place of 15,000 people or more
    Natural Earth 50m land (public domain, https://www.naturalearthdata.com): the land, no borders

Writes:
    jobhunt/data/cities.tsv.gz   lat  lon  population  country  name|other names (CJK names, and Latin ones of big cities)
    web/geo/land.txt             the land as one SVG path, in the map's projection (Miller), 0.05° steps
"""
from __future__ import annotations

import gzip
import json
import math
import re
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "geo"
SOURCES = {
    "cities15000.zip": "https://download.geonames.org/export/dump/cities15000.zip",
    "ne_50m_land.geojson": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson",
}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def fetch(name: str) -> Path:
    p = CACHE / name
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(SOURCES[name], p)
    return p


def miller(lon: float, lat: float) -> tuple[float, float]:
    """The same projection as web/dbview.js (dbvProject): x in degrees of longitude, y down."""
    lat = max(-85.0, min(85.0, lat))
    return lon, -1.25 * math.degrees(math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat))))


def cities() -> None:
    text = zipfile.ZipFile(fetch("cities15000.zip")).read("cities15000.txt").decode("utf-8")
    lines = []
    for row in text.splitlines():
        f = row.split("\t")
        name, ascii_, alts, lat, lon, cc, pop = f[1], f[2], f[3], f[4], f[5], f[8], int(f[14] or 0)
        names = [name] + ([ascii_] if ascii_ and ascii_ != name else [])
        for a in alts.split(","):
            a = a.strip()
            # every CJK name; Latin ones (Peking, Londres, München) only for big cities, to keep the file small
            if a and a not in names and "|" not in a and (CJK.search(a) or (pop >= 300_000 and len(a) > 2 and not a.isupper())):
                names.append(a)
        lines.append(f"{float(lat):.3f}\t{float(lon):.3f}\t{pop}\t{cc}\t{'|'.join(names)}")
    out = ROOT / "jobhunt" / "data" / "cities.tsv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8", newline="\n", compresslevel=9) as w:
        w.write("\n".join(lines) + "\n")
    print(f"{out.relative_to(ROOT)}: {len(lines)} places, {out.stat().st_size // 1024} KB")


def land() -> None:
    geo = json.loads(fetch("ne_50m_land.geojson").read_text(encoding="utf-8"))
    parts = []
    for feat in geo["features"]:
        g = feat["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            for ring in poly:
                pts, last = [], None
                for lon, lat in ring:
                    x, y = miller(lon, lat)
                    q = (round(x * 20), round(y * 20))   # 0.05° steps
                    if q != last:
                        pts.append(q)
                        last = q
                if len(pts) < 4:
                    continue
                d = f"M{pts[0][0]} {pts[0][1]}"
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    d += f"l{bx - ax} {by - ay}"
                parts.append(d + "z")
    out = ROOT / "web" / "geo" / "land.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8", newline="\n")   # units: 1/20 of a degree
    print(f"{out.relative_to(ROOT)}: {len(parts)} rings, {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    cities()
    land()
