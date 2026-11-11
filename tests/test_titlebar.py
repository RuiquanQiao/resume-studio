"""The window's own title bar: geometry and hit test (pure), the page's switch (window.js), and that a failure
leaves the system title bar."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import _titlebar as tb  # noqa: E402


def test_buttons_sit_flush_top_right_at_windows_width():
    r = tb.button_rects(1000, 1.0)
    assert r["close"] == (954, 0, 1000, 50)
    assert r["max"] == (908, 0, 954, 50)
    assert r["min"] == (862, 0, 908, 50)


def test_buttons_scale_with_dpi_and_follow_page_zoom_in_height_only():
    r = tb.button_rects(2000, 1.5, zoom=1.2)
    assert r["close"] == (2000 - 69, 0, 2000, 90)     # 46 DIP wide, 50 CSS px * 1.2 zoom * 1.5 tall
    assert r["min"][0] == 2000 - 3 * 69


def test_hit_test_answers_like_the_system_title_bar():
    w, s, edge = 1000, 1.0, 8
    assert tb.hit_test(500, 3, w, s, edge, maximized=False) == tb.HTTOP
    assert tb.hit_test(5, 3, w, s, edge, maximized=False) == tb.HTTOPLEFT
    assert tb.hit_test(995, 3, w, s, edge, maximized=False) == tb.HTTOPRIGHT
    assert tb.hit_test(880, 25, w, s, edge, maximized=False) == tb.HTMINBUTTON
    assert tb.hit_test(930, 25, w, s, edge, maximized=False) == tb.HTMAXBUTTON
    assert tb.hit_test(999, 49, w, s, edge, maximized=False) == tb.HTCLOSE
    assert tb.hit_test(500, 25, w, s, edge, maximized=False) == tb.HTCAPTION


def test_maximized_window_has_no_top_resize_edge_and_the_corner_closes():
    # maximized, the very top-right pixel must be the close button (throw the mouse into the corner)
    assert tb.hit_test(999, 0, 1000, 1.0, 8, maximized=True) == tb.HTCLOSE
    assert tb.hit_test(500, 0, 1000, 1.0, 8, maximized=True) == tb.HTCAPTION


def test_header_keeps_room_for_the_buttons_at_any_zoom():
    assert tb.caption_css_width(1.0) == 138
    assert tb.caption_css_width(1.25) == 110.4
    assert tb.caption_css_width(0) == 138


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
