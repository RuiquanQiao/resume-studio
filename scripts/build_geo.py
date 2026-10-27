"""Build the map's data from public sources (docs/JOB_HUNT.md, 2026-10-21: the world map). Run once; the output is committed.

    python scripts/build_geo.py

Reads (downloaded into .cache/geo/ and .cache/regions/ if missing):
    GeoNames cities15000 (CC BY 4.0, https://www.geonames.org): every place of 15,000 people or more
    GeoNames alternateNamesV2 and admin1CodesASCII: each place's and each state's name in each language (200 MB)
    Unicode CLDR territory names (Unicode License): every country and region, in the interface's languages
    Unicode CLDR territoryInfo: each country's official languages
    Natural Earth 50m land (public domain, https://www.naturalearthdata.com): the land, no borders

Writes:
    jobhunt/data/cities.tsv.gz   the places a role can be in (docs/JOB_HUNT.md, 2026-10-27: places are picked
                                 from a list), a header line "#\ten|zh|…" naming the languages, then one place a line:
                                 id  lat  lon  population  country  state  name in each language (| between; ""
                                 where it is the English one)  other names it is found by (|)
    jobhunt/data/admin1.tsv.gz   the states and provinces the places are in: CC.code  name in each language (|)
    jobhunt/data/territories.json  {code: {lang: name}}: the countries and regions a remote role is open to
    jobhunt/data/languages.json  {country: [language, ..]}: its official languages a resume can be written in, the
                                 most spoken first (what a company's research is written in: jobhunt/writing.py)
    web/geo/land.txt             the land as one SVG path, in the map's projection (Miller), 0.05° steps
"""
from __future__ import annotations

import gzip
import io
import json
import math
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.i18n import UI_LANGS  # noqa: E402
from jobhunt.geo import TRAD, tidy as _tidy  # noqa: E402
from resume.langs import LANGS as RESUME_LANGS  # noqa: E402

CACHE = ROOT / ".cache" / "geo"
SOURCES = {
    "cities15000.zip": "https://download.geonames.org/export/dump/cities15000.zip",
    "alternateNamesV2.zip": "https://download.geonames.org/export/dump/alternateNamesV2.zip",
    "admin1CodesASCII.txt": "https://download.geonames.org/export/dump/admin1CodesASCII.txt",
    "ne_50m_land.geojson": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson",
}
CLDR = "https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-localenames-full/main/{lang}/territories.json"
CLDR_INFO = "https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-core/supplemental/territoryInfo.json"
LANGS = list(UI_LANGS)
# GeoNames' language tags for the interface's languages, the better ones first (zh-CN is simplified Chinese)
TAGS = {"en": ["en"], "zh": ["zh-CN", "zh-Hans", "zh"], "ja": ["ja"], "ko": ["ko"], "es": ["es"], "fr": ["fr"], "de": ["de"]}
# not where a role is open to: the world (that's "anywhere"), "unknown", test regions, the UN, the eurozone
SKIP = {"001", "ZZ", "XA", "XB", "UN", "EZ", "QO"}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
def local_names(ids: set[int]) -> dict[int, dict[str, str]]:
    """{geonameid: {lang: its name there}}: the preferred, then the short, then any name not historic or colloquial."""
    rank = {(tag): (lang, len(tags) - i) for lang, tags in TAGS.items() for i, tag in enumerate(tags)}
    best: dict[tuple[int, str], tuple[tuple, str]] = {}
    with zipfile.ZipFile(fetch("alternateNamesV2.zip")).open("alternateNamesV2.txt") as raw:
        for row in io.TextIOWrapper(raw, encoding="utf-8"):
            f = row.rstrip("\n").split("\t")
            if len(f) < 8 or f[2] not in rank:
                continue
            gid = int(f[1])
            if gid not in ids or f[6] == "1" or f[7] == "1" or (len(f) > 9 and (f[8] or f[9])):
                continue
            lang, tag_rank = rank[f[2]]
            name = _tidy(lang, f[3].strip())
            if not name or "|" in name or (lang in ("zh", "ja", "ko") and not CJK.search(name)):
                continue
            plain = not (lang == "zh" and f[2] == "zh" and set(TRAD) & set(name))
            if not plain:
                name = "".join(TRAD.get(ch, ch) for ch in name)
            score = (plain, f[4] == "1", tag_rank, f[5] == "1", -len(name))
            k = (gid, lang)
            if k not in best or score > best[k][0]:
                best[k] = (score, name)
    out: dict[int, dict[str, str]] = {}
    for (gid, lang), (_, name) in best.items():
        out.setdefault(gid, {})[lang] = name
    return out


def row_names(en: str, local: dict[str, str]) -> str:
    """The English name, then each other language's ("" where it is the English one)."""
    return "|".join(en if lang == "en" else "" if local.get(lang, en) == en else local[lang] for lang in LANGS)


def cities() -> None:
    text = zipfile.ZipFile(fetch("cities15000.zip")).read("cities15000.txt").decode("utf-8")
    rows = [r.split("\t") for r in text.splitlines()]
    admin1 = {}   # "CN.02" -> geonameid
    for line in fetch("admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        f = line.split("\t")
        if len(f) >= 4 and f[3].isdigit():
            admin1[f[0]] = (int(f[3]), f[1])
    local = local_names({int(f[0]) for f in rows} | {gid for gid, _ in admin1.values()})
    lines = ["#\t" + "|".join(LANGS)]
    used_states = set()
    for f in rows:
        gid, name, ascii_, alts, lat, lon, cc, a1, pop = int(f[0]), f[1], f[2], f[3], f[4], f[5], f[8], f[10], int(f[14] or 0)
        mine = local.get(gid, {})
        en = mine.get("en") or name
        state = f"{cc}.{a1}" if f"{cc}.{a1}" in admin1 else ""
        used_states.add(state)
        shown = {en, *mine.values()}
        others = [x for x in dict.fromkeys([name, ascii_]) if x and x not in shown]
        for a in alts.split(","):
            a = a.strip()
            # every CJK name; Latin ones (Peking, Londres, München) only for big cities, to keep the file small
            if a and a not in shown and a not in others and "|" not in a and (CJK.search(a) or (pop >= 300_000 and len(a) > 2 and not a.isupper())):
                others.append(a)
        lines.append(f"{gid}\t{float(lat):.3f}\t{float(lon):.3f}\t{pop}\t{cc}\t{state}\t{row_names(en, mine)}\t{'|'.join(others)}")
    out = ROOT / "jobhunt" / "data" / "cities.tsv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8", newline="\n", compresslevel=9) as w:
        w.write("\n".join(lines) + "\n")
    print(f"{out.relative_to(ROOT)}: {len(lines) - 1} places, {out.stat().st_size // 1024} KB")
    states = ["#\t" + "|".join(LANGS)]
    for code, (gid, name) in sorted(admin1.items()):
        if code in used_states:
            mine = local.get(gid, {})
            en = mine.get("en") or name
            states.append(f"{code}\t{row_names(en, mine)}")
    out = ROOT / "jobhunt" / "data" / "admin1.tsv.gz"
    with gzip.open(out, "wt", encoding="utf-8", newline="\n", compresslevel=9) as w:
        w.write("\n".join(states) + "\n")
    print(f"{out.relative_to(ROOT)}: {len(states) - 1} states, {out.stat().st_size // 1024} KB")


def territories() -> None:
    """Every country and region by its code, in each interface language (CLDR; a short name, 香港, where it has one)."""
    cache = ROOT / ".cache" / "regions"
    out: dict[str, dict[str, str]] = {}
    for lang in LANGS:
        p = cache / f"{lang}.json"
        if not p.exists():
            cache.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(CLDR.format(lang=lang), p)
        names = next(iter(json.loads(p.read_text(encoding="utf-8"))["main"].values()))["localeDisplayNames"]["territories"]
        for code, name in names.items():
            base, _, alt = code.partition("-alt-")
            # a short name where it is a name (香港 for 中国香港特别行政区), not an abbreviation (US, UK)
            if base in SKIP or (alt and (alt != "short" or (name.isascii() and name.isupper()))):
                continue
            if alt == "short" or lang not in out.setdefault(base, {}):
                out[base][lang] = name
    path = ROOT / "jobhunt" / "data" / "territories.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8", newline="\n")
    print(f"{path.relative_to(ROOT)}: {len(out)} countries and regions, {path.stat().st_size // 1024} KB")


def languages() -> None:
    """Each country's official languages (official or de facto), the most spoken first, as resume language codes."""
    p = ROOT / ".cache" / "regions" / "territoryInfo.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(CLDR_INFO, p)
    info = json.loads(p.read_text(encoding="utf-8"))["supplemental"]["territoryInfo"]
    codes = {c.casefold(): c for c in RESUME_LANGS}
    out: dict[str, list[str]] = {}
    for cc, v in info.items():
        langs = []
        for tag, x in sorted((v.get("languagePopulation") or {}).items(), key=lambda kv: -float(kv[1].get("_populationPercent") or 0)):
            if x.get("_officialStatus") not in ("official", "de_facto_official"):
                continue
            base, _, script = tag.partition("_")
            code = codes.get("zh-tw" if (base, script) == ("zh", "Hant") else base)
            if code and code not in langs:
                langs.append(code)
        if langs:
            out[cc] = langs
    path = ROOT / "jobhunt" / "data" / "languages.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8", newline="\n")
    print(f"{path.relative_to(ROOT)}: {len(out)} countries")


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
    territories()
    languages()
    if "--no-land" not in sys.argv:
        land()
