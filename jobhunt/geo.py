"""Places: the list a role's cities and regions are picked from, and where each is on the map (docs/JOB_HUNT.md,
2026-10-21: the world map; 2026-10-27: places are picked from a list, like LinkedIn's or Workday's location box).

A place is kept by its id and shown by its name in the interface's language, so a table never mixes 悉尼 and
Sydney, and switching the window to English shows Sydney everywhere:

    g1816670    a city: its GeoNames id (jobhunt/data/cities.tsv.gz, every place of 15,000 people or more,
                CC BY 4.0, built by scripts/build_geo.py, with its state from admin1.tsv.gz)
    t:CN        a country or region: its Unicode CLDR code (jobhunt/data/territories.json): 美国, 欧洲, 东南亚
    r:apac      what hiring says that CLDR doesn't (REGIONS): 不限, APAC, 中国大陆, EMEA…

Text is found too: by any of a place's names in any language ("杭州·余杭区" by its parts, "北京市海淀区" by its
start, "浙江杭州" by its end); of two places with one name the bigger wins (Sydney is Australia's).

What the list doesn't know (雄安, a district, a town under 15,000 people) is written as it is and then looked up once
on OpenStreetMap's Nominatim (https://nominatim.org, ODbL), in the background: where it is and its name in each
language. A hit becomes a place like the others, with an id of its own, kept with the data:

    o:R7157395  a place found on OpenStreetMap (R relation, W way, N node + its OSM id)
    resume-studio-data/hunt/places.json   {"places": {id: {lat, lon, country, state, names}}, "asked": {key: [date, id or ""]}}

Only a place's name is sent, one request a second at most, as Nominatim's usage policy asks (never while typing).
What it can't find stays as written; where it is, or a place the map puts wrong, the user or Claude pins by hand:

    resume-studio-data/hunt/geo.json   {"<area as written>": [lat, lon]}
"""
from __future__ import annotations

import bisect
import functools
import gzip
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from core.derived import Derived, code_key, stat_key
from core.i18n import UI_LANGS, current, t

DATA = Path(__file__).resolve().parent / "data"
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_PARTS = re.compile(r"[·・,，、/|;；:：()（）\[\]【】\-–—]+|\s{2,}")
_lock = threading.Lock()
_db: dict | None = None

# the characters of place names that only traditional Chinese uses, and their simplified ones: a name written only
# in traditional is simplified (the list's names, and OpenStreetMap's name:zh)
TRAD = dict(zip("臺灣區縣東門關鄉鎮觀國蘭倫爾羅薩維華陽陰雲廣嶺齊濟遼寧龍貴寶興業會鐵橋廠灘當聖蘇與傑亞紐約馬來韓圖達蘭頓爾費",
                "台湾区县东门关乡镇观国兰伦尔罗萨维华阳阴云广岭齐济辽宁龙贵宝兴业会铁桥厂滩当圣苏与杰亚纽约马来韩图达兰顿尔费"))
# what ends a city's official name in a list of cities: 首尔特别市 is 首尔, 서울특별시 is 서울, 大阪市 is 大阪
SUFFIXES = {"zh": ["特别行政区", "特别市", "广域市", "市"], "ja": ["特別市", "広域市", "市", "都"], "ko": ["특별자치시", "특별시", "광역시", " 시", "시"]}


def tidy(lang: str, name: str) -> str:
    """杭州市 is 杭州 in a list of cities (two characters stay: 津市, 京都)."""
    for s in SUFFIXES.get(lang, []):
        if name.endswith(s) and len(name) - len(s) >= 2:
            return name[: -len(s)].strip()
    return name

# regions hiring speaks of that aren't countries or CLDR's regions: id -> (English name, other names it goes by)
REGIONS = {
    "any": ("Anywhere", ["不限", "anywhere", "worldwide", "global", "globally", "全球", "全世界", "世界各地", "任意", "任何地方",
                         "どこでも", "全世界", "어디서나", "전 세계", "weltweit", "partout", "cualquier lugar", "world"]),
    "mainland-china": ("Mainland China", ["中国大陆", "中國大陸", "大陆", "内地", "中国内地", "mainland", "prc", "中国本土", "중국 본토"]),
    "greater-china": ("Greater China", ["大中华", "大中华区", "大中華", "大中華區", "中国区", "中華圏", "중화권"]),
    "apac": ("Asia Pacific (APAC)", ["apac", "asia pacific", "asia-pacific", "亚太", "亚太区", "亞太", "亚太地区", "アジア太平洋", "아시아태평양"]),
    "emea": ("Europe, Middle East and Africa (EMEA)", ["emea", "欧洲中东非洲", "欧非中东"]),
    "mena": ("Middle East and North Africa (MENA)", ["mena", "中东北非", "中東北アフリカ"]),
    "dach": ("Germany, Austria and Switzerland (DACH)", ["dach", "德奥瑞", "德语区"]),
    "nordics": ("The Nordics", ["nordics", "nordic", "北欧五国", "北欧诸国", "北欧国家", "norden"]),
    "benelux": ("Benelux", ["benelux", "比荷卢"]),
}
# hiring's names for CLDR's regions: LATAM is Latin America (419), ANZ is Australasia (053)
REGION_WORDS = {"latam": "t:419", "anz": "t:053", "north america": "t:003", "北美": "t:003", "北美洲": "t:003",
                "south east asia": "t:035", "sea": "t:035", "东南亚": "t:035", "europe": "t:150", "欧洲": "t:150",
                "uk": "t:GB", "usa": "t:US", "us": "t:US", "u.s.": "t:US", "eu": "t:EU", "hk": "t:HK"}


def _key(s: str) -> str:
    k = re.sub(r"[\s.'’]+", "", str(s or "")).casefold()
    return re.sub(r"(市|特别行政区|特別行政區)$", "", k) if _CJK.search(k) else k


def _load() -> dict:
    """The places and their names, read once. The indexes that find a place by what someone wrote are built on
    the first search (_indexed): keying and sorting 127,000 names took a second, and the job-hunt table, which only
    names the places it has, waited for it on every start (2026-11-13)."""
    global _db
    with _lock:
        if _db is not None:
            return _db
        cities: dict[str, tuple] = {}     # id -> (lat, lon, pop, country, state, {lang: name})
        others: dict[str, str] = {}       # id -> the other names it goes by, "|"-separated, for the indexes
        with gzip.open(DATA / "cities.tsv.gz", "rt", encoding="utf-8") as f:
            langs = f.readline().rstrip("\n").split("\t")[1].split("|")
            for line in f:
                gid, lat, lon, pop, cc, state, names, more = line.rstrip("\n").split("\t")
                pid = "g" + gid
                cities[pid] = (float(lat), float(lon), int(pop), cc, state, dict(zip(langs, names.split("|"))))
                if more:
                    others[pid] = more
        states = _states()
        terr = _small()["terr"]
        regions: dict[str, str] = {}      # name key -> region id (t:… or r:…)
        for code, names in terr.items():
            for n in names.values():
                regions.setdefault(_key(n), "t:" + code)
        for rid, (en, words) in REGIONS.items():
            for n in [en, *words]:
                regions[_key(n)] = "r:" + rid
        for w, rid in REGION_WORDS.items():
            regions[_key(w)] = rid
        # what may start a city's name in Chinese, Japanese or Korean: its province or country (浙江杭州, 中国上海)
        heads = {_key(n) for names in [*states.values(), *terr.values()] for n in names.values() if _CJK.search(n or "")}
        heads |= {h.removesuffix(x) for h in heads for x in ("省", "州", "県", "도") if h.endswith(x) and len(h) > len(x) + 1}
        heads |= {h + "省" for h in heads}   # 广东 and 广东省
        _db = {"cities": cities, "others": others, "found": {}, "states": states,
               "terr": terr, "regions": regions, "heads": heads, "langs": _small()["langs"]}
        for pid, e in _found["places"].items():   # those found for the open data folder (use())
            try:
                _insert(_db, pid, e)
            except (KeyError, TypeError, ValueError):
                continue
        return _db


def _indexed() -> dict:
    """The places with the indexes that find one by what someone wrote, built once, on the first search."""
    db = _load()
    with _lock:
        if "index" in db:
            return db
        cities, found = db["cities"], db["found"]
        index: dict[str, str] = {}        # name key -> id of the biggest place with that name
        # (name key, id, 0 for a name the window shows in some language, 1 for another), sorted: what a typed
        # start finds ("san f" is San Francisco before San Francisco de Quito)
        prefix: list[tuple[str, str, int]] = []
        for pid, (_, _, pop, _, _, local) in cities.items():
            if pid in found:
                continue
            shown = {_key(n) for n in local.values() if n}
            for k in shown | {_key(n) for n in db["others"].get(pid, "").split("|") if n}:
                if not k:
                    continue
                prefix.append((k, pid, 0 if k in shown else 1))
                if k not in index or cities[index[k]][2] < pop:
                    index[k] = pid
        for pid, keys in found.items():   # found ones (use, look_up) after the list's own, as when added later
            for k in keys:
                index.setdefault(k, pid)
                prefix.append((k, pid, 0))
        prefix.sort()
        db.update(index=index, prefix=prefix, pkeys=[x[0] for x in prefix])
        return db


@functools.cache
def _states() -> dict[str, dict]:
    """Each state's (province's) names by its code: a small file, read without the list of cities."""
    states: dict[str, dict] = {}
    with gzip.open(DATA / "admin1.tsv.gz", "rt", encoding="utf-8") as f:
        slangs = f.readline().rstrip("\n").split("\t")[1].split("|")
        for line in f:
            code, names = line.rstrip("\n").split("\t")
            states[code] = dict(zip(slangs, names.split("|")))
    return states


@functools.cache
def _small() -> dict:
    """The countries' names and languages: two small files, read without the list of cities."""
    return {"terr": json.loads((DATA / "territories.json").read_text(encoding="utf-8")),
            "langs": json.loads((DATA / "languages.json").read_text(encoding="utf-8"))}


# the cities the app has looked at, kept between starts (the cache folder's cities.json, set by Geo): the job-hunt
# table names a few hundred places, and reading the list of 34,000 for them took half a second on every start
_memo: dict[str, Any] = {"path": None, "rows": None, "key": ""}


def _memo_key() -> str:
    return json.dumps([code_key(__name__), stat_key(DATA / "cities.tsv.gz"), stat_key(DATA / "admin1.tsv.gz")])


def _city(pid: str) -> tuple | None:
    """(lat, lon, pop, country, state, {lang: name}, {lang: state's name}) for a city in the list or found on
    OpenStreetMap, or None: from the list once it is read, else from the cities kept from earlier starts."""
    if not isinstance(pid, str) or not pid or pid.startswith(("t:", "r:")):
        return None
    e = None
    if _db is None:
        with _lock:
            rows = _memo["rows"]
            if rows is None:
                rows = {}
                if _memo["path"]:
                    try:
                        saved = json.loads(Path(_memo["path"]).read_text(encoding="utf-8"))
                        if isinstance(saved, dict) and saved.get("key") == _memo_key() and isinstance(saved.get("rows"), dict):
                            rows = {k: tuple(v[:5]) + (dict(v[5]), dict(v[6])) if v else None for k, v in saved["rows"].items()}
                    except (OSError, ValueError, TypeError, IndexError):
                        rows = {}
                _memo["rows"] = rows
            if pid in rows:
                return rows[pid]
            e = _found["places"].get(pid)
        if isinstance(e, dict):   # found on OpenStreetMap for this data folder: its own record has all of it
            try:
                st = e.get("state") or ""
                return (float(e["lat"]), float(e["lon"]), 0, e.get("country") or "", st, dict(e["names"]), dict(_states().get(st) or {}))
            except (KeyError, TypeError, ValueError):
                pass
    db = _load()
    c = db["cities"].get(pid)
    row = c + (dict(db["states"].get(c[4]) or {}),) if c else None
    if pid.startswith("g"):   # one found on OpenStreetMap comes from the data folder, not the list: never kept here
        _keep(pid, row)       # nor one the list doesn't have: None, so it isn't looked for again on every start
    return row


def _keep(pid: str, row: tuple | None) -> None:
    """A city joins the kept ones; they are written a moment later, once for all the table looked at."""
    with _lock:
        rows = _memo["rows"] if _memo["rows"] is not None else {}
        _memo["rows"] = rows
        if pid in rows:
            return
        rows[pid] = row
        if _memo["path"] and not _memo.get("timer"):
            _memo["timer"] = threading.Timer(1.0, _write_kept)
            _memo["timer"].daemon = True
            _memo["timer"].start()


def _write_kept() -> None:
    with _lock:
        _memo["timer"] = None
        path, rows = _memo["path"], dict(_memo["rows"] or {})
    if not path or not rows:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_name(Path(path).name + ".tmp")
        tmp.write_text(json.dumps({"key": _memo_key(), "rows": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:   # kept in memory all the same; the next start reads the list again
        pass


def _local(names: dict[str, str], lang: str) -> str:
    return names.get(lang) or names.get("en") or ""


def _country(cc: str, lang: str) -> str:
    return _local(_small()["terr"].get(cc) or {}, lang) or cc


def name(pid: str, lang: str | None = None) -> str:
    """A place's name in the interface's language (or `lang`); "" for an id the list doesn't have."""
    lang = lang or current()
    if not isinstance(pid, str):
        return ""
    if pid.startswith("t:"):
        terr = _small()["terr"]
        return _local(terr[pid[2:]], lang) if pid[2:] in terr else ""
    if pid.startswith("r:"):
        return t(REGIONS[pid[2:]][0], ui=lang) if pid[2:] in REGIONS else ""
    c = _city(pid)
    return _local(c[5], lang) if c else ""


def known(pid: Any) -> bool:
    return isinstance(pid, str) and bool(pid) and bool(name(pid, "en"))


def label(pid: str, lang: str | None = None) -> dict:
    """{id, name, detail, lat, lon}: detail tells two places of one name apart (江苏, 中国 / Anhui, China)."""
    lang = lang or current()
    out = {"id": pid, "name": name(pid, lang), "detail": "", "lat": None, "lon": None}
    c = _city(pid)
    if c:
        lat, lon, _, cc, _, _, state = c
        st = _local(state, lang)
        out.update(lat=lat, lon=lon, detail=", ".join(x for x in (st, _country(cc, lang)) if x and x != out["name"]))
    return out


def find(area: str) -> dict | None:
    """{id, lat, lon, name, country} for a city as someone wrote it, or None."""
    db = _indexed()
    whole = _key(area)
    if not whole:
        return None
    parts = _PARTS.split(str(area))
    # "广东 广州市": a space parts Chinese too (and "Culver City" stays whole)
    parts += [x for p in parts if _CJK.search(p) for x in p.split() if _CJK.search(x)]
    tries = [whole] + [_key(p) for p in parts if _key(p)]
    for k in tries:
        if k in db["index"]:
            return _hit(db["index"][k])
    # "浙江杭州", "广东省深圳": the end after a province or a country, first (湖南长沙 is 长沙, not 湖南, a city in
    # Japan); then "北京市海淀区", "上海浦东": the longest start that is a place. CJK only: Latin starts mislead,
    # and any end would make 纳拉布里 布里, in Brazil
    for k in tries:
        if _CJK.search(k):
            for n in range(2, len(k) - 1):
                if k[:n] in db["heads"] and k[n:] in db["index"]:
                    return _hit(db["index"][k[n:]])
            for n in range(len(k) - 1, 1, -1):
                if k[:n] in db["index"]:
                    return _hit(db["index"][k[:n]])
    return None


def _hit(pid: str) -> dict:
    lat, lon, _, cc, _, names = _load()["cities"][pid]
    return {"id": pid, "lat": lat, "lon": lon, "name": names.get("en") or "", "country": cc}


def region(area: str) -> str | None:
    """The region a remote role is open to, from what someone wrote: 中国大陆 -> r:mainland-china, 美国 -> t:US."""
    regions = _load()["regions"]
    k = _key(area)
    return regions.get(k) or regions.get(k.removesuffix("地区").removesuffix("region")) or None


def resolve(kind: str, area: str) -> str | None:
    """The id of what someone wrote, as a place of this kind (remote: a region first), or None."""
    if kind == "remote":
        rid = region(area)
        if rid:
            return rid
    hit = find(area)
    return hit["id"] if hit else (region(area) if kind != "remote" else None)


def english(pid: str) -> str:
    """What the data file keeps as a picked place's area: its English name, the same in every language."""
    return name(pid, "en")


def country(pid: Any) -> str:
    """The country a place is in (CN for 杭州, for t:CN and for r:mainland-china), or "" for a region of many."""
    if not isinstance(pid, str):
        return ""
    if pid.startswith("t:"):
        return pid[2:] if len(pid) == 4 and pid[2:].isalpha() else ""
    if pid.startswith("r:"):
        return "CN" if pid == "r:mainland-china" else ""
    c = _city(pid)
    return c[3] if c else ""


def countries(lang: str | None = None) -> list[dict]:
    """Every country (ISO 3166 code, name in the interface's language, its English name), by name: what the user picks
    their citizenship, visas and where they look for work from."""
    lang = lang or current()
    terr = _small()["terr"]
    out = [{"code": cc, "name": _local(n, lang) or cc, "en": n.get("en") or cc} for cc, n in terr.items()
           if len(cc) == 2 and cc.isalpha() and cc not in ("EU", "EZ", "UN", "QO", "XA", "XB", "ZZ")]
    return sorted(out, key=lambda x: x["name"])


def languages(cc: str) -> list[str]:
    """A country's official languages a resume can be written in, the most spoken first (CLDR): SG en, zh, ms."""
    return list(_small()["langs"].get(cc) or [])


# ---- what the list doesn't know: looked up on OpenStreetMap, once -------------------------------------------
NOMINATIM = "https://nominatim.openstreetmap.org/search"
AGENT = "ResumeStudio/1.0 (+https://github.com/RuiquanQiao/resume-studio)"
_OSM = {"node": "N", "way": "W", "relation": "R"}
_net = threading.Lock()
_last = [0.0]                                   # when the last request went out (one a second at most)
_found: dict[str, Any] = {"path": None, "places": {}, "asked": {}}   # the open data folder's places.json


def online() -> bool:
    """Off in the tests (STUDIO_PLACES=off): nothing goes out."""
    return os.environ.get("STUDIO_PLACES") != "off"


def _fetch(q: str) -> list[dict]:
    """Nominatim's answers for a name: one request, a second after the one before. Raises OSError/ValueError."""
    with _net:
        wait = _last[0] + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            url = NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "namedetails": 1, "addressdetails": 1,
                                                            "limit": 5, "accept-language": "en"})
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=15) as r:
                rows = json.loads(r.read().decode("utf-8"))
        finally:
            _last[0] = time.monotonic()
    return rows if isinstance(rows, list) else []


def _osm_names(r: dict) -> dict[str, str]:
    """A found place's name in each interface language: its own tag (name:ja), else its local name where the
    country speaks that language, else none (the English one shows)."""
    nd = r.get("namedetails") or {}
    cc = str((r.get("address") or {}).get("country_code") or "").upper()
    first = (languages(cc) or [""])[0]
    out = {"en": str(r.get("name") or nd.get("name:en") or nd.get("name") or "").strip()}
    for lang in UI_LANGS:
        if lang == "en":
            continue
        tags = ["name:zh-Hans", "name:zh-CN", "name:zh"] if lang == "zh" else [f"name:{lang}"]
        n = next((str(nd[x]) for x in tags if nd.get(x)), "")
        if not n and (first.split("-")[0] == lang or (lang == "ja" and first.startswith("zh"))):
            n = str(nd.get("name") or "")   # Japanese writes a Chinese place in its characters too
        if lang == "zh":
            n = "".join(TRAD.get(ch, ch) for ch in n)
        n = tidy(lang, n.split(";")[0].strip())
        if n and n != out["en"]:
            out[lang] = n
    return out


def _same_name(area: str, r: dict) -> bool:
    """Nominatim guesses: 纳拉布里 (Narrabri) once came back as Brisbane, USA. A hit counts only when one of its
    names is what was written, starts it or ends it (广东 香洲区 is 香洲区; Moorestown, New Jersey is Moorestown Township)."""
    simple = lambda s: "".join(TRAD.get(ch, ch) for ch in _key(s))   # noqa: E731 (馬卡蒂 is 马卡蒂)
    names = {simple(v) for k, x in (r.get("namedetails") or {}).items() if "name" in k for v in str(x).split(";")}
    names |= {simple(r.get("name") or "")}
    asked = {simple(p) for p in [area, *_PARTS.split(area), *area.split()] if _key(p)}
    for a in asked:
        for b in names:
            if len(b) < (2 if _CJK.search(b) else 3) or len(a) < (2 if _CJK.search(a) else 3):
                continue
            if a == b or a.startswith(b) or b.startswith(a) or (_CJK.search(b) and a.endswith(b)):
                return True
    return False


def _add(pid: str, e: dict) -> None:
    """A found place joins the list: shown, found and suggested like the others."""
    db = _load()
    with _lock:
        _insert(db, pid, e)


def _insert(db: dict, pid: str, e: dict) -> None:
    if pid in db["cities"]:
        return
    db["cities"][pid] = (float(e["lat"]), float(e["lon"]), 0, e.get("country") or "", e.get("state") or "", dict(e["names"]))
    keys = sorted(k for k in {_key(n) for n in e["names"].values() if n} | {_key(n) for n in e.get("asked") or []} if k)
    db["found"][pid] = keys
    if "index" not in db:   # not built yet: _indexed takes them then
        return
    for k in keys:
        db["index"].setdefault(k, pid)
        i = bisect.bisect_left(db["prefix"], (k, pid, 0))
        db["prefix"].insert(i, (k, pid, 0))
        db["pkeys"].insert(i, k)


def _state(cc: str, lat: float, lon: float) -> str:
    """The state a found place is in: that of the nearest city in the list, in its country (雄安 is in 河北)."""
    best, code = math.inf, ""
    for la, lo, _, c, st, _ in _load()["cities"].values():
        if c == cc and st:
            d = (la - lat) ** 2 + ((lo - lon) * math.cos(math.radians(lat))) ** 2
            if d < best:
                best, code = d, st
    return code if best < 1.5 ** 2 else ""   # farther than about 150 km: no state rather than a wrong one


def _save_found() -> None:
    path = _found["path"]
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"places": _found["places"], "asked": _found["asked"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def use(hunt_dir: Path) -> None:
    """The places found for this data folder join the list (read once a data folder opens). The list itself is
    read when something first needs it, not here: reading it takes a second, and this runs before the window
    opens (2026-11-11)."""
    path = Path(hunt_dir) / "places.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    with _lock:
        _found.update(path=path, places=dict(data.get("places") or {}), asked=dict(data.get("asked") or {}))
        if _db is None:
            return          # _load adds them
    for pid, e in _found["places"].items():
        try:
            _add(pid, e)
        except (KeyError, TypeError, ValueError):
            continue


def look_up(area: str) -> str | None:
    """The id of a place the list doesn't know, from OpenStreetMap: asked once (a miss again after a week, an error
    next time). None when it isn't found, nothing may go out, or no data folder is open."""
    area = " ".join(str(area or "").split())
    k = _key(area)
    if not k or _found["path"] is None:
        return None
    asked = _found["asked"].get(k)
    if asked and (asked[1] or time.time() - float(asked[0]) < 7 * 86400):
        return asked[1] if asked[1] and known(asked[1]) else None
    if not online():
        return None
    try:
        rows = _fetch(area)
    except (OSError, ValueError):
        return None
    pid = ""
    for r in rows:
        try:
            rank = int(r.get("place_rank") or 0)
            if r.get("category") not in ("place", "boundary") or not 10 <= rank <= 22 or r.get("osm_type") not in _OSM:
                continue
            if not _same_name(area, r):
                continue
            pid = "o:" + _OSM[r["osm_type"]] + str(int(r["osm_id"]))
            cc = str((r.get("address") or {}).get("country_code") or "").upper()
            lat, lon = round(float(r["lat"]), 4), round(float(r["lon"]), 4)
            e = {"lat": lat, "lon": lon, "country": cc, "state": _state(cc, lat, lon), "names": _osm_names(r), "asked": [area]}
            if pid in _found["places"]:
                e["asked"] = sorted({*(_found["places"][pid].get("asked") or []), area})
            _found["places"][pid] = e
            _add(pid, e)
            break
        except (KeyError, TypeError, ValueError):
            pid = ""
    _found["asked"][k] = [int(time.time()), pid]
    _save_found()
    return pid or None


def search(q: str, *, kind: str = "onsite", lang: str | None = None, limit: int = 8) -> list[dict]:
    """What a typed start finds, the most likely first: cities by size (and regions first for a remote role)."""
    lang = lang or current()
    db = _indexed()
    k = _key(q)
    if not k:
        return []
    out: list[str] = []
    if kind == "remote":
        rids = {rid for key, rid in db["regions"].items() if key.startswith(k)}
        exact = db["regions"].get(k)
        out += ([exact] if exact else []) + sorted(rids - {exact}, key=lambda r: (not r.startswith("r:"), name(r, lang)))
    lo = bisect.bisect_left(db["pkeys"], k)
    hi = bisect.bisect_right(db["pkeys"], k + "\U0010ffff")
    found: dict[str, int] = {}
    for _, pid, tier in db["prefix"][lo:hi]:
        found[pid] = min(tier, found.get(pid, 1))
    cities = db["cities"]
    out += sorted(found, key=lambda pid: (found[pid], db["index"].get(k) != pid, -cities[pid][2]))
    return [label(pid, lang) for pid in dict.fromkeys(out)][:limit]


class Geo:
    def __init__(self, hunt_dir: Path, cache_dir: Path | None = None) -> None:
        self.path = Path(hunt_dir) / "geo.json"
        self.lock = threading.Lock()
        if cache_dir:   # the cities looked at, kept here between starts (_city)
            with _lock:
                if _memo["path"] != cache_dir / "cities.json":
                    _memo.update(path=cache_dir / "cities.json", rows=None)
        # where the table's places written as text are, kept between starts: finding them needs the indexes of
        # 127,000 names, built in a second (2026-11-13)
        self._found_kept = Derived(cache_dir / "located.json" if cache_dir else None)

    def pins(self) -> dict[str, list[float]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 2} if isinstance(data, dict) else {}

    def lookup(self, areas: Any) -> dict[str, dict | None]:
        """Every area -> where it is (pinned by hand first), or None when it can't be placed."""
        pins = self.pins()
        want = sorted({a for a in (str(x or "").strip() for x in areas) if a and a not in pins})
        key = json.dumps([code_key(__name__), want, stat_key(_found["path"]) if _found["path"] else None])
        found = self._found_kept.get(key, lambda: {a: find(a) for a in want})
        out: dict[str, dict | None] = {}
        for a in areas:
            a = str(a or "").strip()
            if not a or a in out:
                continue
            if a in pins:
                out[a] = {"lat": pins[a][0], "lon": pins[a][1], "name": a, "country": "", "pinned": True}
            else:
                out[a] = dict(found[a]) if found.get(a) else None
        return out

    def places(self, items: Any, lang: str | None = None) -> dict[str, dict]:
        """Every picked place (id) -> {name, detail, lat, lon} in the interface's language; a city pinned by hand
        (its area, as the map shows it) is where the user put it."""
        pins = self.pins()
        out: dict[str, dict] = {}
        for p in items:
            pid = p.get("id") if isinstance(p, dict) else None
            if pid and pid not in out and known(pid):
                out[pid] = label(pid, lang)
                pin = pins.get(pid)
                if pin:
                    out[pid].update(lat=pin[0], lon=pin[1], pinned=True)
        return out

    def pin(self, area: str, lat: Any, lon: Any) -> dict:
        """Put an area (or a picked place, by its id) where the user says (or, with lat None, back to where the
        city list puts it)."""
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
        if known(area):
            return self.places([{"id": area}]).get(area) or {}
        return self.lookup([area])[area] or {}
