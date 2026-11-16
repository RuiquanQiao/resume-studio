"""Scans start only when the user asks, and the user can stop them (user, 2026-11-16: "那个全部扫描按钮感觉有的时候是
自动启动……不准未经我同意就启动", "点了这个按钮之后再点一下应该有权停止").

Each test here fails on the code before the fix: the daily scan was on unless turned off, adding a site scanned its
company by itself, and nothing could stop a run once it had started."""
from __future__ import annotations

import threading
import time

from jobhunt import beisen
from jobhunt.postings import Postings
from jobhunt.scan import Scanner
from test_hunt_agent import FakeBrowser, NoTool, hunt  # noqa: F401

A = "https://a-co.zhiye.com/"
B = "https://b-co.zhiye.com/"


def scanner(hunt, read):
    ps = Postings(hunt)
    hunt.add_company({"name": "甲公司", "sites": [{"url": A}]})
    hunt.add_company({"name": "乙公司", "sites": [{"url": B}]})
    sc = Scanner(hunt, ps, NoTool(), FakeBrowser({}), lambda: None)
    sc._providers.update({A: beisen.PROVIDER, B: beisen.PROVIDER})
    sc.beisen_read = read
    return sc, ps


def wait_for(cond, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_daily_scan_is_off_until_the_user_turns_it_on(hunt):
    ps = Postings(hunt)
    assert ps.scans()["daily"] is False                       # a new data folder: off
    # a folder from before, where "on" was only the default nobody chose: off as well
    hunt._write("scans.json", {"companies": {}, "keywords": [], "daily": True, "last_all": ""})
    assert ps.scans()["daily"] is False
    # turned on by the user in Settings: it stays on
    assert ps.settings(daily=True)["daily"] is True
    assert ps.scans()["daily"] is True


def test_stop_all_ends_the_run_at_once_and_keeps_nothing_half_read(hunt):
    """A company whose list takes long: Stop ends the run at once, its roles stay as they were (none closed for a
    half-read list), and the companies still waiting aren't read."""
    release = threading.Event()
    asked: list[str] = []

    def read(url):
        asked.append(url)
        release.wait(10)   # a slow site, far away
        return {"jobs": [beisen.posting(url.rstrip("/"), {"Id": "1", "JobAdName": "新岗位", "PostURL": url + "job/1"})], "total": 1}

    sc, ps = scanner(hunt, read)
    ps.merge("C1", [{"title": "原有岗位", "url": A + "job/0"}], how="beisen")
    sc.start(["C1", "C2"], every=True)
    assert wait_for(lambda: "C1" in sc.tokens)
    assert sc.batch["all"] is True
    out = sc.stop()
    assert out["stopped"] == ["C1"] and out["dequeued"] == ["C2"]
    assert sc.batch is None and sc.queue == [] and sc.running == {}   # the window shows it ended at once
    assert wait_for(lambda: sc.last_run.get("stopped"), 3)              # the run's thread ends without the site
    release.set()
    assert [p["title"] for p in ps.all("C1") if not p.get("closed")] == ["原有岗位"]   # nothing merged, nothing closed
    assert asked == [A]                                                 # 乙公司 was never read
    assert sc.last_run["stopped"] is True and sc.last_run["reading"] == 0


def test_stopping_one_company_takes_it_off_the_queue_and_the_run_goes_on(hunt):
    sc, ps = scanner(hunt, lambda url: {"jobs": [], "total": 0})
    gate = threading.Event()
    slow = sc.beisen_read

    def read(url):
        if url == A:
            gate.wait(10)
        return slow(url)

    sc.beisen_read = read
    sc.start(["C1", "C2"])
    assert wait_for(lambda: "C1" in sc.tokens)
    assert sc.batch["all"] is False                                     # one company's Scan: Scan all doesn't spin
    out = sc.stop(["C2"])                                               # still waiting: taken off the queue
    assert out == {"stopped": [], "dequeued": ["C2"]} and sc.batch["total"] == 1
    gate.set()
    assert wait_for(lambda: sc.batch is None)
    assert sc.last_run["companies"] == 1 and not sc.last_run["stopped"]


def test_a_stopped_company_can_be_scanned_again_at_once(hunt):
    """The stopped scan's thread may still be waiting on its site; a new scan of the company isn't refused as busy,
    and the old one's ending doesn't clear the new one's state."""
    release = threading.Event()
    calls = []

    def read(url):
        calls.append(url)
        if len(calls) == 1:
            release.wait(10)
        return {"jobs": [], "total": 0}

    sc, _ = scanner(hunt, read)
    first = threading.Thread(target=sc.scan, args=("C1",))
    first.start()
    assert wait_for(lambda: "C1" in sc.tokens)
    sc.stop(["C1"])
    first.join(3)
    assert not first.is_alive()                                         # let go at once, not after the site answers
    second_tok = []
    second = threading.Thread(target=lambda: second_tok.append(sc.scan("C1")))
    second.start()
    second.join(5)
    release.set()
    assert second_tok and "busy" not in second_tok[0] and "stopped" not in second_tok[0]


def test_a_site_added_to_a_company_is_checked_but_not_scanned(hunt):
    sc, _ = scanner(hunt, lambda url: {"jobs": [], "total": 0})
    started: list[str] = []
    sc.start = lambda ids, **kw: started.extend(ids)
    assert sc.check_sites("C1", [A]) == []
    assert started == []
