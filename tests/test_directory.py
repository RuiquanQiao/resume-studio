"""The employer directory (公司库, jobhunt/directory.py): the shipped copy, the daily update from GitHub (stubbed:
never the network), search, and a record mapped into the user's company list."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from jobhunt import directory
from jobhunt.directory import Directory, nature_of, to_company
from jobhunt.store import Hunt, SameCompany

FIELDS = directory.FIELDS


def row(**kw) -> list:
    rec = {f: ([] if f in ("aliases", "cities", "hires", "roles") else "") for f in FIELDS}
    rec.update(kw)
    return [rec[f] for f in FIELDS]


CBA = row(name="Commonwealth Bank", full_name="Commonwealth Bank of Australia", aliases=["CBA", "CommBank"],
          nature="au.company", industry="Banking & Financial Services", sub="Major bank", hq="Sydney",
          cities=["Sydney", "Melbourne"], website="https://www.commbank.com.au",
          notes="Graduate programme open to Australian and New Zealand citizens and Australian permanent residents")
APS = row(name="Treasury", nature="au.government", industry="Government & Research", hq="Canberra", cities=["Canberra"],
          notes="Australian citizenship required (APS requirement)")
MARSH = row(name="Marsh Australia", nature="au.company", parent="Marsh McLennan", industry="Insurance", hq="Sydney")
CATL = row(name="宁德时代", full_name="宁德时代新能源科技股份有限公司", aliases=["CATL"], nature="cn.private", industry="新能源",
           sub="动力电池", hq="宁德", cities=["宁德", "上海"], website="https://www.catl.com",
           campus_site="https://catl.zhiye.com/campus", system="北森")


def write_copy(folder: Path, updated: str, countries: dict[str, list[list]], *, sha: bool = False, short: bool = False,
               version: str = "") -> dict:
    """`sha`: each country's whole sha256; `short`: its first 12 hex as "sha"; `version`: the build time."""
    folder.mkdir(parents=True, exist_ok=True)
    idx = {"updated": updated, "countries": [], **({"version": version} if version else {})}
    for code, rows in countries.items():
        data = json.dumps({"fields": FIELDS, "rows": rows}, ensure_ascii=False).encode("utf-8")
        (folder / f"{code}.json").write_bytes(data)
        entry = {"code": code, "employers": len(rows), "careers": 0, "system": 0}
        if sha:
            entry["sha256"] = hashlib.sha256(data).hexdigest()
        if short:
            entry["sha"] = hashlib.sha256(data).hexdigest()[:12]
        idx["countries"].append(entry)
    (folder / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    return idx


class Remote:
    """GitHub raw, played from a folder; every address asked is kept."""
    def __init__(self, folder: Path, fail: bool = False):
        self.folder, self.fail, self.asked = folder, fail, []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url.rsplit("/", 1)[-1])
        if self.fail:
            raise OSError("offline")
        return (self.folder / url.rsplit("/", 1)[-1]).read_bytes()


@pytest.fixture
def hunt(tmp_path: Path) -> Hunt:
    return Hunt(tmp_path / "resume-studio-data")


# ---------------------------------------------------------------- mapping into the user's list
def test_a_record_maps_into_the_fields_a_company_takes():
    rec = dict(zip(FIELDS, CBA))
    got = to_company(rec)
    assert got["name"] == "Commonwealth Bank"
    assert got["aliases"] == ["Commonwealth Bank of Australia", "CBA", "CommBank"]   # the registered name is one too
    assert got["industry"] == "Banking & Financial Services" and got["nature"] == "private"
    assert got["hq"] == "Sydney" and got["places"] == ["Sydney", "Melbourne"]
    assert got["homepage"] == "https://www.commbank.com.au" and "sites" not in got   # a homepage is no careers site
    assert got["note"].startswith("Graduate programme open to Australian")           # who may apply
    cn = to_company(dict(zip(FIELDS, CATL)))
    assert cn["sites"] == [{"url": "https://catl.zhiye.com/campus", "kind": "campus", "system": "北森"}]
    assert cn["nature"] == "cn.private" and cn["aliases"] == ["宁德时代新能源科技股份有限公司", "CATL"]


@pytest.mark.parametrize("code,parent,want", [
    ("cn.central_soe", "", "cn.central_soe"), ("cn.private", "", "cn.private"), ("cn.university", "", "cn.university"),
    ("au.company", "", "private"), ("au.company", "Marsh McLennan", ""),   # a group's arm: left for the AI, never guessed
    ("au.state", "", "soe"), ("sg.government", "", "government"), ("sg.research", "", "academic"),
    ("au.university", "", "au.university"), ("hk.nonprofit", "", "nonprofit"), ("xx.whatever", "", ""), ("", "", ""),
])
def test_a_directory_nature_is_one_of_the_apps_own(code, parent, want):
    from jobhunt import natures
    got = nature_of(code, parent)
    assert got == want and (got == "" or got in natures.IDS)


def test_adding_puts_every_fact_on_the_list_and_never_twice(hunt):
    c = directory.add(hunt, dict(zip(FIELDS, CATL)))
    assert c["name"] == "宁德时代" and c["nature"] == "cn.private" and c["homepage"] == "https://www.catl.com"
    assert c["sites"][0]["url"] == "https://catl.zhiye.com/campus" and c["sites"][0]["system"] == "北森"
    assert c["hq"]["area"] and len(c["places"]) == 2
    with pytest.raises(SameCompany) as e:                       # by its name or one of its other names
        directory.add(hunt, dict(zip(FIELDS, row(name="CATL"))))
    assert e.value.same["id"] == c["id"]
    hunt.add_company({"name": "Commonwealth"})
    with pytest.raises(SameCompany) as e:                       # a close name asks first
        directory.add(hunt, dict(zip(FIELDS, CBA)))
    assert e.value.close and not e.value.same
    assert directory.add(hunt, dict(zip(FIELDS, CBA)), different=True)["name"] == "Commonwealth Bank"
    hunt.add_company({"name": "Marsh"})                         # a region's arm is a company of its own (store._close)
    assert directory.add(hunt, dict(zip(FIELDS, MARSH)))["name"] == "Marsh Australia"


def test_a_city_the_place_list_cant_read_never_stops_the_add(hunt):
    rec = dict(zip(FIELDS, row(name="某某公司", hq=" ", cities=["", "宁德"])))
    c = directory.add(hunt, rec)
    assert c["hq"] is None and [p["area"] for p in c["places"]] and c["name"] == "某某公司"


# ---------------------------------------------------------------- finding
def test_search_filters_and_counts(tmp_path):
    write_copy(tmp_path / "b", "2026-11-20", {"au": [CBA, APS, MARSH], "cn": [CATL]})
    d = Directory(None, bundled=tmp_path / "b")
    assert d.search()["total"] == 4
    assert [r["name"] for r in d.search("commbank")["rows"]] == ["Commonwealth Bank"]   # an alias
    assert [r["name"] for r in d.search("canberra")["rows"]] == ["Treasury"]            # a city
    assert [r["name"] for r in d.search("动力")["rows"]] == ["宁德时代"]                  # the line of business
    au = d.search(country="au")
    assert au["total"] == 3 and dict(au["industries"])["Insurance"] == 1
    assert au["natures"] == {"private": 1, "government": 1, "": 1}
    assert d.search(country="au", nature="government")["total"] == 1
    assert [r["name"] for r in d.search(careers=True)["rows"]] == ["宁德时代"]
    assert d.search(industry="Insurance", country="au")["rows"][0]["name"] == "Marsh Australia"
    page = d.search(limit=2, offset=2)
    assert page["total"] == 4 and len(page["rows"]) == 2
    assert d.search(country="zz")["total"] == 4          # a country not in the directory is no filter
    assert d.matches("CATL")[0]["name"] == "宁德时代" and d.get(d.matches("CATL")[0]["id"])["name"] == "宁德时代"
    with pytest.raises(KeyError):
        d.get("au-99")


def test_the_shipped_copy_is_whole_and_agrees_with_its_index():
    d = Directory(None)
    idx = d.index()
    assert idx["updated"] and {c["code"] for c in idx["countries"]} >= {"cn", "au"}
    for c in idx["countries"]:
        assert len(d.rows(c["code"])) == c["employers"], c["code"]
    assert sum(c["employers"] for c in idx["countries"]) > 8000


# ---------------------------------------------------------------- keeping up to date
def test_a_newer_index_on_github_brings_the_changed_files_into_the_cache(tmp_path):
    bundled = tmp_path / "b"
    write_copy(bundled, "2026-11-20", {"au": [CBA], "cn": [CATL]}, sha=True)
    remote = tmp_path / "r"
    write_copy(remote, "2026-11-25", {"au": [CBA, APS], "cn": [CATL], "sg": [row(name="GIC", nature="sg.state")]}, sha=True)
    fetch = Remote(remote)
    d = Directory(tmp_path / "cache", bundled=bundled, fetch=fetch)
    assert d.search(country="au")["total"] == 1
    r = d.check()
    assert r["updated"] and r["countries"] == ["au", "cn", "sg"]
    assert "cn.json" not in fetch.asked                   # unchanged (same sha256): copied from the bundle, not downloaded
    assert d.index()["updated"] == "2026-11-25"
    assert d.search(country="au")["total"] == 2 and d.search(country="sg")["total"] == 1
    assert d.search(country="cn")["total"] == 1
    # within the day: not asked again; asked again a day later, and the same index changes nothing
    fetch.asked.clear()
    assert d.check() == {"checked": False} and fetch.asked == []
    assert d.check(force=True) == {"checked": True, "updated": False} and fetch.asked == ["index.json"]
    # a new app version ships a newer copy than the cache: the shipped one is read
    write_copy(bundled, "2026-12-01", {"au": [CBA]}, sha=True)
    d2 = Directory(tmp_path / "cache", bundled=bundled, fetch=fetch)
    assert d2.index()["updated"] == "2026-12-01" and d2.search()["total"] == 1


def test_a_copy_built_later_the_same_day_replaces_the_earlier_one(tmp_path):
    """2026-11-20: the shipped copy was made while the Australian list was still being written; the finished one,
    published the same day, said "updated": "2026-11-20" too and looked no newer. The build time decides, and only
    the countries whose sha differs are downloaded."""
    bundled = tmp_path / "b"
    write_copy(bundled, "2026-11-20", {"au": [CBA], "cn": [CATL]})                    # no version: an older index's shape
    remote = tmp_path / "r"
    write_copy(remote, "2026-11-20", {"au": [CBA, APS], "cn": [CATL]}, short=True, version="2026-11-20T10:31:00Z")
    fetch = Remote(remote)
    d = Directory(tmp_path / "cache", bundled=bundled, fetch=fetch)
    r = d.check()
    assert r["updated"] and fetch.asked == ["index.json", "au.json"]                  # cn's sha is the one in hand
    assert d.search(country="au")["total"] == 2 and d.search(country="cn")["total"] == 1
    assert directory.version(d.index()) == "2026-11-20T10:31:00Z"
    # later the same day again: newer by its version alone
    write_copy(remote, "2026-11-20", {"au": [CBA, APS, MARSH], "cn": [CATL]}, short=True, version="2026-11-20T14:02:00Z")
    fetch.asked.clear()
    assert d.check(force=True)["updated"] and fetch.asked == ["index.json", "au.json"]
    assert d.search(country="au")["total"] == 3
    # the same build again changes nothing; an earlier build of the day is never taken for a newer one
    fetch.asked.clear()
    assert d.check(force=True) == {"checked": True, "updated": False} and fetch.asked == ["index.json"]
    write_copy(remote, "2026-11-20", {"au": [CBA]}, short=True, version="2026-11-20T09:00:00Z")
    assert d.check(force=True) == {"checked": True, "updated": False} and d.search(country="au")["total"] == 3
    # a short sha that doesn't match the file: nothing replaced
    write_copy(remote, "2026-11-21", {"au": [CBA], "cn": [CATL]}, short=True, version="2026-11-21T08:00:00Z")
    idx = json.loads((remote / "index.json").read_text(encoding="utf-8"))
    idx["countries"][0]["sha"] = "000000000000"
    (remote / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    assert "sha" in d.check(force=True)["error"] and d.search(country="au")["total"] == 3


def test_an_update_that_fails_is_logged_and_changes_nothing(tmp_path):
    bundled = tmp_path / "b"
    write_copy(bundled, "2026-11-20", {"au": [CBA]}, sha=True)
    # offline
    d = Directory(tmp_path / "c1", bundled=bundled, fetch=Remote(tmp_path, fail=True))
    r = d.check()
    assert "error" in r and "offline" in (tmp_path / "c1" / "directory" / "update.log").read_text(encoding="utf-8")
    assert d.search()["total"] == 1 and not (tmp_path / "c1" / "directory" / "index.json").exists()
    # a file that doesn't match its sha256 (half-written upstream): no index is written, the old copy stays
    remote = tmp_path / "r"
    idx = write_copy(remote, "2026-11-25", {"au": [CBA, APS]}, sha=True)
    (remote / "au.json").write_text(json.dumps({"fields": FIELDS, "rows": [CBA]}), encoding="utf-8")
    d = Directory(tmp_path / "c2", bundled=bundled, fetch=Remote(remote))
    assert "sha" in d.check()["error"] and idx["updated"] != d.index()["updated"]
    assert not (tmp_path / "c2" / "directory" / "index.json").exists()
    # an index in another shape
    (remote / "index.json").write_text("[]", encoding="utf-8")
    d = Directory(tmp_path / "c3", bundled=bundled, fetch=Remote(remote))
    assert "error" in d.check() and d.search()["total"] == 1


def test_the_day_check_never_runs_in_tests_or_twice_a_day(tmp_path, monkeypatch):
    d = Directory(tmp_path / "c", fetch=Remote(tmp_path, fail=True))
    assert d.due()
    (tmp_path / "c" / "directory").mkdir(parents=True)
    (tmp_path / "c" / "directory" / "checked").write_text(str(__import__("time").time()), encoding="utf-8")
    assert not d.due()
    started = []
    monkeypatch.setattr(directory.threading, "Thread", lambda *a, **k: started.append(k) or type("T", (), {"start": lambda s: None})())
    monkeypatch.setenv("STUDIO_DIRECTORY_UPDATE", "on")
    d.check_later()
    assert started == []                                  # checked within the day
    (tmp_path / "c" / "directory" / "checked").unlink()
    monkeypatch.setenv("STUDIO_DIRECTORY_UPDATE", "off")
    d.check_later()
    assert started == []                                  # switched off (the suite)
    monkeypatch.setenv("STUDIO_DIRECTORY_UPDATE", "on")
    d.check_later()
    assert len(started) == 1


# ---------------------------------------------------------------- copying it into the app
def test_the_sync_script_copies_a_checkout_and_refuses_a_broken_one(tmp_path):
    src = tmp_path / "campus-employers"
    write_copy(src / "docs" / "data", "2026-11-20", {"au": [CBA], "sg": [row(name="GIC")]})
    dest = tmp_path / "dest"
    (dest).mkdir()
    (dest / "hk.json").write_text("{}", encoding="utf-8")      # a country no longer listed goes
    run = lambda: subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_directory.py"), "--from", str(src), "--dest", str(dest)],  # noqa: E731
                                 capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    p = run()
    assert p.returncode == 0, p.stderr
    assert sorted(x.name for x in dest.iterdir()) == ["au.json", "index.json", "sg.json"]
    (src / "docs" / "data" / "sg.json").write_text("{\"rows\": 1}", encoding="utf-8")
    p = run()
    assert p.returncode == 1 and "sg.json" in p.stderr
    assert json.loads((dest / "sg.json").read_text(encoding="utf-8"))["rows"]   # nothing was replaced


# ---------------------------------------------------------------- the in-app agent's command (scripts/hunt.py directory)
def test_the_agent_searches_the_directory_and_adds_by_id_or_whole_name(server):
    st, r = server.req("POST", "/api/hunt/do", {"cmd": "directory", "args": {"q": "宁德时代"}})
    assert st == 200 and r["text"] == "found" and r["directory"]["rows"][0]["name"] == "宁德时代"
    assert r["directory"]["rows"][0]["added"] is None
    st, r = server.req("POST", "/api/hunt/do", {"cmd": "directory", "args": {"add": "宁德时代"}, "chat": "c1"})
    assert st == 200 and r["text"] == "added" and r["change"]                # kept, undoable, like add-company
    c = r["company"]["company"]
    assert c["name"] == "宁德时代" and c["nature"] == "cn.private" and c["homepage"]
    st, r = server.req("POST", "/api/hunt/do", {"cmd": "directory", "args": {"q": "宁德时代"}})
    assert r["directory"]["rows"][0]["added"]["id"] == c["id"]
    st, r = server.req("POST", "/api/hunt/do", {"cmd": "directory", "args": {"add": "宁德时代"}})
    assert st == 409 and r["data"]["same"]["id"] == c["id"]                  # already on the list: that one, never twice
    st, r = server.req("POST", "/api/hunt/do", {"cmd": "directory", "args": {"add": "没有这家公司"}})
    assert st == 404 and "search it first" in r["error"]
    # the window's own list
    st, r = server.req("GET", "/api/hunt/directory?country=au&q=commbank")
    assert st == 200 and r["total"] == 1 and r["countries"][0]["name"]
    st, r = server.req("POST", "/api/hunt/directory/add", {"id": r["rows"][0]["id"]})
    assert st == 200 and r["company"]["name"] == "Commonwealth Bank"
    st, r = server.req("POST", "/api/hunt/directory/add", {"id": "au-0"})
    assert st == 409 and r["data"]["same"]
