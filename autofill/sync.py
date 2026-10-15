"""The local copy of a site's profile, kept in sync with the site like a working copy with its remote.

Three versions of each profile, all in one shape (`{"basics": {...}, "items": [{"id", "section",
"fields"}], "skills": [...]}`):

- `base`: what the site said at the last sync (a read, or a change Claude reported done there);
- `local`: the user's copy, edited in the window;
- a fresh read of the site (by the recorded route, or by Claude).

`merge` brings a fresh read in: what changed only on the site comes into the local copy, what changed
only locally stays to be pushed, and a field changed on both sides is a conflict the user settles.
`pending` is everything the local copy has that the site doesn't (yet): the changes to push.
"""
from __future__ import annotations

import copy
import itertools
from typing import Any

from .profile import label_of, norm_text, same

NAME_FIELD = {"experience": "org", "education": "org", "projects": "name", "awards": "title"}
SECOND_FIELD = {"experience": "title", "education": "degree", "projects": "role", "awards": "issuer"}


def empty() -> dict:
    return {"basics": {}, "items": [], "skills": []}


def clean(p: dict | None) -> dict:
    p = p or {}
    return {"basics": dict(p.get("basics") or {}), "items": [dict(i, fields=dict(i.get("fields") or {})) for i in p.get("items") or []],
            "skills": list(p.get("skills") or [])}


def new_id(*profiles: dict | None) -> str:
    used = {str(i.get("id")) for p in profiles if p for i in p.get("items") or []}
    return next(f"i{n}" for n in itertools.count(1) if f"i{n}" not in used)


def same_fields(a: dict, b: dict) -> bool:
    return all(same(k, str(a.get(k) or ""), str(b.get(k) or "")) for k in set(a) | set(b))


def _likeness(section: str, a: dict, b: dict) -> int:
    """How surely two items are the same entry: 3 identical, 2 same place and role, 1 same place, 0 no."""
    if same_fields(a, b):
        return 3
    nf, sf = NAME_FIELD.get(section), SECOND_FIELD.get(section)
    x, y = norm_text(str(a.get(nf) or "")), norm_text(str(b.get(nf) or ""))
    if not (x and y and (x == y or x in y or y in x)):
        return 0
    if sf and a.get(sf) and b.get(sf) and not same(sf, str(a[sf]), str(b[sf])):
        return 1
    return 2


def match(old: list[dict], new: list[dict]) -> dict[int, int]:
    """new index -> old index: the same entry on both sides (best match first; a weak one only when it is
    the only candidate in its section)."""
    out: dict[int, int] = {}
    taken: set[int] = set()
    for want in (3, 2):
        for j, n in enumerate(new):
            if j in out:
                continue
            for i, o in enumerate(old):
                if i not in taken and o["section"] == n["section"] and _likeness(n["section"], n["fields"], o["fields"]) >= want:
                    out[j] = i
                    taken.add(i)
                    break
    for j, n in enumerate(new):
        if j in out:
            continue
        cands = [i for i, o in enumerate(old) if i not in taken and o["section"] == n["section"]
                 and _likeness(n["section"], n["fields"], o["fields"]) >= 1]
        if len(cands) == 1:
            out[j] = cands[0]
            taken.add(cands[0])
    return out


def _covers(scope: list[str] | None, section: str) -> bool:
    return scope is None or section in scope


def merge(base: dict | None, local: dict | None, remote: dict, scope: list[str] | None = None) -> dict:
    """A fresh read of the site into the local copy. `scope`: the sections the read covered ("basics",
    "skills" and item sections; None = all). Returns {base, local, conflicts, notes, synced}: the new
    base (= the site now) and local copy, field conflicts {"<item id>.<field>" | "basics.<name>": {site,
    local}}, notes for the user, and how many site changes came into the local copy."""
    remote = clean(remote)
    if base is None or local is None:   # the first read: the local copy starts as the site
        b = clean(remote)
        for it in b["items"]:
            it["id"] = new_id(b)
        return {"base": b, "local": copy.deepcopy(b), "conflicts": {}, "notes": [], "synced": 0}
    base, local = clean(base), clean(local)
    conflicts: dict[str, dict] = {}
    notes: list[dict] = []
    synced = 0
    nb, nl = clean(base), clean(local)

    def three(key: str, field: str, b: str, l: str, r: str) -> str:
        nonlocal synced
        if same(field, r, b):
            return l
        if same(field, l, b):
            if r != l:
                synced += 1
            return r
        if same(field, l, r):
            return l
        conflicts[key] = {"site": r, "local": l}
        return l

    # ---- headline, about
    if _covers(scope, "basics"):
        for name in dict.fromkeys([*base["basics"], *remote["basics"], *local["basics"]]):
            b, l, r = (str(x["basics"].get(name) or "") for x in (base, local, remote))
            nl["basics"][name] = three(f"basics.{name}", name, b, l, r)
        nb["basics"] = dict(remote["basics"])

    # ---- items, section by section
    in_scope = [i for i in remote["items"] if _covers(scope, i["section"])]
    old = [i for i in base["items"] if _covers(scope, i["section"])]
    m = match(old, in_scope)
    local_by_id = {i["id"]: i for i in nl["items"]}
    base_ids = {i["id"] for i in base["items"]}
    new_base: list[dict] = [i for i in base["items"] if not _covers(scope, i["section"])]
    seen: set[str] = set()
    for j, r in enumerate(in_scope):
        if j in m:
            b = old[m[j]]
            seen.add(b["id"])
            new_base.append({"id": b["id"], "section": r["section"], "fields": dict(r["fields"])})
            l = local_by_id.get(b["id"])
            if l is None:   # removed here; changed on the site meanwhile: say so, keep it removed
                if not same_fields(r["fields"], b["fields"]):
                    notes.append({"kind": "removed_here_changed_there", "label": label_of(r["section"], r["fields"])})
                continue
            fields = {}
            for f in dict.fromkeys([*l["fields"], *r["fields"], *b["fields"]]):
                fields[f] = three(f"{b['id']}.{f}", f, str(b["fields"].get(f) or ""), str(l["fields"].get(f) or ""),
                                  str(r["fields"].get(f) or ""))
            l["fields"] = {k: v for k, v in fields.items() if v or k in l["fields"] or k in r["fields"]}
        else:
            # new on the site; maybe an item the user added here that is now there too
            added = [x for x in nl["items"] if x["id"] not in base_ids and x["id"] not in seen and x["section"] == r["section"]]
            hit = match(added, [r]).get(0)
            if hit is not None:
                l = added[hit]
                seen.add(l["id"])
                new_base.append({"id": l["id"], "section": r["section"], "fields": dict(r["fields"])})
                continue
            iid = new_id(nb, nl, {"items": new_base})
            new_base.append({"id": iid, "section": r["section"], "fields": dict(r["fields"])})
            nl["items"].append({"id": iid, "section": r["section"], "fields": dict(r["fields"])})
            seen.add(iid)
            synced += 1
    for b in old:
        if b["id"] in seen:
            continue
        # gone from the site
        l = local_by_id.get(b["id"])
        if l is None:
            continue
        if same_fields(l["fields"], b["fields"]):
            nl["items"] = [x for x in nl["items"] if x["id"] != b["id"]]
            synced += 1
        else:   # edited here: kept, and offered to go up again
            notes.append({"kind": "removed_there_changed_here", "label": label_of(b["section"], l["fields"])})
    nb["items"] = new_base
    # the local copy keeps its order; items new on the site go after the others of their section
    order = {s: n for n, s in enumerate(["experience", "education", "projects", "awards"])}
    nl["items"].sort(key=lambda i: order.get(i["section"], 9))

    # ---- skills (a set)
    if _covers(scope, "skills"):
        low = lambda xs: {x.lower() for x in xs}   # noqa: E731
        gone, came = low(base["skills"]) - low(remote["skills"]), [x for x in remote["skills"] if x.lower() not in low(base["skills"])]
        keep = [x for x in local["skills"] if x.lower() not in gone]
        keep += [x for x in came if x.lower() not in low(keep)]
        synced += len(low(local["skills"]) ^ low(keep))
        nl["skills"] = keep
        nb["skills"] = list(remote["skills"])
    return {"base": nb, "local": nl, "conflicts": conflicts, "notes": notes, "synced": synced}


def pending(base: dict | None, local: dict | None) -> list[dict]:
    """What the local copy has that the site doesn't: {id, op: add | update | remove, section, item,
    label, site_label, fields: [{name, from, to}]}."""
    if base is None or local is None:
        return []
    base, local = clean(base), clean(local)
    out: list[dict] = []
    for name in sorted(set(base["basics"]) | set(local["basics"]), key=lambda n: ["headline", "about"].index(n) if n in ("headline", "about") else 9):
        b, l = str(base["basics"].get(name) or ""), str(local["basics"].get(name) or "")
        if not same(name, b, l):
            out.append({"id": f"basics.{name}", "op": "update", "section": "basics", "item": name, "label": name,
                        "fields": [{"name": name, "from": b, "to": l}]})
    by_id = {i["id"]: i for i in base["items"]}
    for l in local["items"]:
        b = by_id.get(l["id"])
        if b is None:
            out.append({"id": f"item.{l['id']}", "op": "add", "section": l["section"], "item": l["id"],
                        "label": label_of(l["section"], l["fields"]),
                        "fields": [{"name": k, "from": "", "to": v} for k, v in l["fields"].items() if v]})
            continue
        diffs = [{"name": k, "from": str(b["fields"].get(k) or ""), "to": str(l["fields"].get(k) or "")}
                 for k in list(dict.fromkeys([*b["fields"], *l["fields"]]))
                 if not same(k, str(b["fields"].get(k) or ""), str(l["fields"].get(k) or ""))]
        if diffs:
            out.append({"id": f"item.{l['id']}", "op": "update", "section": l["section"], "item": l["id"],
                        "label": label_of(l["section"], l["fields"]), "site_label": label_of(b["section"], b["fields"]),
                        "fields": diffs})
    local_ids = {i["id"] for i in local["items"]}
    for b in base["items"]:
        if b["id"] not in local_ids:
            out.append({"id": f"item.{b['id']}", "op": "remove", "section": b["section"], "item": b["id"],
                        "label": label_of(b["section"], b["fields"]),
                        "fields": [{"name": k, "from": v, "to": ""} for k, v in b["fields"].items() if v]})
    low = lambda xs: {x.lower() for x in xs}   # noqa: E731
    add = [x for x in local["skills"] if x.lower() not in low(base["skills"])]
    rem = [x for x in base["skills"] if x.lower() not in low(local["skills"])]
    if add or rem:
        out.append({"id": "skills", "op": "update", "section": "skills", "item": "skills", "label": "skills",
                    "add": add, "remove": rem,
                    "fields": [{"name": "skills", "from": "\n".join(base["skills"]), "to": "\n".join(local["skills"])}]})
    order = {s: n for n, s in enumerate(["basics", "experience", "projects", "education", "awards", "skills"])}
    out.sort(key=lambda x: order.get(x["section"], 9))
    return out


def pushed(base: dict, local: dict, change: dict) -> dict:
    """The base once one pending change is on the site (Claude reported it done)."""
    base, local = clean(base), clean(local)
    if change["section"] == "basics":
        base["basics"][change["item"]] = local["basics"].get(change["item"], "")
    elif change["section"] == "skills":
        base["skills"] = list(local["skills"])
    else:
        l = next((i for i in local["items"] if i["id"] == change["item"]), None)
        rest = [i for i in base["items"] if i["id"] != change["item"]]
        if change["op"] == "remove" or l is None:
            base["items"] = rest
        else:
            at = next((n for n, i in enumerate(base["items"]) if i["id"] == change["item"]), len(rest))
            rest.insert(at, copy.deepcopy(l))
            base["items"] = rest
    return base


def revert(base: dict, local: dict, change_id: str) -> dict:
    """The local copy with one pending change undone (back to what the site has)."""
    base, local = clean(base), clean(local)
    if change_id.startswith("basics."):
        name = change_id.split(".", 1)[1]
        local["basics"][name] = base["basics"].get(name, "")
    elif change_id == "skills":
        local["skills"] = list(base["skills"])
    else:
        iid = change_id.split(".", 1)[1]
        b = next((i for i in base["items"] if i["id"] == iid), None)
        at = next((n for n, i in enumerate(local["items"]) if i["id"] == iid), None)
        if b is None:
            local["items"] = [i for i in local["items"] if i["id"] != iid]
        elif at is None:
            pos = next((n for n, i in enumerate(local["items"]) if i["section"] == b["section"]), len(local["items"]))
            local["items"].insert(pos, copy.deepcopy(b))
        else:
            local["items"][at] = copy.deepcopy(b)
    return local


def summary_of(p: dict | None) -> dict[str, Any]:
    p = clean(p)
    return {"items": len(p["items"]), "skills": len(p["skills"])}
