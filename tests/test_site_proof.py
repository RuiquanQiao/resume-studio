"""A careers site's two kinds of proof (连尚网络, 2026-11-24):

- one under another domain than the company's says who runs it and how it is the company's (`owner`), or the AI's
  fill refuses it: 连尚网络's roles were on cn.wifi.com ("尚网网络"), which read as another company's;
- one the AI showed to list no roles is recorded with its evidence (`no-list`), the one place the window's check can
  see it: written in the chat alone, the check sent the same request back."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from jobhunt.store import Hunt, no_list_on, owner_missing
from test_hunt import cli, live  # noqa: F401
from test_hunt_agent import FakeBrowser, NoTool

GROUP = "https://www.ilinksure.example/careers"
WIFI = "https://cn.wifi.example/jobList.html"
PROOF = ("Opened https://www.ilinksure.example/careers: only job categories and the mailbox hr@zenmen.example; "
         "GET https://www.ilinksure.example/api/jobs returned 404; no list is linked from the page.")


@pytest.fixture
def hunt(tmp_path: Path) -> Hunt:
    return Hunt(tmp_path / "resume-studio-data")


def test_a_site_under_another_domain_needs_who_runs_it(hunt):
    hunt.add_company({"name": "连尚网络", "sites": [{"url": GROUP, "kind": "social", "system": "own site"}]})
    wifi = {"url": WIFI, "kind": "social", "system": "own site"}
    with pytest.raises(ValueError, match="cn.wifi.example"):
        hunt.fill("C1", {"sites": [{"url": GROUP, "kind": "social", "system": "own site"}, wifi]})
    assert [s["url"] for s in hunt.company("C1")["sites"]] == [GROUP]                 # nothing was saved
    said = {**wifi, "owner": "WiFi万能钥匙's site, run by 南京尚网网络科技: same zenmen.example mail",
            "owner_source": "https://cn.wifi.example/"}
    c, _ = hunt.fill("C1", {"sites": [{"url": GROUP, "kind": "social", "system": "own site"}, said]})
    assert c["sites"][1]["owner"].startswith("WiFi万能钥匙") and c["sites"][1]["owner_source"] == "https://cn.wifi.example/"
    assert owner_missing(c) == []
    # a later fill that leaves it out keeps what was said
    c, _ = hunt.fill("C1", {"sites": [{"url": GROUP, "kind": "social", "system": "own site"}, wifi]})
    assert c["sites"][1]["owner"].startswith("WiFi万能钥匙")
    # a board on a recruiting system is on its system's domain: nothing to say
    moka = {"url": "https://app.mokahr.example/social-recruitment/linksure/1", "kind": "social", "system": "Moka"}
    c, _ = hunt.fill("C1", {"sites": [*c["sites"], moka]})
    assert owner_missing(c) == []


def test_a_site_the_user_or_an_old_fill_left_unsaid_is_named(hunt):
    c = hunt.add_company({"name": "连尚网络", "homepage": "https://www.ilinksure.example/",
                          "sites": [{"url": GROUP, "system": "own site"}, {"url": WIFI, "system": "own site"}]})
    assert owner_missing(c) == [WIFI]


def test_a_site_shown_to_list_no_roles_needs_no_route(hunt):
    ps = Postings(hunt)
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    hunt.add_company({"name": "连尚网络", "sites": [{"url": GROUP, "kind": "social", "system": "own site"}]})
    assert sc.scan("C1")["needs"] == "claude" and ps.scan_state("C1")["unrouted"] == [GROUP]
    with pytest.raises(ValueError):                                                 # proof, with what was tried
        hunt.mark_no_list("C1", GROUP, "There is no list.")
    with pytest.raises(ValueError):                                                 # only a site of the company
        hunt.mark_no_list("C1", WIFI, PROOF)
    c = hunt.mark_no_list("C1", GROUP, PROOF)
    assert no_list_on(c["sites"][0]) and c["sites"][0]["no_list"]["evidence"] == PROOF
    # at once, before any scan: nothing left for the AI to learn
    assert (sc.live_state(c)["needs"], sc.live_state(c)["unrouted"]) == ("", [])
    assert sc.site_status(c) == {GROUP: "none"}
    res = sc.scan("C1")
    assert (res["found"], res["needs"], res["unrouted"]) == (0, "", [])
    # an edit of the sites in the window keeps the mark; a fill that leaves it out does too
    hunt.update_company("C1", {"sites": [{**s} for s in hunt.company("C1")["sites"]]})
    hunt.fill("C1", {"sites": [{"url": GROUP, "kind": "social", "system": "own site"}]})
    assert no_list_on(hunt.company("C1")["sites"][0])
    # taken off: it is to be learned again
    c = hunt.mark_no_list("C1", GROUP, "")
    assert "no_list" not in c["sites"][0] and sc.scan("C1")["needs"] == "claude"


def test_a_no_list_mark_lapses_and_a_route_wins_over_it(hunt):
    ps = Postings(hunt)
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({GROUP: {"postings": [{"title": "产品经理", "url": GROUP + "/1"}]}}), lambda: None)
    old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 91 * 86400))
    c = hunt.add_company({"name": "连尚网络", "sites": [{"url": GROUP, "no_list": {"at": old, "evidence": PROOF}}]})
    assert not no_list_on(c["sites"][0]) and [s["how"] for s in sc.plan(c)] == ["claude"]
    c = hunt.mark_no_list("C1", GROUP, PROOF)
    ps.set_scan_state("C1", route={"pages": [{"url": GROUP, "script": "async (h) => ({postings: []})"}], "sites": [GROUP]})
    assert [s["how"] for s in sc.plan(c)] == ["route"] and sc.site_status(c) == {GROUP: "route"}


def test_the_ai_records_it_and_the_windows_check_sees_it(live, data_file):
    """The bug itself: the AI's proof reached the check only through the company's data."""
    hunt = live.inst.hunt
    hunt.add_company({"name": "连尚网络", "sites": [{"url": GROUP, "kind": "social", "system": "own site"}]})
    hunt.scanner.postings.set_scan_state("C1", needs="claude", unrouted=[GROUP], at="2026-11-24T20:00:00", ok=True)
    row = lambda: next(c for c in live.req("GET", "/api/hunt")[1]["companies"] if c["id"] == "C1")  # noqa: E731
    assert row()["postings"]["needs"] == "claude"
    code, out = cli(data_file, "no-list", "连尚网络", GROUP, "--stdin", stdin="too short")
    assert code != 0 and row()["postings"]["needs"] == "claude", out
    code, out = cli(data_file, "no-list", "连尚网络", GROUP, "--stdin", stdin=PROOF)
    assert code == 0 and "lists no roles (recorded" in out, out
    assert (row()["postings"]["needs"], row()["postings"]["unrouted"]) == ("", [])
    v = live.req("GET", "/api/hunt/companies/C1")[1]
    assert v["scan"]["sites"] == {GROUP: "none"} and v["company"]["sites"][0]["no_list"]["evidence"] == PROOF
    code, out = cli(data_file, "no-list", "连尚网络", GROUP, "--clear")
    assert code == 0 and row()["postings"]["needs"] == "claude", out
    # a site under another domain: `show` names it, and the window's check sees it
    hunt.update_company("C1", {"sites": [{"url": GROUP, "system": "own site"}, {"url": WIFI, "system": "own site"}]})
    assert row()["postings"]["owner_missing"] == [WIFI]
    code, out = cli(data_file, "show", "连尚网络")
    assert "whose: not said" in out, out
    code, out = cli(data_file, "fill", "连尚网络", "--stdin", stdin=json.dumps({"sites": [
        {"url": GROUP, "system": "own site"},
        {"url": WIFI, "system": "own site", "owner": "WiFi万能钥匙's site, run by 南京尚网网络科技", "owner_source": "https://cn.wifi.example/"}]}))
    assert code == 0 and row()["postings"]["owner_missing"] == [], out
