"""Reporting the recruiting systems still read by the AI's routes (jobhunt/report.py, 2026-11-12).

What is sent must say nothing about the user: a real tracker's routes held one company's API on its own host
(`job-api.<company>.com`) and one whose very path named the company, so only what two or more companies share is
reported. Companies and hosts here are made up."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from jobhunt import report
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401


def script(*urls: str) -> str:
    return "async (h) => { " + " ".join(f"await fetch('{u}', {{method: 'POST'}});" for u in urls) + " return {postings: []}; }"


PAGES = [
    ("星河科技", "https://xinghe.joinus.example/job", script("https://api.joinus.example/api/v2/job/search?tenant=88123456")),
    ("远山能源", "https://yuanshan.joinus.example/job", script("https://api.joinus.example/api/v2/job/search?tenant=99887766")),
    ("长风集团", "https://careers.changfeng.example/", script("https://job-api.changfeng.example/sapi/campus/jobs/list",
                                                         "https://search.example-cloud.net/1/indexes/changfeng_jobs/query")),
    ("星河科技", "https://xinghe.joinus.example/intern", script("/api/v2/job/search")),
]


def test_only_what_two_or_more_companies_share_is_offered_and_no_name_or_own_host_goes_out():
    items = report.offers(PAGES)
    assert [(x["paths"], x["hosts"], x["companies"], x["pages"]) for x in items] == \
        [(["/api/v2/job/search"], ["api.joinus.example"], 2, 3)]
    body = report.text(items, ui="en")
    for private in ("星河", "远山", "长风", "changfeng", "xinghe", "yuanshan", "88123456"):
        assert private not in body, private
    assert "`/api/v2/job/search`" in body and "2 companies, 3 route pages" in body
    url = report.issue_url(items, body)
    assert url.startswith(report.REPO + "/issues/new?")
    q = parse_qs(urlsplit(url).query)
    assert q["body"] == [body] and q["title"] == ["Scan to build: /api/v2/job/search"]


def test_a_path_a_built_in_scan_reads_is_never_offered():
    assert report.offers(PAGES, lambda url, path: path == "/api/v2/job/search") == []


def test_a_reported_or_ignored_system_is_not_offered_again(hunt):
    ps = Postings(hunt)
    for name, url, s in PAGES[:2]:
        c = hunt.add_company({"name": name, "sites": [{"url": url}]})
        ps.set_scan_state(c["id"], route={"pages": [{"url": url, "script": s}]})
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    got = sc.report_offers()
    assert [x["companies"] for x in got["items"]] == [2] and got["issue_url"] and "/api/v2/job/search" in got["text"]
    sc.mark_reported([got["items"][0]["key"]], "ignored")
    assert sc.report_offers() == {"items": [], "text": "", "issue_url": ""}
    # kept across restarts
    assert Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None).report_offers()["items"] == []
