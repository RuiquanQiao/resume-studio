"""A company's nature and tracks for some natures only (2026-11-13: a private game studio's 后端开发工程师 was put
in "信息科技（国央企）", since a track went by the title alone). Natures are a catalog every country fits
(jobhunt/natures.py): kinds, and each country's own kinds under them; the user's profile says where they may work."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import jobhunt.natures as N
import jobhunt.postings as P
from jobhunt.history import History
from jobhunt.postings import CN_STATE, Postings
from jobhunt.store import Hunt
from test_hunt import cli, live  # noqa: F401

SOE_IT = {"id": "T4", "name": "信息科技 / 软件开发（国央企）", "keywords": ["开发工程师", "研发"], "natures": list(CN_STATE)}
ANY_IT = {"id": "T1", "name": "软件开发", "keywords": ["开发工程师"]}


@pytest.fixture
def hunt(tmp_path: Path) -> Hunt:
    return Hunt(tmp_path / "resume-studio-data")


def test_a_track_for_state_employers_takes_no_role_of_a_private_company(hunt):
    ps = Postings(hunt)
    ps.settings(tracks=[ANY_IT, SOE_IT])
    trs = ps.tracks()
    assert trs[1]["natures"] == list(CN_STATE)
    # the 2026-11-13 case: 沐瞳 (a private company in China) and its 后端开发工程师
    assert P.tracks_of("后端开发工程师", trs, "cn.private") == ["T1"]
    assert P.tracks_of("后端开发工程师", trs, "cn.central_soe") == ["T1", "T4"]
    assert P.tracks_of("后端开发工程师", trs, "cn.public") == ["T1", "T4"]      # a track for several natures takes each
    assert P.tracks_of("后端开发工程师", trs, "cn.foreign") == ["T1"]
    assert P.tracks_of("后端开发工程师", trs, "au.gbe") == ["T1"]               # another country's SOE: not China's
    # not known yet: a track for some natures only can't tell, so takes none; a track for every company still does
    assert P.tracks_of("后端开发工程师", trs, "") == ["T1"] and P.tracks_of("后端开发工程师", trs) == ["T1"]
    assert [tr["id"] for tr in P.held_back(trs, "")] == ["T4"] and P.held_back(trs, "cn.private") == []


def test_a_kind_takes_every_countrys_own_kinds_of_it_and_a_subtype_only_itself(hunt):
    ps = Postings(hunt)
    ps.settings(tracks=[{"id": "T1", "name": "SOE", "keywords": ["Engineer"], "natures": ["soe"]},
                        {"id": "T2", "name": "央企", "keywords": ["Engineer"], "natures": ["cn.central_soe"]}])
    trs = ps.tracks()
    assert P.tracks_of("Engineer", trs, "cn.central_soe") == ["T1", "T2"]
    assert P.tracks_of("Engineer", trs, "au.gbe") == ["T1"]
    assert P.tracks_of("Engineer", trs, "soe") == ["T1"]          # an SOE whose country's kind isn't known: the kind only
    assert P.tracks_of("Engineer", trs, "cn.local_soe") == ["T1"]
    assert P.tracks_of("Engineer", trs, "cn.private") == []


def test_the_table_and_the_picked_roles_go_by_each_companys_nature(hunt):
    ps = Postings(hunt)
    ps.settings(tracks=[SOE_IT])
    mt = hunt.add_company({"name": "沐瞳科技", "nature": "民企"})
    cs = hunt.add_company({"name": "中国电信", "nature": "央企"})
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


def test_natures_are_kept_by_id_and_a_word_not_in_the_catalog_is_refused(hunt):
    c = hunt.add_company({"name": "沐瞳科技"})
    assert c["nature"] == ""
    for word, kept in (("央企", "cn.central_soe"), ("国企", "soe"), ("外企", "cn.foreign"), ("高校/科研院所", "cn.university"),
                       ("Foreign", "foreign"), ("AU.APS", "au.aps"), ("intl_org", "intl_org"), ("", "")):
        assert hunt.update_company(c["id"], {"nature": word})["nature"] == kept
    with pytest.raises(ValueError):
        hunt.update_company(c["id"], {"nature": "大厂"})
    with pytest.raises(ValueError):
        Postings(hunt).settings(tracks=[{**SOE_IT, "natures": ["国企", "大厂"]}])
    assert P.clean_tracks([{**SOE_IT, "natures": "央企, 事业单位, cn.central_soe"}])[0]["natures"] == ["cn.central_soe", "cn.public"]
    assert P.clean_tracks([ANY_IT])[0]["natures"] == []
    # every kind and every country's own is in the catalog once, each country's under a kind
    assert len(N.IDS) == len(set(N.IDS)) and all(N.kind(i) in N.KIND_IDS for i in N.IDS)


def test_fill_needs_a_source_for_the_nature_and_leaves_the_users_own(hunt):
    c = hunt.add_company({"name": "沐瞳科技"})
    with pytest.raises(ValueError, match="source"):
        hunt.fill(c["id"], {"nature": "cn.private"})
    with pytest.raises(ValueError, match="source"):
        hunt.fill(c["id"], {"nature": "cn.private", "sources": {"nature": "企查查"}})   # a source is a page
    got, _ = hunt.fill(c["id"], {"nature": "cn.private", "sources": {"nature": "https://www.qcc.com/x"}})
    assert got["nature"] == "cn.private" and got["sources"]["nature"] == "https://www.qcc.com/x"
    assert hunt.fill(c["id"], {"industry": "游戏"})[0]["nature"] == "cn.private"          # a fill without it keeps it
    hunt.update_company(c["id"], {"nature": "cn.foreign"})                                # the user's, from the window
    got, kept = hunt.fill(c["id"], {"nature": "soe", "sources": {"nature": "https://example.com/y"}})
    assert kept == ["nature"] and got["nature"] == "cn.foreign" and got["sources"]["nature"] == "https://www.qcc.com/x"


def test_an_undo_puts_the_nature_back(hunt):
    h = History(hunt)
    c = hunt.add_company({"name": "沐瞳科技"})
    before = h.snapshot("company", c["id"])
    hunt.fill(c["id"], {"nature": "soe", "sources": {"nature": "https://example.com/wrong"}})
    e = h.record("company", c["id"], before, h.snapshot("company", c["id"]), cmd="fill")
    assert "nature" in e["after"]
    h.undo(e["id"])
    assert hunt.company(c["id"])["nature"] == ""


@pytest.mark.parametrize("before", [None, ["soe", "public"]])
def test_the_tracks_named_for_state_employers_are_for_chinas_after_the_update(hunt, before):
    """A track named "（国央企）" is for China's state employers, whether it had no natures yet or the first
    update's every-country ["soe", "public"]; the others, and a track the user set otherwise, stay."""
    hunt.dir.mkdir(parents=True, exist_ok=True)
    t4 = {"id": "T4", "name": "信息科技 / 软件开发（国央企）", "keywords": ["研发"], **({"natures": before} if before else {})}
    (hunt.dir / "scans.json").write_text(json.dumps({"tracks": [
        {"id": "T1", "name": "AI 应用", "keywords": ["AI"]}, t4,
        {"id": "T5", "name": "数据与人工智能（国央企）", "keywords": ["数据"], "natures": ["cn.central_soe"]}],
        **({"migrated": ["track-natures"]} if before else {})}, ensure_ascii=False), encoding="utf-8")
    ps = Postings(hunt)
    assert [tr["natures"] for tr in ps.tracks()] == [[], list(CN_STATE), ["cn.central_soe"]]
    ps.settings(tracks=[{**tr, "natures": []} for tr in ps.tracks()])          # the user clears them: they stay clear
    assert [tr["natures"] for tr in Postings(hunt).tracks()] == [[], [], []]


def test_an_employer_that_takes_only_citizens_says_so_to_who_isnt_one():
    au_citizen = N.clean_work({"rights": [{"country": "au", "status": "citizen"}], "targets": "CN, AU"})
    assert au_citizen == {"rights": [{"country": "AU", "status": "citizen"}]}      # where they look: the records say it
    cn = N.clean_work({"rights": [{"country": "CN", "status": "citizen"},
                                  {"country": "AU", "status": "work", "visa": "485", "from": "2027-07~", "until": "2029-07~"}]})
    assert N.short_of("cn.civil", "", au_citizen) == "citizen"          # a subtype's own country, not the company's hq
    assert N.short_of("cn.civil", "", cn) == ""
    assert N.short_of("au.aps", "", cn) == "citizen"                    # a work visa isn't citizenship
    assert N.short_of("au.state_ps", "", N.clean_work({"resident": ["AU"]})) == ""
    assert N.short_of("au.state_ps", "", cn) == "resident"
    assert N.short_of("private", "AU", cn) == ""                        # a visa that lets them work is enough
    assert N.short_of("private", "NZ", cn) == "work"
    # nothing to tell by: no nature, no country, or nothing in the profile
    assert N.short_of("", "AU", cn) == "" and N.short_of("private", "", cn) == "" and N.short_of("au.aps", "", N.clean_work({})) == ""
    assert N.countries_first(N.clean_work({"targets": ["AU"], "rights": [{"country": "CN", "status": "citizen"}]}), "SG") == ["CN", "SG"]


def test_work_rights_are_one_record_per_country_with_rough_dates():
    """2026-11-13: one record per country (China: citizen; Australia: a 485 visa, from about 2027-07 for two
    years), and a date only roughly known is kept as such (EDTF "~")."""
    w = N.clean_work({"rights": [
        {"country": "CN", "status": "citizen", "visa": "ignored", "from": "2001"},
        {"country": "AU", "status": "work", "visa": " 485  Temporary Graduate ", "from": "2027.07~", "until": "2029-07～"},
        {"country": "AU", "status": "work"},                                 # the same twice: the first is kept
        {"country": "NZ", "status": "study", "from": "July", "until": "2026-13"},   # unreadable dates are left out
        {"country": "XX1", "status": "citizen"}, {"country": "US", "status": "tourist"}, "nonsense"]})
    assert w["rights"] == [{"country": "CN", "status": "citizen"},
                           {"country": "AU", "status": "work", "visa": "485 Temporary Graduate", "from": "2027-07~", "until": "2029-07~"},
                           {"country": "NZ", "status": "study"}]
    assert N.status(w, "NZ") == "work"                                       # a student visa's work rights count
    # a visa that ended, known exactly, no longer counts; one whose end is only rough still does
    ended = N.clean_work({"rights": [{"country": "AU", "status": "work", "until": "2026-10"}]})
    assert N.short_of("private", "AU", ended, today="2026-11-13") == "work"
    assert N.short_of("private", "AU", ended, today="2026-10-31") == ""
    rough = N.clean_work({"rights": [{"country": "AU", "status": "work", "until": "2026~"}]})
    assert N.short_of("private", "AU", rough, today="2027-03-01") == ""
    # the first shape (lists per status, one note) reads as records
    old = N.clean_work({"citizen": ["CN"], "visa": ["AU"], "targets": ["AU"], "note": "485 until 2028-03"})
    assert old == {"rights": [{"country": "CN", "status": "citizen"}, {"country": "AU", "status": "work", "visa": "485 until 2028-03"}]}


def test_the_window_says_what_an_employer_asks_from_the_profile(live, data_file):
    """The profile's work status (resume.yaml profile.work, never rendered) reaches the companies: each one's
    country, and what it asks that the user doesn't have."""
    cli(data_file, "add-company", "Department of Finance", "--hq", "Canberra")
    cli(data_file, "edit-company", "Department of Finance", "--nature", "au.aps")
    cli(data_file, "add-company", "Atlassian", "--hq", "Sydney", "--nature", "private")
    d = live.req("GET", "/api/hunt")[1]
    by = {c["name"]: c for c in d["companies"]}
    assert by["Department of Finance"]["country"] == "AU" and by["Department of Finance"]["short_of"] == ""   # no profile yet
    assert {"kinds", "subtypes", "work"} <= set(d["natures"]) and any(s["id"] == "au.aps" for s in d["natures"]["subtypes"])
    code, st = live.req("GET", "/api/state")
    prof = dict(st["doc"]["profile"])
    prof["work"] = {"rights": [{"country": "CN", "status": "citizen"}, {"country": "AU", "status": "work", "visa": "485"}]}
    assert live.req("PUT", "/api/item", {"kind": "profile", "data": prof, "base": st["hashes"]["profile"]})[0] == 200
    d = live.req("GET", "/api/hunt")[1]
    by = {c["name"]: c for c in d["companies"]}
    assert by["Department of Finance"]["short_of"] == "citizen" and by["Atlassian"]["short_of"] == ""
    assert [r["country"] for r in d["natures"]["work"]["rights"]] == ["CN", "AU"] and "targets" not in d["natures"]["work"]
    out = cli(data_file, "show", "Department of Finance")[1]
    assert "au.aps (Australian Public Service (APS))" in out and "takes citizens of AU only" in out.replace("its roles take", "takes")
    geo = live.req("GET", "/api/hunt/countries")[1]
    assert len(geo["countries"]) > 200
    assert any(v["country"] == "AU" and v["status"] == "work" and "485" in v["name"] for v in geo["visas"])


def test_claude_sets_and_reads_the_nature_from_its_command(live, data_file):
    cli(data_file, "add-company", "沐瞳科技")
    code, out = cli(data_file, "fill", "沐瞳科技", "--stdin", stdin=json.dumps({"nature": "cn.private"}))
    assert code != 0 and "sources.nature" in out
    f = {"nature": "民企", "sources": {"nature": "https://www.qcc.com/mt"}}
    code, out = cli(data_file, "fill", "沐瞳科技", "--stdin", stdin=json.dumps(f, ensure_ascii=False))
    assert code == 0 and "nature: cn.private (Private company (民企))  [https://www.qcc.com/mt]" in out
    hid = out.split("kept as change ")[1].split()[0]
    code, out = cli(data_file, "tracks", "--stdin", stdin=json.dumps({"tracks": [ANY_IT, SOE_IT]}, ensure_ascii=False))
    assert code == 0 and "only at: cn.central_soe (Central SOE (央企))" in out
    assert cli(data_file, "undo", hid)[0] == 0
    out = cli(data_file, "show", "沐瞳科技")[1]
    assert "nature:" not in out.split("roles")[0] and "nature" in out.split("not filled yet:")[1].split("\n")[0]
    assert cli(data_file, "edit-company", "沐瞳科技", "--nature", "外企")[0] == 0
    assert "nature: cn.foreign" in cli(data_file, "show", "沐瞳科技")[1]
    assert cli(data_file, "edit-company", "沐瞳科技", "--nature", "大厂")[0] != 0
    helped = cli(data_file, "fill", "--help")[1]
    assert '"nature"' in helped and "Companies House" in helped and "ASIC" in helped and "企查查" in helped
    assert "au.aps" in cli(data_file, "tracks", "--help")[1]


def test_every_listed_visa_is_one_name_per_country_and_status():
    """2026-11-14: a visa is picked from a list (jobhunt/visas.py), so the list itself must be clean: a known
    country, a status a record has, no name twice in a country, and a Chinese name to find it by."""
    import jobhunt.visas as V
    seen = set()
    for cc, st, name, zh, words in V.VISAS:
        assert len(cc) == 2 and cc.isupper() and st in ("work", "study") and name.strip() == name and zh
        assert (cc, name) not in seen, name
        seen.add((cc, name))
    zh = V.catalog("zh")
    assert any(v["name"] == "Temporary Graduate visa (subclass 485)" and v["desc"] == "485 毕业生工作签证" and "485" in v["find"] for v in zh)
    assert all(v["desc"] == "" for v in V.catalog("en"))
