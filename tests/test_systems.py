"""Routes of several companies that call one job API are found by the code, not left to the AI (2026-11-11).

The AI learns one site at a time: about 120 北森 route pages were written one company after another, every one
calling /api/Jobad/GetJobAdPageList, before a built-in scan read them all. The scripts below are shaped like routes
the AI really recorded (HotJob's three ways of naming the portal, one group's own talent sites, a table of links
on an own site and on 北森's); the companies and addresses are made up."""
from __future__ import annotations

from jobhunt import systems
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401

HOTJOB_INLINE = """async (h) => { const r = await fetch('/wecruit/positionInfo/listPosition/SU6619d98e1eb8053acd618afb?iSaJAx=isAjax',
  {method: 'POST', body: 'pageSize=50'}); return {postings: []} }"""
HOTJOB_JOINED = """async (h) => { const su = 'SU60e7f1a2bef57c5a3a1d3c7e';
  const r = await fetch('/wecruit/positionInfo/listPosition/' + su, {method: 'POST'}); return {postings: []} }"""
HOTJOB_TEMPLATE = """async (h) => { const r = await fetch(`/wecruit/positionInfo/listPosition/${su}?iSaJAx=isAjax`); return {postings: []} }"""
ALIBABA = """async (h) => { const t = document.cookie; const r = await fetch('/position/search', {method: 'POST',
  body: JSON.stringify({pageIndex: 1})}); const rows = (await r.json()).content.datas;
  return {postings: rows.map(j => ({title: j.name, url: location.origin + '/off-campus/position-detail?positionId=' + j.id}))} }"""
FANRUAN_LINKS = """async (h) => { const config = {campus: [1, 'campus', '/campus/detail?id='], trainee: [3, 'intern', '/intern/detail?id=']};
  const r = await fetch('/api/search.php', {method: 'POST'}); return {postings: []} }"""
BEISEN_LINKS = """async (h) => { const path = "/campus/detail";
  const r = await fetch('/api/Jobad/GetJobAdPageList', {method: 'POST'});
  return {postings: rows.map(j => ({title: j.JobAdName, url: new URL(({"1": "/social/detail", "2": "/campus/detail"})[j.c], location.href).href}))} }"""


def test_a_script_comes_down_to_the_api_it_calls():
    # a tenant's id written in, added on or in a template is the same API
    assert systems.paths(HOTJOB_INLINE) == systems.paths(HOTJOB_JOINED) == systems.paths(HOTJOB_TEMPLATE) \
        == {"/wecruit/positioninfo/listposition"}
    # a role's link the script builds is a page, not the API
    assert systems.paths(ALIBABA) == {"/position/search"}
    assert systems.paths(FANRUAN_LINKS) == {"/api/search.php"}
    assert systems.paths(BEISEN_LINKS) == {"/api/jobad/getjobadpagelist"}
    # one part, or a file, says nothing about the system
    assert systems.paths("fetch('/jobs'); fetch('/JobDetails.html'); fetch('https://x.example/api/v2/jobs?p=1')") == {"/api/v2/jobs"}


def test_three_companies_calling_one_api_are_one_group_and_two_are_not():
    pages = [("云帆科技", "https://careers.yunfan.example/SU1/pb/school.html", HOTJOB_INLINE),
             ("远山能源", "https://wecruit.hotjob.cn/SU2/pb/school.html", HOTJOB_JOINED)]
    assert systems.groups(pages) == []          # two unrelated sites shared /api/job/list by chance
    pages.append(("青松集团", "https://yuanshan.hotjob.cn/SU3/pb/social.html", HOTJOB_TEMPLATE))
    pages.append(("青松集团", "https://yuanshan.hotjob.cn/SU3/pb/interns.html", HOTJOB_TEMPLATE))   # one company twice
    (g,) = systems.groups(pages)
    assert g["paths"] == ["/wecruit/positioninfo/listposition"]
    assert g["companies"] == sorted(["远山能源", "青松集团", "云帆科技"])
    assert g["sites"] == ["careers.yunfan.example", "wecruit.hotjob.cn", "yuanshan.hotjob.cn"]
    # links of pages two systems both build are no group: 星河科技 and two 北森 sites
    assert systems.groups([("星河科技", "https://join.xinghe.example/", FANRUAN_LINKS),
                           ("明舟", "https://mingzhou.zhiye.com/", BEISEN_LINKS),
                           ("扬帆", "https://yangfan.zhiye.com/", BEISEN_LINKS)]) == []


def _scanner(hunt, sites: dict[str, str]):
    ps = Postings(hunt)
    rows = lambda u: {"total": 1, "postings": [{"title": "研发工程师", "url": u + "/1"}]}
    browser = FakeBrowser({u: rows(u) for u in sites.values()})
    sc = Scanner(hunt, ps, NoTool(), browser, lambda: None)
    sc.empty_wait = 0
    for name, u in sites.items():
        hunt.add_company({"name": name, "site": u})
        sc._providers[u] = None
    return sc


def test_a_route_that_joins_two_others_on_one_api_says_so(hunt):
    sites = {"青松物流": "https://talent.qingsong.example/campus/position", "百合到家": "https://talent.baihe.example/campus/position",
             "兰亭游戏": "https://talent.lanting.example/campus/position", "星河科技": "https://careers.xinghe.example/jobs"}
    sc = _scanner(hunt, sites)
    for name in ("青松物流", "百合到家"):
        assert not sc.save_route(name, {"pages": [{"url": sites[name], "script": ALIBABA}]})[1]
    assert sc.kin("百合到家") == [] and sc.shared_systems() == []
    assert not sc.save_route("兰亭游戏", {"pages": [{"url": sites["兰亭游戏"], "script": ALIBABA}]})[1]
    (k,) = sc.kin("兰亭游戏")
    assert k["companies"] == sorted(["百合到家", "青松物流"]) and k["paths"] == ["/position/search"]
    assert [g["companies"] for g in sc.shared_systems()] == [sorted(["百合到家", "青松物流", "兰亭游戏"])]
    # a site on its own API is kin to nobody
    assert not sc.save_route("星河科技", {"pages": [{"url": sites["星河科技"], "script": FANRUAN_LINKS}]})[1]
    assert sc.kin("星河科技") == []


def test_routes_on_a_system_a_built_in_scan_reads_are_no_group(hunt):
    """北森's routes, written before jobhunt/beisen.py, are old routes, not a system still to be built: on a
    zhiye.com address, or on a company's own domain calling its API. HotJob's the same, since jobhunt/hotjob.py."""
    sites = {"诺亚电子": "https://nova.zhiye.com/campus/jobs", "七石科技": "https://qishi.zhiye.com/campus/jobs",
             "华宝能源": "https://careers.huabao.example/campus/jobs"}
    sc = _scanner(hunt, sites)
    for name, u in sites.items():
        sc.postings.set_scan_state(hunt.company(name)["id"], route={"pages": [{"url": u, "script": BEISEN_LINKS}], "sites": [u]})
    assert sc.shared_systems() == [] and sc.kin("华宝能源") == []


def test_hotjob_routes_are_no_group_now_the_scan_reads_hotjob(hunt):
    sites = {"云帆科技": "https://careers.yunfan.example/SU1/pb/school.html", "远山能源": "https://wecruit.hotjob.cn/SU2/pb/school.html",
             "青松集团": "https://yuanshan.hotjob.cn/SU3/pb/social.html"}
    sc = _scanner(hunt, sites)
    for (name, u), script in zip(sites.items(), (HOTJOB_INLINE, HOTJOB_JOINED, HOTJOB_TEMPLATE)):
        sc.postings.set_scan_state(hunt.company(name)["id"], route={"pages": [{"url": u, "script": script}], "sites": [u]})
    assert sc.shared_systems() == []
