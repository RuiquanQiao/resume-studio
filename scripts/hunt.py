"""Work the job-hunt tracker from Claude Code (the window must be open): companies, their roles and progress.

    python scripts/hunt.py list [--status interview] [--company 星河科技]
    python scripts/hunt.py show 星河科技                      # a company: its details, about, roles, recent events
    python scripts/hunt.py show 星河科技/服务端              # one application (or its id: A3)
    python scripts/hunt.py add-company "星河科技" --site https://careers.xinghe.example/campus --alias Xinghe
    python scripts/hunt.py edit-company 星河科技 --deadline 2026-10-31 --subset SUB-002 --industry 电商
    python scripts/hunt.py add-role 星河科技 "服务端研发工程师" [--url ...] [--channel site|platform|referral|other]
                                       [--platform boss] [--order 2] [--status saved]
    python scripts/hunt.py edit-role 星河科技/服务端 [--role ...] [--url ...] [--order 1] [--note ...]
    python scripts/hunt.py status 星河科技/服务端 interview --note "一面 周三 14:00" [--on 2026-10-18]
    python scripts/hunt.py note 星河科技/服务端 "笔试已交" [--on 2026-10-18]
    python scripts/hunt.py next 星河科技/服务端 "等二面通知" --due 2026-10-25
    python scripts/hunt.py about 星河科技 --stdin <<'JSON'  {"summary": "...", "points": [{"topic": "...", "text": "...",
                                                       "sources": ["https://..."]}], "read_at": "2026-10-16"}  JSON
    python scripts/hunt.py sent 星河科技                      # the sign-up on its site went out (every role not sent yet)
    python scripts/hunt.py sent 星河科技/服务端 [--subset SUB-002]

Statuses: saved (want to apply), applied, assessment (测评/笔试), interview (rounds are notes), offer, hired,
rejected, withdrawn; Chinese words work too (一面 → interview, 笔试 → assessment, 挂 → rejected).
Every command prints what is there now. Options: --data PATH (default: $RESUME_STUDIO_DATA, else the
file the window last opened). Standard library only, no venv needed.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _bootstrap import resolve_data

CHANNELS = {"site": "its own site", "platform": "via {platform}", "referral": "referral", "other": "other"}


def server_url(data: Path) -> str:
    state = data.parent / ".studio" / "server.json"
    try:
        return json.loads(state.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError):
        sys.exit(f"Resume Studio isn't open for {data} (no {state}). Open the window first.")


def call(url: str, cmd: str, args: dict) -> dict:
    data = json.dumps({"cmd": cmd, "args": args}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/api/hunt/do", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except ValueError:
            msg = str(e)
        sys.exit(f"error: {msg}")
    except OSError as e:
        sys.exit(f"error: can't reach Resume Studio at {url} ({e}). Is the window open?")


def app_line(a: dict, company: str = "") -> str:
    ch = a.get("channel") or "site"
    how = CHANNELS.get(ch, ch).format(platform=a.get("platform") or "a platform")
    if ch == "site" and a.get("order"):
        how += f", choice {a['order']}"
    parts = [f"{a['id']:<5} {(company + ' / ') if company else ''}{a['role']}", f"[{a['status']}]", f"({how})"]
    if a.get("sent"):
        s = a["sent"][-1]
        parts.append(f"sent {s['at'][:10]}: {s['subset']} v{s['n']}")
    if a.get("next"):
        parts.append(f"next: {a['next']}" + (f" (due {a['due']})" if a.get("due") else ""))
    elif a.get("due"):
        parts.append(f"due {a['due']}")
    if a.get("url"):
        parts.append(a["url"])
    return "  ".join(parts)


def event_line(e: dict) -> str:
    what = {"created": f"added as {e['to']}", "status": f"{e['from']} → {e['to']}", "sent": f"sent ({e['from']} → {e['to']})",
            "note": "note"}.get(e["kind"], e["kind"])
    return f"  {e['at'][:16].replace('T', ' ')}  {e['app']:<5} {what}" + (f": {e['note']}" if e.get("note") else "") + f"  [{e['source']}]"


def show_company(v: dict) -> None:
    c = v["company"]
    print(f"{c['id']} {c['name']}" + (f" (also: {', '.join(c['aliases'])})" if c.get("aliases") else ""))
    for k, label in (("site", "careers site"), ("industry", "industry"), ("location", "location"), ("size", "size"),
                     ("deadline", "deadline"), ("account", "account"), ("subset", "filled from")):
        if c.get(k):
            print(f"  {label}: {c[k]}" + (f" ({c.get('subset_label')})" if k == "subset" and c.get("subset_label") else ""))
    if c.get("note"):
        print("  notes: " + c["note"].replace("\n", "\n         "))
    ab = c.get("about")
    if ab:
        print(f"  about (read {ab.get('read_at', '?')}): {ab.get('summary') or ''}")
        for p in ab.get("points") or []:
            print(f"    - {p.get('topic') + ': ' if p.get('topic') else ''}{p['text']}" + (f"  [{', '.join(p['sources'])}]" if p.get("sources") else ""))
    else:
        print("  about: nothing yet (research it and save with `about`)")
    print(f"  roles ({len(v['applications'])}):" if v["applications"] else "  roles: none yet")
    for a in v["applications"]:
        print("    " + app_line(a))
    if v["events"]:
        print("  recent events:")
        for e in v["events"][-12:]:
            print("  " + event_line(e))


def show_app(v: dict) -> None:
    a, c = v["application"], v["company"]
    print(app_line(a, c["name"]))
    if a.get("note"):
        print("  notes: " + a["note"].replace("\n", "\n         "))
    for s in a.get("sent") or []:
        print(f"  sent {s['at'][:16].replace('T', ' ')}: {s['subset']} v{s['n']}")
    print("  events:" if v["events"] else "  events: none")
    for e in v["events"]:
        print(event_line(e))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--status"); p.add_argument("--company")
    p = sub.add_parser("show"); p.add_argument("ref")
    for name in ("add-company", "edit-company"):
        p = sub.add_parser(name); p.add_argument("ref" if name == "edit-company" else "name")
        if name == "edit-company":
            p.add_argument("--name")
        for opt in ("site", "industry", "location", "size", "deadline", "account", "subset", "note"):
            p.add_argument("--" + opt)
        p.add_argument("--alias", action="append", help="another name it goes by (repeat for more)")
    for name in ("add-role", "edit-role"):
        p = sub.add_parser(name)
        if name == "add-role":
            p.add_argument("company"); p.add_argument("role")
        else:
            p.add_argument("ref"); p.add_argument("--role")
        for opt in ("url", "channel", "platform", "order", "note", "subset") + (("status",) if name == "add-role" else ()):
            p.add_argument("--" + opt)
    p = sub.add_parser("status"); p.add_argument("ref"); p.add_argument("status"); p.add_argument("--note", default="")
    p.add_argument("--on", default="")
    p = sub.add_parser("note"); p.add_argument("ref"); p.add_argument("text"); p.add_argument("--on", default="")
    p = sub.add_parser("next"); p.add_argument("ref"); p.add_argument("text"); p.add_argument("--due", default="")
    p = sub.add_parser("about"); p.add_argument("ref"); p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("sent"); p.add_argument("ref"); p.add_argument("--subset")
    args = ap.parse_args()
    url = server_url(resolve_data(args.data))
    c = args.cmd
    pick = lambda *names: {n: getattr(args, n) for n in names if getattr(args, n, None) not in (None, "")}  # noqa: E731

    if c == "list":
        r = call(url, "list", {})["list"]
        names = {x["id"]: x["name"] for x in r["companies"]}
        rows = r["applications"]
        if args.status:
            rows = [a for a in rows if a["status"] == args.status]
        if args.company:
            k = args.company.casefold()
            rows = [a for a in rows if k in names.get(a["company"], "").casefold()]
        if not rows:
            print("no applications" + (" like that" if args.status or args.company else " yet"))
        for a in sorted(rows, key=lambda a: (names.get(a["company"], ""), a.get("order") or 0)):
            print(app_line(a, names.get(a["company"], "")))
        lone = [x for x in r["companies"] if not x["roles"]]
        if lone and not (args.status or args.company):
            print("companies with no roles yet: " + ", ".join(f"{x['name']} ({x['id']})" for x in lone))
        return 0
    if c == "show":
        r = call(url, "show", {"ref": args.ref})
        show_company(r["company"]) if "company" in r else show_app(r["app"])
        return 0
    if c in ("add-company", "edit-company"):
        a = pick("site", "industry", "location", "size", "deadline", "account", "subset", "note")
        if c == "add-company":
            a["name"] = args.name
        else:
            a.update(ref=args.ref, **pick("name"))
        if args.alias:
            a["aliases"] = args.alias
        r = call(url, c, a)
        print(r["text"] + ":")
        show_company(r["company"])
        return 0
    if c in ("add-role", "edit-role"):
        a = pick("url", "channel", "platform", "order", "note", "subset", "status")
        if c == "add-role":
            a.update(company=args.company, role=args.role)
        else:
            a.update(ref=args.ref, **pick("role"))
        r = call(url, c, a)
        print(r["text"] + ":")
        show_app(r["app"])
        return 0
    if c == "status":
        r = call(url, "status", {"ref": args.ref, "status": args.status, "note": args.note, "on": args.on})
    elif c == "note":
        r = call(url, "note", {"ref": args.ref, "note": args.text, "on": args.on})
    elif c == "next":
        r = call(url, "next", {"ref": args.ref, "next": args.text, "due": args.due})
    elif c == "about":
        raw = sys.stdin.buffer.read().decode("utf-8") if args.stdin else Path(args.file).read_text(encoding="utf-8") if args.file else ""
        try:
            about = json.loads(raw)
        except ValueError as e:
            sys.exit(f"error: that isn't valid JSON ({e})")
        r = call(url, "about", {"ref": args.ref, "about": about})
        print("saved:")
        show_company(r["company"])
        return 0
    else:   # sent
        r = call(url, "sent", {"ref": args.ref, **pick("subset")})["sent"]
        print(f"sent with {r['subset']} v{r['n']}: {', '.join(r['sent'])}" + (f"; {r['copied']} copied to career-ops" if r.get("copied") else ""))
        return 0
    print(r["text"] + ":")
    show_app(r["app"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
