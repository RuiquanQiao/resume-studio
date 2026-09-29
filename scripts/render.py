"""Render or export a resume version from the command line (for Claude Code).

    python scripts/render.py                      # render every version, report pages
    python scripts/render.py RES-TECH-EN          # render one version
    python scripts/render.py RES-TECH-EN --export # also copy PDF + .tex to the export dir
    python scripts/render.py --check              # only validate resume.yaml

Options: --data PATH (default: $RESUME_STUDIO_DATA or ./resume.yaml)
"""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import reexec_in_venv, resolve_data

reexec_in_venv()

from resume import model  # noqa: E402
from resume.service import Studio  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("versions", nargs="*")
    ap.add_argument("--data")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    studio = Studio(resolve_data(args.data))
    if not studio.store.exists():
        print(f"找不到数据文件：{studio.store.path}", file=sys.stderr)
        return 2
    doc, _ = studio.store.load()
    warnings = model.validate(doc)
    for w in warnings:
        print(f"warning: {w}")
    if args.check:
        return 1 if warnings else 0

    ids = args.versions or [str(v.get("id")) for v in doc.get("versions") or []]
    results, worst = [], 0
    for vid in ids:
        r = studio.export(vid) if args.export else studio.render(vid)
        results.append(r)
        if not r["ok"]:
            worst = 1
            print(f"{vid}: FAILED")
            for e in r["errors"]:
                print(f"    {e}")
            continue
        flag = "OK" if r["pages"] == 1 else f"OVER ONE PAGE ({r['pages']} pages)"
        print(f"{vid}: {flag}  {r['seconds']}s  {r['pdf']}")
        if r.get("exported"):
            for k, p in r["exported"].items():
                print(f"    exported {k}: {p}")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return worst


if __name__ == "__main__":
    sys.exit(main())
