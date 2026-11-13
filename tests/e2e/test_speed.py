"""Speed with a lot of data: 5,000 companies (made up here, nothing of the user's) in Applying.

2026-11-13 the user found every switch of tab or workspace taking a few tenths of a second with 229 companies and
asked that 5,000 stay smooth. Each table then drew every row on every switch (5,000 rows: about 6 s), the server
read the companies' file eight times a look and looked at a file per company (5.6 s), and every change anywhere
fetched the whole overview again (21 MB) and drew it anew. These tests hold what fixed that: rows drawn near the
screen only, a table kept for going back to, one read of each file a look, and an unchanged overview answered with
304. The time limits are several times what a quiet machine takes, so a busy one doesn't fail them.
"""
from __future__ import annotations

import os
import time

import pytest

from conftest import ROOT, edge_args

pytestmark = pytest.mark.e2e
N = 5000


@pytest.fixture(scope="module")
def speed_ctx():
    from playwright.sync_api import sync_playwright

    profile = ROOT / ".cache" / ("pw-profile-" + (os.environ.get("STUDIO_TEST_GROUP") or "speed"))
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), channel="msedge", headless=True,
                                                   args=edge_args(), viewport={"width": 1440, "height": 900})
        yield ctx
        ctx.close()


def many(server, n: int = N) -> None:
    """n companies, some with a role, written straight to the files (adding them one by one would take minutes)."""
    hunt = server.inst.hunt
    cs = [{"id": f"C{i}", "name": f"Company {i:05d}", "aliases": [f"公司{i}"], "industry": ["Software", "金融", "Mining"][i % 3],
           "strength": f"About {i} staff", "site": f"https://careers{i}.example.com",
           "places": [{"kind": "onsite", "area": ["Sydney", "上海", "Berlin"][i % 3]}],
           "created": "2026-11-01T10:00:00", "updated": "2026-11-01T10:00:00"} for i in range(1, n + 1)]
    apps = [{"id": f"A{i}", "company": f"C{i}", "role": f"Engineer {i}", "status": "saved", "channel": "site",
             "created": "2026-11-01T10:00:00", "updated": "2026-11-01T10:00:00"} for i in range(1, n + 1, 10)]
    hunt._write("companies.json", {"next": n + 1, "companies": cs})
    hunt._write("applications.json", {"next": n + 1, "applications": apps, "migrated": list(__import__("jobhunt.store", fromlist=["x"]).MIGRATIONS)})


@pytest.fixture
def big(speed_ctx, server):
    many(server)
    pg = speed_ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server.url)
    pg.evaluate("localStorage.clear()")
    pg.reload()
    pg.wait_for_function("() => typeof S !== 'undefined' && S")
    pg.evaluate("""async () => { setWorkspace('fill'); while (!H.data || H.loading) await new Promise(r => setTimeout(r, 20));
                   await setHuntTab('companies'); }""")
    pg.wait_for_selector(".dbv-view[data-table=companies] tbody tr[data-id]")
    # what the window loads besides (the platforms, the views, the report) has come and been drawn
    pg.wait_for_function("() => F.sites && DBV.tables && !H.loading && REPORT.at > 0")
    pg.wait_for_timeout(500)
    yield pg
    pg.close()
    assert not errors, errors


SWITCH = """async (go) => {
  const frame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  await frame();
  const t = performance.now();
  await eval(go);
  await frame();
  return performance.now() - t;
}"""


def test_a_long_table_draws_only_the_rows_near_the_screen(big):
    rows = big.evaluate("() => document.querySelectorAll('.dbv-view tbody tr[data-id]').length")
    assert 0 < rows < 200, f"{rows} rows drawn of {N}"
    # scrolled to its foot, the last company is there
    big.evaluate("() => { const sc = document.querySelector('.hunt-scroll'); sc.scrollTop = sc.scrollHeight; }")
    big.wait_for_selector(f".dbv-view tbody tr[data-id=C{N}]", timeout=5000)
    assert big.evaluate("() => document.querySelectorAll('.dbv-view tbody tr[data-id]').length") < 300   # the top let go again
    # a search finds one far down at once
    big.fill("#table-search", f"Company {N - 3:05d}")
    big.wait_for_selector(f".dbv-view tbody tr[data-id=C{N - 3}]")
    assert big.evaluate("() => document.querySelectorAll('.dbv-view tbody tr[data-id]').length") == 1


def test_going_back_to_a_table_puts_it_back_as_it_was(big):
    big.evaluate("() => { document.querySelector('.hunt-scroll').scrollTop = 40000; }")
    big.wait_for_timeout(200)
    first = big.evaluate("() => document.querySelector('.dbv-view[data-table=companies]')")
    big.evaluate("() => { window.__table = document.querySelector('.dbv-view[data-table=companies]'); window.__top = document.querySelector('.hunt-scroll').scrollTop; }")
    assert first is not None
    times = []
    for go in ("setHuntTab('apps')", "setHuntTab('companies')", "setWorkspace('resume')", "setWorkspace('fill')"):
        times.append(big.evaluate(SWITCH, go))
    # the very same table, not one drawn anew, where it was read to
    assert big.evaluate("() => document.querySelector('.dbv-view[data-table=companies]') === window.__table")
    assert abs(big.evaluate("() => document.querySelector('.hunt-scroll').scrollTop") - big.evaluate("() => window.__top")) < 2
    assert max(times) < 400, [round(x) for x in times]


def test_an_unchanged_overview_is_not_sent_or_drawn_again(big, server):
    # the companies written before the window opened may still be on their way as a change: asked until it holds still
    same = False
    for _ in range(6):
        big.evaluate("async () => { await (H.loading || null); window.__data = H.data; window.__table = document.querySelector('.dbv-view[data-table=companies]'); }")
        big.evaluate("() => loadHunt({ quiet: true })")
        same = big.evaluate("() => H.data === window.__data && document.querySelector('.dbv-view[data-table=companies]') === window.__table")
        if same:
            break
        big.wait_for_timeout(300)
    assert same, "the same overview was drawn anew every time"
    # a change is fetched and drawn
    server.inst.hunt.update_company("C1", {"industry": "Changed industry"})
    big.evaluate("() => loadHunt({ quiet: true })")
    assert big.evaluate("() => H.data !== window.__data")
    big.wait_for_selector(".dbv-view tbody tr[data-id=C1] >> text=Changed industry")


def test_matching_roles_are_worked_out_once_per_change(server, monkeypatch):
    """The roles in the tracks came from every posting a scan kept, matched anew on every look (11 s after a start
    with 29 MB of postings, half a second after that). Now kept, in memory and on disk, until what they come from
    changes."""
    from jobhunt.postings import Postings
    calls = []
    real = Postings.matched
    monkeypatch.setattr(Postings, "matched", lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    many(server, 50)
    posts = server.inst.hunt.scanner.postings
    posts.merge("C1", [{"title": "Backend Engineer", "url": "https://careers1.example.com/1"},
                       {"title": "Accountant", "url": "https://careers1.example.com/2"}], how="route")
    posts.settings(tracks=[{"name": "Eng", "keywords": "Engineer"}])
    first = server.req("GET", "/api/hunt/matches")[1]
    again = server.req("GET", "/api/hunt/matches")[1]
    assert len(calls) == 1 and first["total"] == 1 and first["postings"] == again["postings"]
    # kept on disk: a new start (nothing in memory) reads it from there
    assert (server.inst.studio.store.cache_dir / "matches.json").is_file()
    # a change in what they come from (a new role, other tracks): worked out again, and shown
    posts.merge("C1", [{"title": "Backend Engineer", "url": "https://careers1.example.com/1"},   # a scan finds them all again
                       {"title": "Accountant", "url": "https://careers1.example.com/2"},
                       {"title": "Data Engineer", "url": "https://careers1.example.com/3"}], how="route")
    assert server.req("GET", "/api/hunt/matches")[1]["total"] == 2 and len(calls) == 2
    posts.settings(tracks=[{"name": "Eng", "keywords": "Accountant"}])
    assert [p["title"] for p in server.req("GET", "/api/hunt/matches")[1]["postings"]] == ["Accountant"]


def test_the_overview_of_many_companies_is_quick(server):
    many(server)
    server.req("GET", "/api/hunt")   # the first look: the postings read
    t = time.perf_counter()
    status, data = server.req("GET", "/api/hunt")
    took = time.perf_counter() - t
    assert status == 200 and len(data["companies"]) == N
    assert took < 4, f"{took:.1f} s for {N} companies"   # 5.6 s before 2026-11-13, about 0.6 s after


def test_a_new_start_shows_the_table_from_what_was_worked_out_before(server, monkeypatch):
    """2026-11-13: every start worked the table out again from the postings (29 MB, 86,000 titles matched against
    the tracks: 9 s), read the list of 34,000 cities to name a few hundred (0.5 s) and built its search indexes to
    find the places written as text (1 s): the window showed "Loading…" for seconds although nothing had changed.
    Now each is kept in the cache folder with what it came from, and a new start with nothing changed reads them."""
    from jobhunt import geo
    from jobhunt.postings import Postings
    from resume.app import create_app
    many(server, 50)
    hunt = server.inst.hunt
    cs = hunt._read_rows("companies.json", "companies")
    sh = geo.find("上海")["id"]
    cs["companies"][0]["hq"] = {"kind": "onsite", "area": "Shanghai", "id": sh}
    hunt._write("companies.json", cs)
    posts = hunt.scanner.postings
    posts.merge("C1", [{"title": "Backend Engineer", "url": "https://careers1.example.com/1"},
                       {"title": "Accountant", "url": "https://careers1.example.com/2"}], how="route")
    posts.settings(tracks=[{"name": "Eng", "keywords": "Engineer"}])
    first = server.req("GET", "/api/hunt")[1]
    geo._write_kept()
    cache = server.inst.studio.store.cache_dir
    assert all((cache / n).is_file() for n in ("summary.json", "changes.json", "located.json", "cities.json"))
    # a new start: another app on the same files, nothing in memory; the postings, the matching and the list of
    # cities may not be needed
    monkeypatch.setattr(geo, "_db", None)
    monkeypatch.setattr(geo, "_memo", {"path": None, "rows": None, "key": ""})
    no = lambda what: (lambda *a, **k: (_ for _ in ()).throw(AssertionError(what + " again")))   # noqa: E731
    monkeypatch.setattr(Postings, "_view", no("the postings read"))
    monkeypatch.setattr(Postings, "_work_out_summary", no("the table worked out"))
    monkeypatch.setattr(geo, "_load", no("the list of cities read"))
    inst = create_app(server.inst.studio.store.path, watch_interval=5, prefs_dir=server.inst.studio.store.path.parent / "local2")
    try:
        overview = next(f for m, r, f in inst.app.routes if m == "GET" and r.pattern == "^/api/hunt$")
        t = time.perf_counter()
        again = overview(None)
        took = time.perf_counter() - t
    finally:
        inst.shutdown()
    pick = lambda d: {c["id"]: (c["postings"]["open"], c["postings"]["matching"], c.get("country")) for c in d["companies"]}   # noqa: E731
    assert pick(again) == pick(first) and pick(again)["C1"] == (2, 1, "CN")
    assert again["geo"] == first["geo"] and again["places"] == first["places"] and again["changes"] == first["changes"]
    assert took < 2, f"{took:.1f} s"   # 11 s on the user's data before; 0.15 s after


def test_the_window_opens_on_applying_with_its_table_drawn(speed_ctx, server):
    """2026-11-13: opened on Applying, the window showed and its table followed seconds later: the table was asked for
    only after the resume, the plugins and the countries, and the resume's document was laid out first though hidden.
    Now the page asks for it in its <head>, the window shows once the table is drawn, and the hidden document after."""
    many(server, 50)
    pg = speed_ctx.new_page()
    pg.goto(server.url)
    pg.evaluate("localStorage.clear(); localStorage.setItem('rs:ws', 'fill')")
    pg.add_init_script("""window.__drawn = null;
        const of = window.fetch;
        window.fetch = function (u, o) {
          if (String(u).includes('/api/drawn')) window.__drawn = { rows: document.querySelectorAll('.dbv-view tbody tr[data-id]').length,
                                                                  doc: !!document.querySelector('#editor .doc-wrap') };
          return of.apply(this, arguments);
        };""")
    pg.reload()
    pg.wait_for_function("() => typeof ui !== 'undefined' && ui.started", timeout=60000)
    at = pg.evaluate("() => window.__drawn")
    assert at and at["rows"] > 0, at            # the window shows with its table, not with "Loading…"
    assert not at["doc"]                         # the hidden document waited
    times = pg.evaluate("""() => { const r = performance.getEntriesByType('resource');
        const end = (s) => (r.find((e) => e.name.endsWith(s)) || {}).responseEnd, start = (s) => (r.find((e) => e.name.endsWith(s)) || {}).startTime;
        return { hunt: start('/api/hunt'), app: end('/app.js'), state: start('/api/state') }; }""")
    assert times["hunt"] < times["app"] and times["state"] < times["app"], times   # asked for before the scripts were in
    pg.wait_for_selector("#editor .doc-wrap", state="attached")                     # and the document is there after
    pg.close()
