"""Drive Resume Studio's browser from Claude Code (the window must be open).

    python scripts/browser.py open https://www.linkedin.com/in/me/
    python scripts/browser.py snapshot                 # the whole page, with a [ref] on every control
    python scripts/browser.py snapshot --dialog        # only the open dialog
    python scripts/browser.py click 12
    python scripts/browser.py type 13 --text "AI Agent Engineer"
    python scripts/browser.py type 14 --stdin <<'EOF'  # long or multi-line text
    python scripts/browser.py select 15 "March"
    python scripts/browser.py press Enter
    python scripts/browser.py screenshot               # prints the PNG's path (open it with Read)
    python scripts/browser.py extract --stdin <<'JS' ... JS  # run a read script on the open page, print its JSON
    python scripts/browser.py route linkedin --stdin <<'JSON' ... JSON   # save the read route and run it once
    python scripts/browser.py sync linkedin             # check for updates now, by the route
    python scripts/browser.py profile linkedin --stdin <<'JSON' ... JSON   # what the site's profile says now
    python scripts/browser.py report linkedin item.i3 done
    python scripts/browser.py sign-in https://careers.example.com/   # a site refuses to sign in a driven browser

Every command prints what happened. The browser is a real Chrome or Edge with its own profile, which
the window starts; this script only talks to the window (standard library only, no venv needed).
Options: --data PATH (default: $RESUME_STUDIO_DATA, else the file the window last opened)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _bootstrap import resolve_data, server_state


def server_url(data: Path) -> str:
    state = server_state(data)
    try:
        return json.loads(state.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError):
        sys.exit(f"Resume Studio isn't open for {data} (no {state}). Open the window first.")


def call(url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except ValueError:
            msg = str(e)
        sys.exit(f"error: {msg}")
    except OSError as e:
        sys.exit(f"error: can't reach Resume Studio at {url} ({e}). Is the window open?")


def read_text(args: argparse.Namespace) -> str:
    if getattr(args, "stdin", False):
        return sys.stdin.buffer.read().decode("utf-8").rstrip("\n")
    if getattr(args, "file", None):
        return Path(args.file).read_text(encoding="utf-8").rstrip("\n")
    if getattr(args, "text", None) is not None:
        return args.text
    sys.exit("error: give the text with --text, --stdin or --file")


def show(r: dict) -> None:
    if "lines" in r:
        print(f"{r.get('title', '')} — {r.get('url', '')}")
        print("\n".join(r["lines"]))
        if r.get("truncated"):
            print("(cut off here: use `snapshot --in <ref>` for one part, or `text`)")
        return
    if "text" in r and len(r) <= 3 and "url" not in r:
        print(r["text"])
        return
    if "tabs" in r:
        for tab in r["tabs"]:
            print(f"{'*' if tab['current'] else ' '} {tab['index']}: {tab['title']} — {tab['url']}")
        return
    parts = [f"ok: {r.get('title', '')} — {r.get('url', '')}"]
    if r.get("target"):
        parts.append("target: " + r["target"])
    if "value" in r:
        parts.append("the field now holds: " + json.dumps(r["value"], ensure_ascii=False))
    if r.get("path"):
        parts.append("saved: " + r["path"])
    if r.get("text"):
        parts.append(r["text"])
    if r.get("note"):
        parts.append("note: " + r["note"])
    print("\n".join(parts))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open"); p.add_argument("url"); p.add_argument("--new-tab", action="store_true")
    p = sub.add_parser("snapshot"); p.add_argument("--dialog", action="store_true"); p.add_argument("--in", dest="within"); p.add_argument("--max", type=int)
    sub.add_parser("text")
    p = sub.add_parser("click"); p.add_argument("ref")
    p = sub.add_parser("hover", help="put the mouse over an element (menus and buttons that show on hover)"); p.add_argument("ref")
    p = sub.add_parser("type"); p.add_argument("ref"); p.add_argument("--text"); p.add_argument("--stdin", action="store_true")
    p.add_argument("--file"); p.add_argument("--append", action="store_true", help="keep what the field holds and add to it")
    p = sub.add_parser("select"); p.add_argument("ref"); p.add_argument("option")
    p = sub.add_parser("press"); p.add_argument("key")
    p = sub.add_parser("scroll"); p.add_argument("ref", nargs="?"); p.add_argument("--down", type=int, default=600)
    p = sub.add_parser("upload"); p.add_argument("ref"); p.add_argument("files", nargs="+")
    p = sub.add_parser("screenshot"); p.add_argument("--full", action="store_true")
    sub.add_parser("tabs")
    p = sub.add_parser("tab"); p.add_argument("index", type=int)
    sub.add_parser("back")
    sub.add_parser("front")
    p = sub.add_parser("sign-in", help="reopen the browser without remote control, for the user to sign in"); p.add_argument("url")
    p = sub.add_parser("extract", help="run a read script on the open page"); p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("route", help="save a site's read route and run it once"); p.add_argument("site")
    p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("sync", help="check for updates now, by the route"); p.add_argument("site")
    p = sub.add_parser("profile", help="send what the site's profile says now"); p.add_argument("site")
    p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("report", help="how one approved change went"); p.add_argument("site"); p.add_argument("id")
    p.add_argument("state", choices=["doing", "done", "failed", "skipped"]); p.add_argument("--note", default="")
    p = sub.add_parser("signed-out", help="the site wants the user to sign in"); p.add_argument("site")
    args = ap.parse_args()

    url = server_url(resolve_data(args.data))
    c = args.cmd
    if c in ("profile", "route"):
        raw = read_text(args)
        try:
            data = json.loads(raw)
        except ValueError as e:
            sys.exit(f"error: that isn't valid JSON ({e})")
        show(call(url, "POST", f"/api/autofill/{args.site}/{'current' if c == 'profile' else 'route'}", data))
        return 0
    if c == "sync":
        show(call(url, "POST", f"/api/autofill/{args.site}/sync", {}))
        return 0
    if c == "extract":
        r = call(url, "POST", "/api/browser/do", {"cmd": "extract", "script": read_text(args)})
        out = json.dumps(r.get("data"), ensure_ascii=False, indent=1)
        print(out if len(out) <= 30000 else out[:30000] + "\n(cut off: the script's answer is longer)")
        return 0
    if c == "report":
        show(call(url, "POST", f"/api/autofill/{args.site}/report", {"id": args.id, "state": args.state, "note": args.note}))
        return 0
    if c == "signed-out":
        show(call(url, "POST", f"/api/autofill/{args.site}/signed-out", {}))
        return 0
    payload: dict = {"cmd": c}
    if c == "open":
        payload.update(url=args.url, new_tab=args.new_tab)
    elif c == "sign-in":
        payload["url"] = args.url
    elif c == "snapshot":
        payload.update(dialog=args.dialog, within=args.within, max=args.max)
    elif c in ("click", "hover", "select", "type", "upload", "scroll"):
        payload["ref"] = args.ref
    if c == "type":
        payload.update(text=read_text(args), append=args.append)
    elif c == "select":
        payload["option"] = args.option
    elif c == "press":
        payload["key"] = args.key
    elif c == "scroll":
        payload["dy"] = args.down
    elif c == "upload":
        payload["files"] = [str(Path(f).resolve()) for f in args.files]
    elif c == "screenshot":
        payload["full"] = args.full
    elif c == "tab":
        payload["index"] = args.index
    show(call(url, "POST", "/api/browser/do", payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
