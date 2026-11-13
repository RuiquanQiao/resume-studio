"""A company's nature (国央企, 民企…) and tracks for some natures only (2026-11-13: a private game studio's
后端开发工程师 was put in "信息科技（国央企）", since a track went by the title alone)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import jobhunt.postings as P
from jobhunt.history import History
from jobhunt.postings import Postings
from jobhunt.store import Hunt
from test_hunt import cli, live  # noqa: F401

SOE_IT = {"id": "T4", "name": "信息科技 / 软件开发（国央企）", "keywords": ["开发工程师", "研发"], "natures": ["soe", "public"]}
ANY_IT = {"id": "T1", "name": "软件开发", "keywords": ["开发工程师"]}


@pytest.fixture
def hunt(tmp_path: Path) -> Hunt:
    return Hunt(tmp_path / "resume-studio-data")


def test_a_track_for_state_employers_takes_no_role_of_a_private_company(hunt):
    ps = Postings(hunt)
    ps.settings(tracks=[ANY_IT, SOE_IT])
    trs = ps.tracks()
    assert trs[1]["natures"] == ["soe", "public"]
    # the 2026-11-13 case: 沐瞳 (private) and its 后端开发工程师
    assert P.tracks_of("后端开发工程师", trs, "private") == ["T1"]
    assert P.tracks_of("后端开发工程师", trs, "soe") == ["T1", "T4"]
    assert P.tracks_of("后端开发工程师", trs, "public") == ["T1", "T4"]      # a track for several natures takes each
    assert P.tracks_of("后端开发工程师", trs, "foreign") == ["T1"]
    # not known yet: a track for some natures only can't tell, so takes none; a track for every company still does
    assert P.tracks_of("后端开发工程师", trs, "") == ["T1"] and P.tracks_of("后端开发工程师", trs) == ["T1"]
    assert [tr["id"] for tr in P.held_back(trs, "")] == ["T4"] and P.held_back(trs, "private") == []


def test_the_table_and_the_picked_roles_go_by_each_companys_nature(hunt):
    ps = Postings(hunt)
    ps.settings(tracks=[SOE_IT])
    mt = hunt.add_company({"name": "沐瞳科技", "nature": "民企"})
    cs = hunt.add_company({"name": "中国电信", "nature": "soe"})
    blank = hunt.add_company({"name": "某公司"})
    for c in (mt, cs, blank):
        ps.merge(c["id"], [{"title": "后端开发工程师", "url": f"https://jobs.example/{c['id']}"}], how="route")
    s = ps.summary()
    assert (s[mt["id"]]["matching"], s[cs["id"]]["matching"], s[blank["id"]]["matching"]) == (0, 1, 0)
    assert [p["company"] for p in ps.matched()["postings"]] == [cs["id"]]
    # its nature filled in: counted again at once, not at the next scan
    hunt.update_company(blank["id"], {"nature": "事业单位"})
    assert ps.summary()[blank["id"]]["matching"] == 1
    assert sorted(p["company"] for p in ps.matched()["postings"]) == sorted([cs["id"], blank["id"]])


def test_natures_are_kept_by_id_and_a_word_not_on_the_list_is_refused(hunt):
    c = hunt.add_company({"name": "沐瞳科技"})
    assert c["nature"] == ""
    for word, kept in (("国央企", "soe"), ("央企", "soe"), ("高校/科研院所", "academic"), ("Foreign", "foreign"), ("", "")):
        assert hunt.update_company(c["id"], {"nature": word})["nature"] == kept
    with pytest.raises(ValueError):
        hunt.update_company(c["id"], {"nature": "大厂"})
    with pytest.raises(ValueError):
        Postings(hunt).settings(tracks=[{**SOE_IT, "natures": ["国企", "大厂"]}])
    assert P.clean_tracks([{**SOE_IT, "natures": "国央企, 事业单位, soe"}])[0]["natures"] == ["soe", "public"]
    assert P.clean_tracks([ANY_IT])[0]["natures"] == []


def test_fill_needs_a_source_for_the_nature_and_leaves_the_users_own(hunt):
    c = hunt.add_company({"name": "沐瞳科技"})
    with pytest.raises(ValueError, match="source"):
        hunt.fill(c["id"], {"nature": "private"})
    with pytest.raises(ValueError, match="source"):
        hunt.fill(c["id"], {"nature": "private", "sources": {"nature": "企查查"}})   # a source is a page
    got, _ = hunt.fill(c["id"], {"nature": "private", "sources": {"nature": "https://www.qcc.com/x"}})
    assert got["nature"] == "private" and got["sources"]["nature"] == "https://www.qcc.com/x"
    assert hunt.fill(c["id"], {"industry": "游戏"})[0]["nature"] == "private"            # a fill without it keeps it
    hunt.update_company(c["id"], {"nature": "foreign"})                                   # the user's, from the window
    got, kept = hunt.fill(c["id"], {"nature": "soe", "sources": {"nature": "https://example.com/y"}})
    assert kept == ["nature"] and got["nature"] == "foreign" and got["sources"]["nature"] == "https://www.qcc.com/x"


def test_an_undo_puts_the_nature_back(hunt):
    h = History(hunt)
    c = hunt.add_company({"name": "沐瞳科技"})
    before = h.snapshot("company", c["id"])
    hunt.fill(c["id"], {"nature": "soe", "sources": {"nature": "https://example.com/wrong"}})
    e = h.record("company", c["id"], before, h.snapshot("company", c["id"]), cmd="fill")
    assert "nature" in e["after"]
    h.undo(e["id"])
    assert hunt.company(c["id"])["nature"] == ""


def test_the_tracks_named_for_state_employers_are_for_them_only_after_the_update(hunt):
    """Once: a track named "（国央企）" gets natures soe and public; the others, and a track the user set, stay."""
    (hunt.dir).mkdir(parents=True, exist_ok=True)
    (hunt.dir / "scans.json").write_text(json.dumps({"tracks": [
        {"id": "T1", "name": "AI 应用", "keywords": ["AI"]},
        {"id": "T4", "name": "信息科技 / 软件开发（国央企）", "keywords": ["研发"]},
        {"id": "T5", "name": "数据与人工智能（国央企）", "keywords": ["数据"], "natures": ["soe"]}]}, ensure_ascii=False), encoding="utf-8")
    ps = Postings(hunt)
    assert [tr["natures"] for tr in ps.tracks()] == [[], ["soe", "public"], ["soe"]]
    ps.settings(tracks=[{**tr, "natures": []} for tr in ps.tracks()])          # the user clears them: they stay clear
    assert [tr["natures"] for tr in Postings(hunt).tracks()] == [[], [], []]


def test_claude_sets_and_reads_the_nature_from_its_command(live, data_file):
    cli(data_file, "add-company", "沐瞳科技")
    code, out = cli(data_file, "fill", "沐瞳科技", "--stdin", stdin=json.dumps({"nature": "private"}))
    assert code != 0 and "sources.nature" in out
    f = {"nature": "民企", "sources": {"nature": "https://www.qcc.com/mt"}}
    code, out = cli(data_file, "fill", "沐瞳科技", "--stdin", stdin=json.dumps(f, ensure_ascii=False))
    assert code == 0 and "nature: private (民企)  [https://www.qcc.com/mt]" in out
    hid = out.split("kept as change ")[1].split()[0]
    code, out = cli(data_file, "tracks", "--stdin", stdin=json.dumps({"tracks": [ANY_IT, SOE_IT]}, ensure_ascii=False))
    assert code == 0 and "only at: soe, public" in out
    assert cli(data_file, "undo", hid)[0] == 0
    out = cli(data_file, "show", "沐瞳科技")[1]
    assert "nature:" not in out.split("roles")[0] and "nature" in out.split("not filled yet:")[1].split("\n")[0]
    assert cli(data_file, "edit-company", "沐瞳科技", "--nature", "外企")[0] == 0
    assert "nature: foreign (外企)" in cli(data_file, "show", "沐瞳科技")[1]
    assert cli(data_file, "edit-company", "沐瞳科技", "--nature", "大厂")[0] != 0
    assert "soe (国央企)" in cli(data_file, "tracks", "--help")[1] and '"nature"' in cli(data_file, "fill", "--help")[1]
