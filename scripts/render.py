"""Render or export a resume subset from the command line (for Claude Code).

    python scripts/render.py                      # render every subset, report pages
    python scripts/render.py RES-TECH-EN          # render one subset
    python scripts/render.py RES-TECH-EN --export # also copy PDF + .tex to the export dir
    python scripts/render.py --check              # only validate resume.yaml
    python scripts/render.py JOB-001 --lang de    # same template and layout, in another resume language

Every render also lists the bullets out of the subset's length range (set under Layout; 70-100% of a
line by default): the ones longer than the most (with how full the text is: 112% = 12% more than one
line) and the ones shorter than the least, and the entries that lack text in the rendered language.

Options: --data PATH (default: $RESUME_STUDIO_DATA, else the file the window last opened, else <skill>/resume.yaml)
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
    ap.add_argument("subsets", nargs="*")
    ap.add_argument("--data")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lang", help="render in this resume language instead of the subset's own (any code in settings.languages)")
    args = ap.parse_args()
    if args.lang and args.export:
        ap.error("--lang only checks; to export in another language, set that subset's lang")

    studio = Studio(resolve_data(args.data))
    if not studio.store.exists():
        print(f"data file not found: {studio.store.path}", file=sys.stderr)
        return 2
    doc, _ = studio.store.load()
    warnings = model.validate(doc)
    for w in warnings:
        print(f"warning: {w}")
    if args.check:
        return 1 if warnings else 0

    ids = args.subsets or [str(v.get("id")) for v in doc.get("subsets") or []]
    results, worst = [], 0
    for vid in ids:
        r = studio.export(vid) if args.export else studio.render(vid, lang=args.lang, images=False)
        results.append(r)
        if not r["ok"]:
            worst = 1
            print(f"{vid}: FAILED")
            for e in r["errors"]:
                print(f"    {e}")
            continue
        if vid == model.ALL_ID:
            flag = f"full set, {r['pages']} page(s)"
        else:
            flag = "OK" if r["pages"] == 1 else f"OVER ONE PAGE ({r['pages']} pages)"
        print(f"{vid} [{r['lang']}]: {flag}  {r['seconds']}s  {r['pdf']}")
        report = studio.bullet_report(r, short=True)
        long = [b for b in report if not b["short"]]
        short = [b for b in report if b["short"]]
        r["long_bullets"], r["short_bullets"] = long, short
        lo, hi = (round(x * 100) for x in r.get("range") or (model.LINE_MIN, model.LINE_MAX))
        if long:
            print(f"    {len(long)} bullet(s) longer than the most allowed, {hi}% of a line (shorten only these, and only "
                  f"to just under {hi}%; index = position in the entry's bullets):")
            for b in long:
                print(f"    - {b['id']} \"{b['title']}\" bullet {b['index']}: {b['lines']} line(s), "
                      f"text is {round(b['fill'] * 100)}% of one line: {b['text']}")
        if short:
            print(f"    {len(short)} bullet(s) shorter than the least allowed, {lo}% of a line (lengthen with "
                  "substance from notes / evidence, or merge two related ones; never pad):")
            for b in short:
                print(f"    - {b['id']} \"{b['title']}\" bullet {b['index']}: {b['lines']} line(s), "
                      f"text is {round(b['fill'] * 100)}% of one line: {b['text']}")
        if not long and not short:
            print(f"    every bullet is within {lo}-{hi}% of a line (the range set under Layout)")
        for m in r.get("missing") or []:
            parts = list(m.get("fields") or []) + ([f"bullets {m['bullets']}"] if m.get("bullets") else [])
            print(f"    missing {r['lang']} text (falls back to another language): {m['id']} {', '.join(parts)}")
        if r.get("exported"):
            for k, p in r["exported"].items():
                print(f"    exported {k}: {p}")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return worst


if __name__ == "__main__":
    sys.exit(main())
