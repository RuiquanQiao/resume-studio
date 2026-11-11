"""The plugins' records from Claude Code (docs/PLUGINS.md). The window needn't be open: the records are a file in
the data folder, and an open window shows every change by itself.

    python scripts/plugin.py list                                    # the plugins here, on or off, and their collections
    python scripts/plugin.py show civil-service [positions] [--id R3]
    python scripts/plugin.py add civil-service positions --stdin <<'JSON'
    {"title": "…", "exam": "national", "apply_by": "2026-10-24", "notice": "https://…"}
    JSON
    python scripts/plugin.py edit civil-service positions R3 --stdin <<'JSON'
    {"stage": "written"}
    JSON
    python scripts/plugin.py delete civil-service positions R3
    python scripts/plugin.py check path/to/plugin-folder             # for plugin developers: does it pass?

Options: --data PATH (default: $RESUME_STUDIO_DATA, else the file the window last opened).
Standard library only, no venv needed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import PREFS_DIR, guard_args, read_input, resolve_data

from core import i18n
from core.store import data_dirs
from plugins import manifest
from plugins.records import Records
from plugins.registry import Registry


def out(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("show"); p.add_argument("plugin"); p.add_argument("collection", nargs="?"); p.add_argument("--id")
    for name in ("add", "edit"):
        p = sub.add_parser(name); p.add_argument("plugin"); p.add_argument("collection")
        if name == "edit":
            p.add_argument("id")
        p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("delete"); p.add_argument("plugin"); p.add_argument("collection"); p.add_argument("id")
    p = sub.add_parser("check"); p.add_argument("folder")
    args = ap.parse_args()
    guard_args()

    if args.cmd == "check":
        try:
            m = manifest.read(Path(args.folder))
        except manifest.ManifestError as e:
            sys.exit(f"error: {e}")
        print(f"ok: {m['id']} {m['version']}, {len(m['collections'])} collection(s)")
        return 0

    prefs = i18n.Prefs(PREFS_DIR)
    i18n.set_lang(prefs.ui_lang())
    reg = Registry(PREFS_DIR / "plugins", prefs)
    state = data_dirs(resolve_data(args.data))[0]

    if args.cmd == "list":
        for p in reg.all():
            if "error" in p:
                print(f"{p['id']}  ({p['source']}) doesn't load: {p['error']}")
                continue
            m = p["manifest"]
            print(f"{m['id']}  {manifest.local(m['name'], 'en')} / {manifest.local(m['name'], 'zh')}  v{m['version']}  "
                  f"{'on' if p['on'] else 'off'}  ({p['source']})")
            for c in m["collections"]:
                print(f"  {c['id']}: " + ", ".join(f"{x['key']}:{x['type']}" for x in c["properties"]))
        return 0

    try:
        p = reg.get(args.plugin)
    except KeyError as e:
        sys.exit(f"error: {str(e).strip(chr(39))}; `list` shows the plugins here")
    if not p["on"]:
        sys.exit(f"error: the plugin {args.plugin} is off; the user turns it on in Settings → Plugins")
    recs = Records(state, p["manifest"])
    try:
        if args.cmd == "show":
            if args.id:
                if not args.collection:
                    sys.exit("error: --id needs the collection")
                out(recs.get(args.collection, args.id))
            else:
                out(recs.rows(args.collection) if args.collection else recs.all())
        elif args.cmd in ("add", "edit"):
            text = read_input(args)
            if not text.strip():
                sys.exit("error: give the fields as a JSON object (--stdin or --file)")
            try:
                fields = json.loads(text)
            except ValueError as e:
                sys.exit(f"error: the fields aren't valid JSON: {e}")
            row = recs.add(args.collection, fields, by="claude") if args.cmd == "add" \
                else recs.edit(args.collection, args.id, fields, by="claude")
            print(f"{'added' if args.cmd == 'add' else 'changed'} {row['id']}")
            out(row)
        elif args.cmd == "delete":
            row = recs.delete(args.collection, args.id)
            print(f"deleted {row['id']}")
    except KeyError as e:
        sys.exit(f"error: {str(e).strip(chr(39))}")
    except ValueError as e:
        sys.exit(f"error: nothing was saved: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
