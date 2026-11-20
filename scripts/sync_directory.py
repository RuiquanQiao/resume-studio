"""Copy the employer directory (公司库) into the app: jobhunt/data/directory/ (2026-11-20).

The directory is the open campus-employers database (github.com/RuiquanQiao/campus-employers). Its
browsing page's compact files, docs/data/index.json and docs/data/<cc>.json, are copied as they are, so a fresh
install has every employer offline. The window then keeps itself up to date from GitHub (jobhunt/directory.py).

    python scripts/sync_directory.py                 # from the campus-employers checkout beside this repository
    python scripts/sync_directory.py --from E:/Forge/campus-employers
    python scripts/sync_directory.py --download      # from GitHub raw

Each file is checked before anything is replaced; files of countries no longer listed are removed. Then run
scripts/check.py and commit jobhunt/data/directory/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobhunt.directory import BUNDLED, REMOTE, valid_country, valid_index  # noqa: E402

BESIDE = ROOT.parent / "campus-employers"


def read(src: str | Path, name: str, download: bool) -> bytes:
    if download:
        req = urllib.request.Request(str(src) + name, headers={"User-Agent": "ResumeStudio-directory"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    return (Path(src) / "docs" / "data" / name).read_bytes()


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="src", default=str(BESIDE), help="a campus-employers checkout")
    ap.add_argument("--download", action="store_true", help="read the files from GitHub raw instead")
    ap.add_argument("--dest", default=str(BUNDLED), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    src = REMOTE if a.download else a.src
    dest = Path(a.dest)
    try:
        index_raw = read(src, "index.json", a.download)
        index = json.loads(index_raw.decode("utf-8"))
        if not valid_index(index):
            raise ValueError("index.json is not in the expected shape")
        files = {}
        for c in index["countries"]:
            data = read(src, f"{c['code']}.json", a.download)
            if c.get("sha256") and hashlib.sha256(data).hexdigest() != c["sha256"]:
                raise ValueError(f"{c['code']}.json does not match its sha256")
            body = json.loads(data.decode("utf-8"))
            if not valid_country(body):
                raise ValueError(f"{c['code']}.json is not in the expected shape")
            files[c["code"]] = (data, len(body["rows"]))
    except (OSError, ValueError) as e:
        print(f"nothing copied: {e}", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    for code, (data, _) in files.items():
        (dest / f"{code}.json").write_bytes(data)
    for old in dest.glob("*.json"):
        if old.name != "index.json" and old.stem not in files:
            old.unlink()
    (dest / "index.json").write_bytes(index_raw)
    print(f"directory {index['updated']}: " + ", ".join(f"{c} {n}" for c, (_, n) in files.items()) + f" -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
