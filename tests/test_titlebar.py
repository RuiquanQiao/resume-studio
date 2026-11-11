"""The window's own title bar: geometry and hit test, its message handling (native/TitleBar.cs, run here through
pythonnet with no window), the page's switch (window.js), and that a failure leaves the system title bar."""
import ctypes
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _titlebar as tb  # noqa: E402

if sys.platform != "win32":
    pytest.skip("the title bar is Windows' own", allow_module_level=True)

TB = tb.load()


def box(r):
    return (r.Left, r.Top, r.Right, r.Bottom)


def test_the_title_bar_is_compiled_from_its_source():
    # the dll is committed: a change to TitleBar.cs that wasn't built would silently not run
    assert tb.DLL.stat().st_mtime >= (ROOT / "native" / "TitleBar.cs").stat().st_mtime - 2


def test_buttons_sit_flush_top_right_at_windows_width():
    r = TB.ButtonRects(1000, 1.0, 1.0)
    assert [box(x) for x in r] == [(862, 0, 908, 50), (908, 0, 954, 50), (954, 0, 1000, 50)]


def test_buttons_scale_with_dpi_and_follow_page_zoom_in_height_only():
    r = TB.ButtonRects(2000, 1.5, 1.2)
    assert box(r[2]) == (2000 - 69, 0, 2000, 90)     # 46 DIP wide, 50 CSS px * 1.2 zoom * 1.5 tall
    assert r[0].Left == 2000 - 3 * 69


def test_hit_test_answers_like_the_system_title_bar():
    w, s, edge = 1000, 1.0, 8
    assert TB.HitTest(500, 3, w, s, edge, False, 1.0) == TB.HTTOP
    assert TB.HitTest(5, 3, w, s, edge, False, 1.0) == TB.HTTOPLEFT
    assert TB.HitTest(995, 3, w, s, edge, False, 1.0) == TB.HTTOPRIGHT
    assert TB.HitTest(880, 25, w, s, edge, False, 1.0) == TB.HTMINBUTTON
    assert TB.HitTest(930, 25, w, s, edge, False, 1.0) == TB.HTMAXBUTTON
    assert TB.HitTest(999, 49, w, s, edge, False, 1.0) == TB.HTCLOSE
    assert TB.HitTest(500, 25, w, s, edge, False, 1.0) == TB.HTCAPTION


def test_maximized_window_has_no_top_resize_edge_and_the_corner_closes():
    # maximized, the very top-right pixel must be the close button (throw the mouse into the corner)
    assert TB.HitTest(999, 0, 1000, 1.0, 8, True, 1.0) == TB.HTCLOSE
    assert TB.HitTest(500, 0, 1000, 1.0, 8, True, 1.0) == TB.HTCAPTION


def test_header_keeps_room_for_the_buttons_at_any_zoom():
    assert TB.CaptionCssWidth(1.0) == 138
    assert TB.CaptionCssWidth(1.25) == 110.4
    assert TB.CaptionCssWidth(0) == 138
    assert "138px" in tb.css_script(1.0)


def test_page_keeps_room_only_when_the_title_bar_is_installed(server):
    js = server.req("GET", "/window.js")[1].decode("utf-8")
    assert "toggle('own-titlebar', false)" in js           # the browser, or a failed install: header as before
    server.inst.titlebar["on"] = True
    try:
        assert "toggle('own-titlebar', true)" in server.req("GET", "/window.js")[1].decode("utf-8")
    finally:
        server.inst.titlebar.clear()


def test_window_js_runs_in_head_before_anything_is_drawn():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    head = html.split("</head>")[0]
    assert '<script src="window.js"></script>' in head
    assert head.index("window.js") < head.index("style.css")


def test_the_hit_test_is_answered_on_the_header_with_windows_own_borders_kept():
    bar = TB.Detached()
    lp = lambda x, y: (y << 16) | x                       # noqa: E731
    assert bar.Send(TB.WM_NCHITTEST, 0, lp(930, 25)).Result.ToInt64() == TB.HTMAXBUTTON
    assert bar.Send(TB.WM_NCHITTEST, 0, lp(500, 30)).Result.ToInt64() == TB.HTCAPTION
    assert list(bar.Passed).count(TB.WM_NCHITTEST) == 2   # Windows asked first: its borders come back as they are


def test_pointing_at_maximize_reaches_windows_so_snap_layouts_open():
    # user report 2026-11-11: the snap layouts never showed; the pointer's move over the button was kept from
    # Windows, whose own handling of it is what opens them
    bar = TB.Detached()
    bar.Send(TB.WM_NCMOUSEMOVE, TB.HTMAXBUTTON, 0)
    assert bar.Hover == TB.MAX
    assert TB.WM_NCMOUSEMOVE in list(bar.Passed)


def test_windows_is_told_where_the_buttons_really_are():
    # user report 2026-11-11: restored, the snap layouts never opened. Windows locates the maximize button
    # through WM_GETTITLEBARINFOEX, and its own answer was a 1 px strip at the top edge (the removed top frame)
    bar = TB.Detached()
    from System.Drawing import Point
    bar.TestOrigin = Point(100, 200)
    buf = (ctypes.c_int32 * 35)()
    buf[0] = ctypes.sizeof(buf)
    bar.Send(TB.WM_GETTITLEBARINFOEX, 0, ctypes.addressof(buf))
    rect = lambda i: tuple(buf[1 + 4 * 0:5]) if i == "bar" else tuple(buf[11 + 4 * i:15 + 4 * i])   # noqa: E731
    assert rect(3) == (100 + 908, 200, 100 + 954, 250)      # maximize, as tall as the header
    assert rect(5) == (100 + 954, 200, 1100, 250)
    assert rect(2) == (100 + 862, 200, 100 + 908, 250)
    assert rect("bar") == (100, 200, 1100, 250)
    assert TB.WM_GETTITLEBARINFOEX in list(bar.Passed)      # Windows still fills the rest


def test_a_click_on_a_button_is_the_bars_own():
    # Windows would draw its old-style buttons over ours on a press, and act on its own
    bar = TB.Detached()
    bar.Send(TB.WM_NCLBUTTONDOWN, TB.HTCLOSE, 0)
    bar.Send(TB.WM_NCLBUTTONUP, TB.HTCLOSE, 0)
    assert list(bar.Acted) == [TB.CLOSE]
    assert TB.WM_NCLBUTTONDOWN not in list(bar.Passed) and TB.WM_NCLBUTTONUP not in list(bar.Passed)


def test_a_press_dragged_off_the_button_does_nothing():
    bar = TB.Detached()
    bar.Send(TB.WM_NCLBUTTONDOWN, TB.HTCLOSE, 0)
    bar.Send(TB.WM_NCLBUTTONUP, TB.HTMAXBUTTON, 0)
    assert list(bar.Acted) == []

class _Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class _Window:
    def __init__(self):
        class E:
            pass
        self.events = E()
        self.events.before_show, self.events.loaded = _Event(), _Event()
        self.js = []

    @property
    def native(self):
        raise RuntimeError("no form")

    def evaluate_js(self, s):
        self.js.append(s)


def test_a_failed_install_leaves_the_system_title_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(tb.os, "name", "nt")
    w, on = _Window(), {}
    tb.attach(w, on, log=tmp_path / "err.txt")
    for fn in w.events.before_show:
        fn()
    for fn in w.events.loaded:
        fn()
    assert not on.get("on")            # window.js keeps the header as it was
    assert w.js == []                  # nothing pushed into the page
    assert "no form" in (tmp_path / "err.txt").read_text(encoding="utf-8")


def test_the_window_shows_once_the_page_has_drawn(server):
    # 2026-11-11: the window starts hidden and shows on the page's word, so it never opens as an empty frame
    import threading
    shown = threading.Event()
    server.inst.ready["fn"] = shown.set
    try:
        assert server.req("POST", "/api/drawn", {})[0] == 200
        assert shown.wait(3)
    finally:
        server.inst.ready.clear()
    assert server.req("POST", "/api/drawn", {})[0] == 200            # in the browser: nothing to show


def test_the_page_says_when_its_first_screen_is_drawn():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    main = js[js.index("(async function main()"):]
    assert main.index('"/api/drawn"') > main.index("initWorkspaces();")
