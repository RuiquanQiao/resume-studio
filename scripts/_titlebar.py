"""The window's own title bar (Windows): the app's header row is the title bar, like VS Code, Teams or Edge.

What stays with Windows, so nothing the system title bar could do is lost:
  * the left, right and bottom borders are the system's own, so resizing there, Aero Snap, Win+arrows and the
    shadow work as before;
  * the top resize edge, the minimize / maximize / close buttons and every empty spot of the header answer
    Windows' hit test (WM_NCHITTEST) as the system title bar would: dragging the header moves the window,
    dragging it to a screen edge snaps it, a double click maximizes, a right click opens the window menu, and
    pointing at maximize shows Windows 11's snap layouts.

How: the form's non-client area loses its top part (WM_NCCALCSIZE), so the page reaches the top edge. The page
itself is cut out (a window region) where the buttons and the top resize edge are; there the form paints the
buttons and answers the hit test. The header's empty spots are marked `app-region: drag` in the page and
WebView2 treats them as title bar (its non-client region support).
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

BTN_W = 46          # Windows 11's caption button width, in DIPs
HEADER_H = 50       # the page's header height in CSS px (.topbar in style.css)
KINDS = ("min", "max", "close")

HTCLIENT, HTCAPTION, HTMINBUTTON, HTMAXBUTTON = 1, 2, 8, 9
HTTOP, HTTOPLEFT, HTTOPRIGHT, HTCLOSE = 12, 13, 14, 20
HT_OF = {"min": HTMINBUTTON, "max": HTMAXBUTTON, "close": HTCLOSE}
KIND_OF = {v: k for k, v in HT_OF.items()}

# colours follow the page's tokens in style.css: --bg, --text, --text-3, and --tint-2 laid on --bg
LIGHT = {"bg": (0xEF, 0xEF, 0xF1), "fg": (0x1D, 0x1D, 0x1F), "fg_off": (0x9D, 0x9D, 0xA4),
         "hover": (0xDB, 0xDB, 0xDD), "down": (0xD0, 0xD0, 0xD3)}
DARK = {"bg": (0x0E, 0x0F, 0x12), "fg": (0xEC, 0xEC, 0xEF), "fg_off": (0x75, 0x75, 0x7E),
        "hover": (0x2A, 0x2B, 0x2E), "down": (0x36, 0x37, 0x3A)}
CLOSE_HOVER, CLOSE_DOWN = (0xC4, 0x2B, 0x1C), (0xC8, 0x3C, 0x30)   # Windows 11's own close button


# ---- geometry (pure, so the tests cover it) ---------------------------------------------------------------

def button_rects(client_w: int, scale: float, zoom: float = 1.0) -> dict[str, tuple[int, int, int, int]]:
    """Client-pixel (left, top, right, bottom) of each button, flush with the top-right corner. As tall as the
    header (which follows the page's zoom), as wide as Windows' own buttons."""
    w, h = round(BTN_W * scale), round(HEADER_H * zoom * scale)
    right = client_w
    out = {}
    for kind in reversed(KINDS):
        out[kind] = (right - w, 0, right, h)
        right -= w
    return out


def hit_test(x: int, y: int, client_w: int, scale: float, resize_h: int, maximized: bool,
             zoom: float = 1.0) -> int:
    """What Windows should take a point of the form's client area for. Only the parts cut out of the page reach
    the form: the top resize edge and the buttons."""
    if not maximized and y < resize_h:
        corner = resize_h * 2
        if x < corner:
            return HTTOPLEFT
        if x >= client_w - corner:
            return HTTOPRIGHT
        return HTTOP
    for kind, (l, t, r, b) in button_rects(client_w, scale, zoom).items():
        if l <= x < r and t <= y < b:
            return HT_OF[kind]
    return HTCAPTION


def caption_css_width(zoom: float) -> float:
    """CSS px the header keeps free on its right for the buttons, at the page's zoom."""
    return round(BTN_W * len(KINDS) / (zoom or 1.0), 2)


def system_dark() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


# ---- the Windows part ---------------------------------------------------------------------------------------

WM_SIZE, WM_ACTIVATE, WM_SETTINGCHANGE, WM_NCDESTROY = 0x0005, 0x0006, 0x001A, 0x0082
WM_NCCALCSIZE, WM_NCHITTEST, WM_NCACTIVATE = 0x0083, 0x0084, 0x0086
WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP, WM_NCLBUTTONDBLCLK = 0x00A0, 0x00A1, 0x00A2, 0x00A3
WM_NCMOUSELEAVE, WM_DPICHANGED, WM_SYSCOMMAND = 0x02A2, 0x02E0, 0x0112
SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE, SC_CLOSE = 0xF020, 0xF030, 0xF120, 0xF060
SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 33, 92
SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010   # + NOSIZE NOMOVE NOZORDER NOACTIVATE
RGN_DIFF = 4
TME_LEAVE, TME_NONCLIENT = 0x2, 0x10

LRESULT = ctypes.c_ssize_t
SUBCLASSPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                  ctypes.c_size_t, ctypes.c_size_t)


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("hwndTrack", wintypes.HWND),
                ("dwHoverTime", wintypes.DWORD)]


def _api():
    u, g, c = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.comctl32
    c.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
    c.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    c.DefSubclassProc.restype = LRESULT
    c.RemoveWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    u.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wintypes.UINT]
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u.IsZoomed.argtypes = [wintypes.HWND]
    u.GetDpiForWindow.argtypes = [wintypes.HWND]
    u.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
    g.CreateRectRgn.restype = wintypes.HANDLE
    g.CombineRgn.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_int]
    g.DeleteObject.argtypes = [wintypes.HANDLE]
    return u, g, c


def _signed_xy(lparam: int) -> tuple[int, int]:
    x, y = lparam & 0xFFFF, (lparam >> 16) & 0xFFFF
    return (x - 0x10000 if x & 0x8000 else x), (y - 0x10000 if y & 0x8000 else y)


class TitleBar:
    """Installed on the pywebview form on its own (GUI) thread, before the form is shown."""

    def __init__(self, form) -> None:
        self.form = form
        self.hwnd = int(form.Handle.ToInt64())
        self.view = form.webview                       # the WebView2 control, docked to fill the form
        self.view_hwnd = int(self.view.Handle.ToInt64())
        self.u, self.g, self.c = _api()
        self.hover: str | None = None
        self.down: str | None = None
        self.active = True
        self.zoom = 1.0
        self.tracking = False
        self.colors = DARK if system_dark() else LIGHT
        self._proc = SUBCLASSPROC(self._wndproc)       # kept alive as long as the window
        self._font_name = self._icon_font()

    # -- install --
    def install(self) -> None:
        self._apply_colors()
        self.form.Paint += self._on_paint
        if not self.c.SetWindowSubclass(self.hwnd, self._proc, 1, 0):
            raise OSError("SetWindowSubclass failed")
        self.u.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED)   # recompute the frame now
        self.view.ZoomFactorChanged += self._on_zoom
        # the cut-out follows the page's own size: WinForms lays the page out after the form's WM_SIZE
        self.view.SizeChanged += self._on_view_size
        self._reshape()

    def _on_view_size(self, sender, e) -> None:
        try:
            self._reshape()
        except Exception:
            pass

    def prepare_webview(self, core) -> None:
        """Before the first navigation: let `app-region: drag` in the page act as title bar."""
        core.Settings.IsNonClientRegionSupportEnabled = True

    # -- metrics --
    @property
    def scale(self) -> float:
        return (self.u.GetDpiForWindow(self.hwnd) or 96) / 96

    def resize_h(self) -> int:
        dpi = self.u.GetDpiForWindow(self.hwnd) or 96
        try:
            gm = ctypes.windll.user32.GetSystemMetricsForDpi
            return gm(SM_CYSIZEFRAME, dpi) + gm(SM_CXPADDEDBORDER, dpi)
        except AttributeError:
            return round(8 * dpi / 96)

    def client_w(self) -> int:
        r = wintypes.RECT()
        self.u.GetClientRect(self.hwnd, ctypes.byref(r))
        return r.right

    def maximized(self) -> bool:
        return bool(self.u.IsZoomed(self.hwnd))

    # -- the page's cut-out --
    def _reshape(self) -> None:
        """Cut the top resize edge and the buttons out of the page, so those points reach the form."""
        r = wintypes.RECT()
        self.u.GetClientRect(self.view_hwnd, ctypes.byref(r))
        rgn = self.g.CreateRectRgn(0, 0, r.right, r.bottom)
        top = self.g.CreateRectRgn(0, 0, r.right, self.resize_h())
        self.g.CombineRgn(rgn, rgn, top, RGN_DIFF)
        self.g.DeleteObject(top)
        for l, t, rr, b in button_rects(r.right, self.scale, self.zoom).values():
            hole = self.g.CreateRectRgn(l, t, rr, b)
            self.g.CombineRgn(rgn, rgn, hole, RGN_DIFF)
            self.g.DeleteObject(hole)
        self.u.SetWindowRgn(self.view_hwnd, rgn, True)   # the system owns rgn from here on
        self.u.InvalidateRect(self.hwnd, None, True)

    # -- painting --
    def _icon_font(self) -> str:
        from System.Drawing.Text import InstalledFontCollection
        names = {f.Name for f in InstalledFontCollection().Families}
        return "Segoe Fluent Icons" if "Segoe Fluent Icons" in names else "Segoe MDL2 Assets"

    def _apply_colors(self) -> None:
        from System.Drawing import Color
        self.form.BackColor = Color.FromArgb(*self.colors["bg"])

    def _on_paint(self, sender, e) -> None:
        try:
            self._paint(e.Graphics)
        except Exception:   # a paint must never take the window down
            pass

    def _paint(self, g) -> None:
        from System.Drawing import Color, Font, GraphicsUnit, Rectangle, SolidBrush
        from System.Windows.Forms import TextFormatFlags, TextRenderer
        s, c = self.scale, self.colors
        font = Font(self._font_name, 10 * s, GraphicsUnit.Pixel)
        glyphs = {"min": "", "max": "" if self.maximized() else "", "close": ""}
        flags = TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding
        for kind, (l, t, r, b) in button_rects(self.client_w(), s, self.zoom).items():
            rect = Rectangle(l, t, r - l, b - t)
            fill, fg = c["bg"], (c["fg"] if self.active else c["fg_off"])
            if kind == self.down and kind == self.hover:
                fill = CLOSE_DOWN if kind == "close" else c["down"]
            elif kind == self.hover:
                fill = CLOSE_HOVER if kind == "close" else c["hover"]
            if kind == "close" and kind == self.hover:
                fg = (255, 255, 255)
            brush = SolidBrush(Color.FromArgb(*fill))
            g.FillRectangle(brush, rect)
            brush.Dispose()
            TextRenderer.DrawText(g, glyphs[kind], font, rect, Color.FromArgb(*fg), flags)
        font.Dispose()

    def _repaint(self) -> None:
        self.u.InvalidateRect(self.hwnd, None, False)

    # -- the page's zoom --
    def _on_zoom(self, sender, e) -> None:
        self.zoom = float(self.view.ZoomFactor) or 1.0
        self._reshape()
        self.push_css()

    def css_script(self) -> str:
        return ("document.documentElement.style.setProperty('--caption-w', '%spx')" % caption_css_width(self.zoom))

    def push_css(self) -> None:
        try:
            self.view.CoreWebView2.ExecuteScriptAsync(self.css_script())
        except Exception:
            pass

    # -- messages --
    def _act(self, kind: str) -> None:
        cmd = {"min": SC_MINIMIZE, "max": SC_RESTORE if self.maximized() else SC_MAXIMIZE, "close": SC_CLOSE}[kind]
        self.u.PostMessageW(self.hwnd, WM_SYSCOMMAND, cmd, 0)

    def _track_leave(self) -> None:
        if not self.tracking:
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE | TME_NONCLIENT, self.hwnd, 0)
            self.u.TrackMouseEvent(ctypes.byref(tme))
            self.tracking = True

    def _wndproc(self, hwnd, msg, wp, lp, _id, _data):
        try:
            return self._handle(hwnd, msg, wp, lp)
        except Exception:
            return self.c.DefSubclassProc(hwnd, msg, wp, lp)

    def _handle(self, hwnd, msg, wp, lp):
        dsp = self.c.DefSubclassProc
        if msg == WM_NCCALCSIZE and wp:
            params = NCCALCSIZE_PARAMS.from_address(lp)
            top = params.rgrc[0].top
            res = dsp(hwnd, msg, wp, lp)     # the system's left, right and bottom borders stay
            # maximized, Windows pushes the frame past the screen's edge: keep the page on screen
            params.rgrc[0].top = top + (self.resize_h() if self.maximized() else 0)
            return res
        if msg == WM_NCHITTEST:
            res = dsp(hwnd, msg, wp, lp)
            if res != HTCLIENT:
                return res                    # the system's own borders
            pt = wintypes.POINT(*_signed_xy(lp))
            self.u.ScreenToClient(hwnd, ctypes.byref(pt))
            return hit_test(pt.x, pt.y, self.client_w(), self.scale, self.resize_h(), self.maximized(), self.zoom)
        if msg == WM_NCMOUSEMOVE:
            kind = KIND_OF.get(wp)
            self._track_leave()
            if kind != self.hover:
                self.hover = kind
                self._repaint()
            # and on to Windows: its own handling of the pointer over HTMAXBUTTON is what opens the snap layouts
            # (swallowing it here kept them from ever showing, user report 2026-11-11)
        elif msg == WM_NCMOUSELEAVE:
            self.tracking = False
            if self.hover or self.down:
                self.hover = self.down = None
                self._repaint()
        elif msg in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and wp in KIND_OF:
            self.down = self.hover = KIND_OF[wp]
            self._repaint()
            return 0                          # the system would draw its old buttons over ours
        elif msg == WM_NCLBUTTONUP and wp in KIND_OF:
            kind, self.down = self.down, None
            self._repaint()
            if kind == KIND_OF[wp]:
                self._act(kind)
            return 0
        elif msg in (WM_NCLBUTTONUP,) and self.down:
            self.down = None
            self._repaint()
        elif msg == WM_ACTIVATE:
            self.active = (wp & 0xFFFF) != 0
            self._repaint()
        elif msg == WM_SIZE:
            res = dsp(hwnd, msg, wp, lp)
            self._repaint()                   # maximize <-> restore glyph
            return res
        elif msg == WM_DPICHANGED:
            res = dsp(hwnd, msg, wp, lp)
            self._reshape()
            return res
        elif msg == WM_SETTINGCHANGE and lp:
            if ctypes.wstring_at(lp) == "ImmersiveColorSet":
                self.colors = DARK if system_dark() else LIGHT
                self._apply_colors()
                self._repaint()
        elif msg == WM_NCDESTROY:
            self.c.RemoveWindowSubclass(hwnd, self._proc, 1)
        return dsp(hwnd, msg, wp, lp)


def attach(window, on: dict, log=None) -> None:
    """Hook `window` (a pywebview Window, before webview.start) so its header row is the title bar.
    `on["on"]` turns True once installed; the page reads it (window.js) to keep room for the buttons.
    Anything going wrong leaves the system title bar, and the page as it was."""
    if os.name != "nt":
        return
    state: dict = {}

    def before_show() -> None:
        try:
            form = window.native
            bar = TitleBar(form)
            browser = form.browser
            load_url = browser.load_url

            def load_with_regions(url):   # the first navigation starts here, right after WebView2 is ready
                try:
                    bar.prepare_webview(browser.webview.CoreWebView2)
                except Exception as exc:
                    _report(log, exc)
                browser.load_url = load_url
                return load_url(url)

            browser.load_url = load_with_regions
            bar.install()
            state["bar"] = bar
            on["on"] = True
        except Exception as exc:
            _report(log, exc)

    def loaded() -> None:
        # runs on a worker thread: touch no WinForms / WebView2 object here (that blocks on the GUI thread
        # while holding the GIL the GUI thread needs for the title bar's messages); bar.zoom is kept by _on_zoom
        bar = state.get("bar")
        if bar:
            window.evaluate_js(bar.css_script())

    window.events.before_show += before_show
    window.events.loaded += loaded


def _report(log, exc: BaseException) -> None:
    if log:
        try:
            import traceback
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        except OSError:
            pass
    print(f"  title bar: {exc}", file=sys.stderr)
