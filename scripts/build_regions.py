"""Build the names of countries and regions a company's arm goes by (麦当劳中国, McDonald's Australia, Siemens
Deutschland, 日本マクドナルド): next to the company itself it is another company, with its own careers site, so
adding it never asks "is this the one on the list?" (jobhunt/store.py, _close). Run once; the output is committed.

    python scripts/build_regions.py

Reads (downloaded into .cache/regions/ if missing):
    Unicode CLDR territory names (Unicode License, https://github.com/unicode-org/cldr-json), in the languages
    below: every country and territory, and the continents and their parts (Europe, 东南亚, Latin America…)

Writes:
    jobhunt/data/regions.txt   one name key per line (lower case, no spaces or punctuation, as store._key makes it)
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobhunt.store import _key  # noqa: E402

CACHE = ROOT / ".cache" / "regions"
OUT = ROOT / "jobhunt" / "data" / "regions.txt"
URL = "https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-localenames-full/main/{lang}/territories.json"
# the interface's languages, and those of the places people most often look for work in
LANGS = ["en", "zh", "zh-Hant", "ja", "ko", "de", "fr", "es", "pt", "it", "nl", "sv", "da", "nb", "fi", "pl", "cs",
         "ru", "uk", "tr", "el", "hu", "ro", "vi", "th", "id", "ms", "fil", "hi"]
SKIP = {"001", "ZZ", "UN", "EZ", "QO"}   # the world, "unknown", the UN, the eurozone: not where an arm is
# what hiring says that CLDR doesn't: the regions companies are run by
EXTRA = ["APAC", "Asia Pacific", "Asia-Pacific", "EMEA", "LATAM", "ANZ", "DACH", "Benelux", "Nordics", "Nordic",
         "MENA", "Greater China", "Mainland China", "North America", "大中华", "大中华区", "中国区", "中国大陆", "亚太",
         "亚太区", "港澳", "大中華", "亞太", "アジア太平洋", "아시아태평양"]
# short names hide inside ordinary words ("us" in "campus", タイ in タイヤ): only these acronyms are kept
SHORT = {"usa", "uk", "uae", "eu", "anz", "hk", "prc"}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
KANA = re.compile(r"[぀-ヿ]+")


def names(lang: str) -> dict[str, str]:
    p = CACHE / f"{lang}.json"
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL.format(lang=lang), p)
    data = json.loads(p.read_text(encoding="utf-8"))["main"]
    return next(iter(data.values()))["localeDisplayNames"]["territories"]


def main() -> None:
    keys = set()
    for lang in LANGS:
        for code, name in names(lang).items():
            if code.split("-")[0] not in SKIP:
                keys.add(_key(name))
    keys |= {_key(x) for x in EXTRA}
    keys = {k for k in keys if (CJK.search(k) and len(k) >= 2 and not (KANA.fullmatch(k) and len(k) <= 2))
            or len(k) >= 4 or k in SHORT}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(k + "\n" for k in sorted(keys)), encoding="utf-8", newline="\n")
    print(f"{len(keys)} names -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
