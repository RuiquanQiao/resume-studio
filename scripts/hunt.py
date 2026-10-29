"""Work the job-hunt tracker from Claude Code (the window must be open): companies, their roles and progress.

    python scripts/hunt.py list [--status interview] [--company 星河科技]
    python scripts/hunt.py show 星河科技                      # a company: its details, about, roles, recent events
    python scripts/hunt.py show 星河科技/服务端              # one application (or its id: A3)
    python scripts/hunt.py add-company "星河科技" --site https://careers.xinghe.example/campus --alias Xinghe
    python scripts/hunt.py edit-company 星河科技 --subset SUB-002 --industry 电商
    python scripts/hunt.py fill 星河科技 --stdin <<'JSON'   # Claude's research, every field at once (see `fill --help`)
    python scripts/hunt.py add-role 星河科技 "服务端研发工程师" [--url ...] [--channel site|platform|referral|other]
                                       [--platform boss] [--order 2] [--status saved] [--apply-by 2026-10-31] [--batch 27秋招]
                                       [--place 南京 --place 上海 --place "remote: 中国大陆" --place "hybrid: 悉尼"]
    python scripts/hunt.py edit-role 星河科技/服务端 [--role ...] [--url ...] [--referral <内推码>] [--order 1] [--note ...] [--apply-by ...] [--batch ...]
                                       [--place ... (the whole list, again) | --no-places]
    python scripts/hunt.py status 星河科技/服务端 interview --note "一面 周三 14:00" [--on 2026-10-18]
    python scripts/hunt.py note 星河科技/服务端 "笔试已交" [--on 2026-10-18]
    python scripts/hunt.py jd 星河科技/服务端 --stdin <<'JD'   # the posting's full text (JD), kept on the role
    python scripts/hunt.py book 星河科技/服务端 一面 --at "2026-10-25 14:00" [--minutes 45] [--where 腾讯会议 123] [--note ..]
    python scripts/hunt.py rebook 星河科技/服务端 E2 [--title 二面] [--at ..] [--minutes ..] [--where ..] [--note ..]
    python scripts/hunt.py unbook 星河科技/服务端 E2        # (or its title: 二面)
    python scripts/hunt.py ics [星河科技/服务端]             # everything booked (apply-by dates too) as an .ics file
    python scripts/hunt.py where                            # every place in the tracker and where the map puts it
    python scripts/hunt.py pin "滨江" 30.21 120.21 | pin "滨江" --unpin   # put a place the map can't find (or got wrong)
    python scripts/hunt.py about 星河科技 --stdin <<'JSON'  {"summary": "...", "points": [{"topic": "...", "text": "...",
                                                       "sources": ["https://..."]}], "read_at": "2026-10-16"}  JSON
    python scripts/hunt.py logo 星河科技                      # look at its logo as the window shows it (a png to view)
    python scripts/hunt.py sent 星河科技                      # the sign-up on its site went out (every role not sent yet)
    python scripts/hunt.py sent 星河科技/服务端 [--subset SUB-002]
    python scripts/hunt.py scan [星河科技 ...]                # read the companies' sites for open postings now
    python scripts/hunt.py scan-route 星河科技 --stdin <<'JSON' ... JSON   # record how to read its job list, run it once
    python scripts/hunt.py postings 星河科技 --stdin [--partial]   # the postings you read yourself (no route possible)
    python scripts/hunt.py apply P12                        # the user goes for posting P12: it becomes an application
    python scripts/hunt.py keywords "算法, AI, Agent, 后端"  # the roles the user looks for (others are folded away)

When to apply by is an event of the role, not a field, and only what the posting says: --apply-by 2026-10-31
gives the date it closes, --apply-by rolling says it closes once filled (招满即止, only when the posting says so),
and --apply-by "" drops it. No date known is no event: never guess one. --batch is the user's own name for the
hiring round.

Statuses: saved (want to apply), applied, assessment (测评/笔试), interview (rounds are booked: `book`), offer, hired,
rejected, withdrawn; Chinese words work too (一面 → interview, 笔试 → assessment, 挂 → rejected).
Every command prints what is there now. Options: --data PATH (default: $RESUME_STUDIO_DATA, else the
file the window last opened). Standard library only, no venv needed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from _bootstrap import resolve_data, server_state

CHANNELS = {"site": "its own site", "platform": "via {platform}", "referral": "referral", "other": "other"}
# built-in events keep no title (they read in the window's language): their names here
EVENT_NAMES = {"apply": "Apply by", "test": "Written test (笔试)", "assessment": "Assessment (测评)", "round1": "1st interview (一面)",
               "round2": "2nd interview (二面)", "round3": "3rd interview (三面)", "hr": "HR interview (HR 面)", "final": "Final interview (终面)"}


def event_name(e: dict) -> str:
    return e.get("title") or EVENT_NAMES.get(e.get("kind") or "", "")


def apply_when(e: dict) -> str:
    return e["at"] or ("until filled (招满即止)" if e.get("rolling") else "no date known")
FILL_HELP = """Claude's research on a company, every field at once, as JSON on stdin:

  {"name": "Xinghe Technology Ltd.",                      # its official name (kept as an alias; the user's name stays)
   "aliases": ["星河科技", "Xinghe", "Nebula"],
   "industry": "E-commerce / internet",              # industry, strength, size and about: in the language `show`
                                                     # names ("write … in"), whatever language the chat is in
   "strength": "Market cap about US$160 bn (NASDAQ: XHT, 2026-10-17)",   # listed: market cap, exchange, ticker,
                                                     # date; not listed: valuation, latest round or headcount, with the date
   "hq": "Shanghai",                                 # the city of its headquarters: a city's name, nothing more
   "places": ["Shanghai", "北京", "深圳", "remote: 中国大陆"],    # every city it hires in (`add-role --help` has the forms)
                                                     # cities and regions are looked up in the list of places, by a
                                                     # name in any language; one it doesn't know is looked up on
                                                     # OpenStreetMap, else kept as written
   "size": "About 23,000 people (2025 annual report)",
   "sites": [{"url": "https://careers.xinghe.example/campus", "kind": "campus", "system": "own site"},
             {"url": "https://careers.xinghe.example/intern", "kind": "intern", "system": "own site"}],
   "logo_url": "https://.../logo.png",                # downloaded once into the data folder
   "sources": {"strength": "https://...", "industry": "https://...", "hq": "https://...", "size": "https://..."},
   "about": {"summary": "...", "points": [{"topic": "...", "text": "...", "sources": ["https://..."]}], "read_at": "2026-10-18"}}

Every key is optional; send what you found. Fields the user edited in the window are theirs and are left as
they are (the reply says which). site kinds: campus, intern, social, other."""


LOOK = ("look at the logo as the window shows it (on white): {path}\n"
        "  if it is blank, someone else's icon, cut off or a blur, send another logo_url with `fill`. Leave the png: "
        "Resume Studio clears these itself.")


def server_url(data: Path) -> str:
    state = server_state(data)
    try:
        return json.loads(state.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError):
        sys.exit(f"Resume Studio isn't open for {data} (no {state}). Open the window first.")


SCAN_WAIT = 3600   # seconds: a route has at most 20 pages of 2 minutes each, and several companies may be scanned


def call(url: str, cmd: str, args: dict, timeout: float = 120) -> dict:
    data = json.dumps({"cmd": cmd, "args": args}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/api/hunt/do", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except ValueError:
            msg = str(e)
        sys.exit(f"error: {msg}")
    except TimeoutError:
        sys.exit(f"error: no answer within {int(timeout)} s; Resume Studio may still be working on it (`show` the company to see)")
    except OSError as e:
        sys.exit(f"error: can't reach Resume Studio at {url} ({e}). Is the window open?")


def place_text(p: dict) -> str:
    """A place as the tracker keeps it: one from the list of places by its English name, one it doesn't know as
    written, marked so (it reads the same in every interface language, and only a pin puts it on the map)."""
    area = p["area"] + ("" if p.get("id") else " [not in the list of places]")
    return {"remote": f"remote: {area}", "hybrid": f"{area} (hybrid)"}.get(p.get("kind") or "onsite", area)


def app_line(a: dict, company: str = "") -> str:
    ch = a.get("channel") or "site"
    how = CHANNELS.get(ch, ch).format(platform=a.get("platform") or "a platform")
    if ch == "site" and a.get("order"):
        how += f", choice {a['order']}"
    parts = [f"{a['id']:<5} {(company + ' / ') if company else ''}{a['role']}", f"[{a['status']}]", f"({how})"]
    if a.get("sent"):
        s = a["sent"][-1]
        parts.append(f"sent {s['at'][:10]}: {s['subset']} v{s['n']}")
    if a.get("places"):
        parts.append("in " + "; ".join(place_text(x) for x in a["places"]))
    if a.get("batch"):
        parts.append(f"batch {a['batch']}")
    due = next((e for e in a.get("schedule") or [] if e.get("kind") == "apply"), None)
    if due and a.get("status") == "saved":
        parts.append(f"apply by {apply_when(due)}")
    soon = [e for e in a.get("schedule") or [] if e["at"] and e.get("kind") != "apply" and e["at"][:10] >= time.strftime("%Y-%m-%d")]
    if soon:
        parts.append(f"next: {event_name(soon[0])} {soon[0]['at']}")
    if a.get("referral"):
        parts.append(f"referral code {a['referral']}")
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
    r = c.get("research") or {}
    if r.get("lang"):
        place = r.get("place_name") or ""
        why = {"applying": f"the user applies in {place}", "hq": f"its headquarters is in {place}",
               "fallback": f"{place} speaks none of the resume's languages",
               "unknown": "where it is isn't known yet: once you know its headquarters, the language there if the "
                          "resume has it, else this"}.get(r.get("why") or "", "")
        print(f"  write industry, strength, size and about in: {r.get('lang_name') or r['lang']} ({r['lang']}; {why})"
              + (f"\n  what is there now is in {r.get('rewrite_name') or r['rewrite']}: write it again in "
                 f"{r.get('lang_name') or r['lang']} (same facts and sources) and send it with `fill`" if r.get("rewrite") else ""))
    src = c.get("sources") or {}
    kept = set(c.get("kept") or [])
    if c.get("places"):
        print("  hires in: " + "; ".join(place_text(x) for x in c["places"]) + ("  [" + src["places"] + "]" if src.get("places") else "")
              + ("  (the user's: fill leaves it)" if "places" in kept else ""))
    for k, label in (("industry", "industry"), ("strength", "strength"), ("hq", "headquarters"), ("size", "size"),
                     ("site", "careers site"), ("account", "account"), ("subset", "filled from")):
        if c.get(k):
            value = place_text(c[k]) if k == "hq" else c[k]
            print(f"  {label}: {value}" + (f" ({c.get('subset_label')})" if k == "subset" and c.get("subset_label") else "")
                  + (f"  [{src[k]}]" if src.get(k) else "") + ("  (the user's: fill leaves it)" if k in kept else ""))
    for x in c.get("sites") or []:
        print(f"  site ({x['kind']}{', ' + x['system'] if x.get('system') else ''}): {x['url']}")
    print("  logo: " + ("saved" if c.get("logo") else "none yet") + ("  (the user's)" if "logo" in kept else ""))
    missing = [k for k in ("industry", "strength", "hq", "site", "logo") if not c.get(k)]
    if missing:
        print("  not filled yet: " + ", ".join(missing) + " (research it and send them with `fill`)")
    if c.get("note"):
        print("  notes: " + c["note"].replace("\n", "\n         "))
    ab = c.get("about")
    if ab:
        print(f"  about (read {ab.get('read_at', '?')}): {ab.get('summary') or ''}")
        for p in ab.get("points") or []:
            print(f"    - {p.get('topic') + ': ' if p.get('topic') else ''}{p['text']}" + (f"  [{', '.join(p['sources'])}]" if p.get("sources") else ""))
    else:
        print("  about: nothing yet (research it and send it with `fill`)")
    print(f"  roles ({len(v['applications'])}):" if v["applications"] else "  roles: none yet")
    for a in v["applications"]:
        print("    " + app_line(a))
    if v["events"]:
        print("  recent events:")
        for e in v["events"][-12:]:
            print("  " + event_line(e))
    sc = v.get("scan") or {}
    ps = [p for p in v.get("postings") or [] if not p.get("closed")]
    kw = v.get("keywords") or []
    if sc.get("at"):
        print(f"  last scan {sc['at'][:16].replace('T', ' ')} by {sc.get('how') or '?'}: "
              + (f"{sc.get('found', 0)} found" if sc.get("ok") else f"failed: {sc.get('error')}")
              + ("; the route is recorded" if sc.get("recorded") else ""))
    if sc.get("needs") == "claude":
        print("  a careers site has no read route yet: record one with `scan-route` (see the instructions)")
    elif sc.get("needs") == "sites":
        print("  no careers site known yet: find them and send them with `fill` (sites)")
    if ps:
        mine = [p for p in ps if not kw or any(k.casefold() in p["title"].casefold() for k in kw)]
        print(f"  open postings: {len(ps)}" + (f", {len(mine)} matching the user's keywords ({', '.join(kw)})" if kw else "") + ":")
        for p in mine[:60]:
            print(f"    {p['id']:<6} {p['title']}" + (f" · {p['location']}" if p.get("location") else "")
                  + (f" · {p['batch']}" if p.get("batch") else "") + (f" · closes {p['deadline']}" if p.get("deadline") else "")
                  + (f" · applied as {p['app']}" if p.get("app") else ""))
        if len(mine) > 60:
            print(f"    … and {len(mine) - 60} more")


def show_app(v: dict) -> None:
    a, c = v["application"], v["company"]
    print(app_line(a, c["name"]))
    if a.get("note"):
        print("  notes: " + a["note"].replace("\n", "\n         "))
    if a.get("jd"):
        print("  JD:\n    " + a["jd"].replace("\n", "\n    "))
    else:
        print("  JD: not kept yet (`jd` saves the posting's text)")
    for s in a.get("sent") or []:
        print(f"  sent {s['at'][:16].replace('T', ' ')}: {s['subset']} v{s['n']}")
    print("  booked:" if a.get("schedule") else "  booked: nothing")
    for e in a.get("schedule") or []:
        when = apply_when(e) if e.get("kind") == "apply" else e["at"]
        print(f"    {e['id']:<4} {when}  {event_name(e)}" + (" [when to apply by]" if e.get("kind") == "apply" else "")
              + (f" ({e['minutes']} min)" if e.get("minutes") else "")
              + (f" @ {e['where']}" if e.get("where") else "") + (f": {e['note']}" if e.get("note") else ""))
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
        else:
            p.add_argument("--different", action="store_true", help="its name is close to one on the list, but it is another company")
        for opt in ("site", "industry", "strength", "hq", "size", "account", "subset", "note"):
            p.add_argument("--" + opt, *(["--location"] if opt == "hq" else []),
                           **({"help": "the city of its headquarters"} if opt == "hq" else {}))
        p.add_argument("--alias", action="append", help="another name it goes by (repeat for more)")
        p.add_argument("--place", action="append", help='a city it hires in, or "remote: <region>" (repeat; the whole list)')
    for name in ("add-role", "edit-role"):
        p = sub.add_parser(name)
        if name == "add-role":
            p.add_argument("company"); p.add_argument("role")
        else:
            p.add_argument("ref"); p.add_argument("--role")
        for opt in ("url", "referral", "channel", "platform", "order", "note", "subset", "apply-by", "batch") + (("status",) if name == "add-role" else ()):
            p.add_argument("--" + opt)
        p.add_argument("--place", action="append", help='where it is: a city, "remote: <region it is open to>" or "hybrid: <city>" '
                       "(repeat for each; on edit-role the list replaces the old one)")
        if name == "edit-role":
            p.add_argument("--no-places", action="store_true", help="it no longer says where")
    p = sub.add_parser("status"); p.add_argument("ref"); p.add_argument("status"); p.add_argument("--note", default="")
    p.add_argument("--on", default="")
    p = sub.add_parser("note"); p.add_argument("ref"); p.add_argument("text"); p.add_argument("--on", default="")
    for name in ("book", "rebook"):
        p = sub.add_parser(name); p.add_argument("ref")
        if name == "book":
            p.add_argument("title"); p.add_argument("--at", required=True)
        else:
            p.add_argument("event"); p.add_argument("--title"); p.add_argument("--at")
        for opt in ("minutes", "where", "note"):
            p.add_argument("--" + opt)
    p = sub.add_parser("unbook"); p.add_argument("ref"); p.add_argument("event")
    p = sub.add_parser("ics"); p.add_argument("ref", nargs="?", default="")
    p = sub.add_parser("where")
    p = sub.add_parser("pin"); p.add_argument("area"); p.add_argument("lat", nargs="?", type=float); p.add_argument("lon", nargs="?", type=float)
    p.add_argument("--unpin", action="store_true", help="back to where the city list puts it")
    p = sub.add_parser("jd"); p.add_argument("ref"); p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("logo", description="Look at a company's logo as the window shows it: a png drawing of it, and what is wrong with it.")
    p.add_argument("ref")
    p = sub.add_parser("about"); p.add_argument("ref"); p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("fill", description=FILL_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ref"); p.add_argument("--stdin", action="store_true"); p.add_argument("--file")
    p = sub.add_parser("sent"); p.add_argument("ref"); p.add_argument("--subset")
    p = sub.add_parser("scan"); p.add_argument("companies", nargs="*")
    p = sub.add_parser("scan-route"); p.add_argument("ref"); p.add_argument("--file"); p.add_argument("--stdin", action="store_true")
    p = sub.add_parser("postings"); p.add_argument("ref"); p.add_argument("--file"); p.add_argument("--stdin", action="store_true")
    p.add_argument("--partial", action="store_true", help="you read only some of the lists: nothing missing is closed")
    p = sub.add_parser("apply"); p.add_argument("ref")
    p = sub.add_parser("keywords"); p.add_argument("keywords")
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
        a = pick("site", "industry", "strength", "hq", "size", "account", "subset", "note")
        if c == "add-company":
            a.update(name=args.name, different=args.different)
        else:
            a.update(ref=args.ref, **pick("name"))
        if args.alias:
            a["aliases"] = args.alias
        if args.place:
            a["places"] = args.place
        r = call(url, c, a)
        print(r["text"] + ":")
        show_company(r["company"])
        return 0
    if c in ("add-role", "edit-role"):
        a = pick("url", "referral", "channel", "platform", "order", "note", "subset", "status", "batch")
        if args.apply_by is not None:   # a date, "rolling" (招满即止), or "" to drop it: not known
            a["apply_by"] = args.apply_by
        if args.place or getattr(args, "no_places", False):
            a["places"] = args.place or []
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
    elif c in ("book", "rebook"):
        r = call(url, c, {"ref": args.ref, **({"title": args.title} if c == "book" else {"event": args.event, **pick("title")}),
                          **pick("at", "minutes", "where", "note")})
    elif c == "unbook":
        r = call(url, c, {"ref": args.ref, "event": args.event})
    elif c == "where":
        r = call(url, c, {})
        for pid, w in sorted((r.get("places") or {}).items(), key=lambda x: x[1]["name"]):
            print(f"  {w['name']} ({pid}{', ' + w['detail'] if w.get('detail') else ''})"
                  + (f": {w['lat']}, {w['lon']}" + (" (pinned)" if w.get("pinned") else "") if w.get("lat") is not None else ""))
        for area, w in sorted(r["geo"].items()):
            print(f"  {area} [not in the list of places]: " + (f"{w['lat']}, {w['lon']} ({'pinned' if w.get('pinned') else w['name'] + ', ' + w['country']})" if w
                                   else "not on the map: pin it (hunt.py pin)"))
        return 0
    elif c == "pin":
        if not args.unpin and (args.lat is None or args.lon is None):
            sys.exit("error: give its latitude and longitude, or --unpin")
        r = call(url, c, {"area": args.area, "lat": None if args.unpin else args.lat, "lon": None if args.unpin else args.lon})
        w = r["where"]
        print(f"{args.area}: " + (f"{w['lat']}, {w['lon']}" + (" (pinned)" if w.get("pinned") else f" ({w.get('name')})") if w else "not found"))
        return 0
    elif c == "ics":
        r = call(url, c, {"ref": args.ref})
        print(f"written: {r['path']} (opening it imports it into the user's calendar app)")
        return 0
    elif c == "jd":
        raw = sys.stdin.buffer.read().decode("utf-8-sig") if args.stdin else Path(args.file).read_text(encoding="utf-8-sig") if args.file else ""
        if not raw.strip():
            sys.exit("error: give the JD's text with --stdin or --file")
        r = call(url, "edit-role", {"ref": args.ref, "jd": raw})
    elif c in ("about", "fill"):
        raw = sys.stdin.buffer.read().decode("utf-8-sig") if args.stdin else Path(args.file).read_text(encoding="utf-8-sig") if args.file else ""
        try:
            got = json.loads(raw)
        except ValueError as e:
            sys.exit(f"error: that isn't valid JSON ({e})")
        r = call(url, c, {"ref": args.ref, ("about" if c == "about" else "fields"): got})
        if r.get("kept"):
            print("left as the user set them: " + ", ".join(r["kept"]))
        if r.get("logo_error"):
            print("logo not saved: " + r["logo_error"])
        if r.get("lang_error"):
            print("language: " + r["lang_error"])
        print("saved:")
        show_company(r["company"])
        if r.get("logo_look"):
            print(LOOK.format(path=r["logo_look"]))
        return 0
    elif c == "logo":
        r = call(url, "logo", {"ref": args.ref})
        if not r["logo"]:
            print(f"{r['company']}: no logo yet. Find its real logo on the web and send it as logo_url with `fill`.")
            return 0
        if r.get("problem"):
            print(f"{r['company']}: its logo won't do: {r['problem']}. Find another picture and send it as logo_url with `fill`.")
        elif not r.get("checked"):
            print(f"{r['company']}: not checked (the window isn't open). The file: {r['file']}")
        if r.get("look"):
            print(LOOK.format(path=r["look"]))
        return 0
    elif c in ("scan", "scan-route", "postings"):
        # a scan answers once every page is read: a slow site's route takes minutes (up to 2 per page), and giving
        # up sooner left it running in the window, so a retry read "already being scanned" as a stuck scan
        if c in ("scan", "scan-route"):
            print("scanning: a slow site can take several minutes; this waits for the answer", flush=True)
        if c == "scan":
            r = call(url, "scan", {"companies": args.companies}, timeout=SCAN_WAIT)
        else:
            raw = sys.stdin.buffer.read().decode("utf-8-sig") if args.stdin else Path(args.file).read_text(encoding="utf-8-sig") if args.file else ""
            try:
                got = json.loads(raw)
            except ValueError as e:
                sys.exit(f"error: that isn't valid JSON ({e})")
            if c == "scan-route":
                r = call(url, c, {"ref": args.ref, "route": got}, timeout=SCAN_WAIT)
            else:
                rows = got.get("postings") if isinstance(got, dict) else got
                r = call(url, c, {"ref": args.ref, "postings": rows, "partial": args.partial})
        for x in r["scanned"]:
            if x.get("busy"):
                print(f"{x['company']}: still being scanned ({x.get('since', 0)} s so far), not stuck: its result shows "
                      "on the company once it ends. Wait and `show` it rather than scanning again or restarting")
            elif x.get("error"):
                print(f"{x['company']}: failed: {x['error']}" + (" (signed out: the user signs in with the button on its row)" if x.get("signed_out") else ""))
            elif "found" in x:
                print(f"{x['company']}: {x['found']} open, {x['new']} new, {x['closed']} closed, {x['changed']} changed"
                      + (f"; still needs: {x['needs']}" if x.get("needs") else ""))
            else:
                print(f"{x['company']}: needs {x.get('needs') or 'claude'} (no site could be read without you)")
        if r.get("company"):
            show_company(r["company"])
        return 0
    elif c == "apply":
        r = call(url, "apply", {"ref": args.ref})
    elif c == "keywords":
        r = call(url, "keywords", {"keywords": args.keywords})
        print("keywords: " + (", ".join(r["settings"]["keywords"]) or "none (every posting counts)"))
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
