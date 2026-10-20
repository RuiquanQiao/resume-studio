"""Read and write the user's plan from Claude Code (docs/PLAN.md). The window needn't be open: the plan is a
file in the data folder, and an open window shows every change by itself.

    python scripts/plan.py show [--version 2]
    python scripts/plan.py write --stdin --why "算法进度比计划慢，先砍掉 CV 方向" <<'MD'   # the whole plan, kept as a version
    python scripts/plan.py note "做了 2 道双指针，搞懂了滑动窗口" [--under 进展] [--on 2026-10-19]
    python scripts/plan.py done "LeetCode hot 100 前 20 题" [--undo]
    python scripts/plan.py versions

A rewrite keeps a version with its reason; a line of progress or a ticked box goes into the latest one.
Options: --data PATH (default: $RESUME_STUDIO_DATA, else the file the window last opened).
Standard library only, no venv needed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bootstrap import PREFS_DIR, resolve_data

from core import i18n
from studyplan.store import Plan


def version_line(v: dict) -> str:
    who = "Claude" if v.get("by") == "claude" else "the user"
    return (f"v{v['n']}  {v['at'][:16].replace('T', ' ')}  by {who}" + (f": {v['why']}" if v.get("why") else "")
            + (f"  (small changes until {v['edited'][:16].replace('T', ' ')})" if v.get("edited") else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show"); p.add_argument("--version", type=int)
    p = sub.add_parser("write"); p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p.add_argument("--why", required=True, help="why the plan changed: kept with the version")
    p = sub.add_parser("note"); p.add_argument("text"); p.add_argument("--under", default=""); p.add_argument("--on", default="")
    p = sub.add_parser("done"); p.add_argument("words"); p.add_argument("--undo", action="store_true")
    sub.add_parser("versions")
    args = ap.parse_args()
    i18n.set_lang(i18n.Prefs(PREFS_DIR).ui_lang())
    data = resolve_data(args.data)
    plan = Plan(data.parent / ".studio")

    try:
        if args.cmd == "show":
            if args.version:
                v = plan.get(args.version)
                print(version_line(v))
                print("-" * 40)
                print(v["text"] or "(empty)", end="")
                return 0
            st = plan.state()
            print(st["text"] or "(the plan is empty)", end="" if st["text"] else "\n")
            if st["versions"]:
                print("-" * 40)
                for v in st["versions"][-5:]:
                    print(version_line(v))
                if not st["kept"]:
                    print("(edited outside since the latest version)")
            return 0
        if args.cmd == "versions":
            vs = plan.state()["versions"]
            print("\n".join(version_line(v) for v in vs) if vs else "no versions yet")
            return 0
        if args.cmd == "write":
            raw = sys.stdin.buffer.read().decode("utf-8") if args.stdin else Path(args.file).read_text(encoding="utf-8") if args.file else ""
            if not raw.strip():
                sys.exit("error: send the whole plan as Markdown (--stdin or --file)")
            r = plan.write(raw, why=args.why, by="claude")
            print(f"kept as v{r['made']['n']}: {r['made']['why']}" if r["made"] else "nothing changed")
            return 0
        if args.cmd == "note":
            r = plan.note(args.text, under=args.under, on=args.on)
        else:
            r = plan.tick_item(args.words, done=not args.undo)
        last = r["versions"][-1] if r["versions"] else None
        print("saved" + (f" (in v{last['n']})" if last and r["kept"] else ""))
        return 0
    except (KeyError, ValueError) as e:
        sys.exit("error: " + str(e).strip("'\""))


if __name__ == "__main__":
    sys.exit(main())
