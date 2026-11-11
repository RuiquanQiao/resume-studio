// The window's own title bar (Windows): the app's header row is the title bar, like VS Code, Teams or Edge.
// Build: python scripts/build_launcher.py (writes native/TitleBar.dll, loaded by scripts/_titlebar.py).
//
// Compiled, not Python: every message to the window passes through here (each mouse move's hit test, each
// paint), and in Python each one waited for the interpreter's lock while other threads worked, so the buttons
// were drawn a moment after the window and hovering lagged (2026-11-11).
//
// What stays with Windows, so nothing the system title bar could do is lost:
//   * the left, right and bottom borders are the system's own: resizing there, Aero Snap, Win+arrows, the shadow;
//   * the top resize edge, the minimize / maximize / close buttons and every empty spot of the header answer
//     Windows' hit test (WM_NCHITTEST) as the system title bar would: dragging the header moves the window, to a
//     screen edge snaps it, a double click maximizes, a right click opens the window menu, and pointing at
//     maximize opens Windows 11's snap layouts.
// How: the form's non-client area loses its top part (WM_NCCALCSIZE), so the page reaches the top edge. The page
// is cut out (a window region) where the buttons and the top resize edge are; there the form paints the buttons
// and answers the hit test. The header's empty spots are `app-region: drag` in the page, which WebView2 treats
// as title bar (its non-client region support, turned on by scripts/_titlebar.py).
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Text;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ResumeStudio
{
    public sealed class TitleBar : NativeWindow
    {
        public const int BtnW = 46;      // Windows 11's caption button width, in DIPs
        public const int HeaderH = 50;   // the page's header height in CSS px (.topbar in web/style.css)
        public const int HTCLIENT = 1, HTCAPTION = 2, HTMINBUTTON = 8, HTMAXBUTTON = 9;
        public const int HTTOP = 12, HTTOPLEFT = 13, HTTOPRIGHT = 14, HTCLOSE = 20;
        public const int MIN = 0, MAX = 1, CLOSE = 2;   // the buttons, left to right

        public const int WM_SIZE = 0x0005, WM_ACTIVATE = 0x0006, WM_SETTINGCHANGE = 0x001A, WM_NCDESTROY = 0x0082;
        public const int WM_NCCALCSIZE = 0x0083, WM_NCHITTEST = 0x0084;
        public const int WM_NCMOUSEMOVE = 0x00A0, WM_NCLBUTTONDOWN = 0x00A1, WM_NCLBUTTONUP = 0x00A2, WM_NCLBUTTONDBLCLK = 0x00A3;
        public const int WM_NCMOUSELEAVE = 0x02A2, WM_DPICHANGED = 0x02E0, WM_SYSCOMMAND = 0x0112, WM_GETTITLEBARINFOEX = 0x033F;
        const int SC_MINIMIZE = 0xF020, SC_MAXIMIZE = 0xF030, SC_RESTORE = 0xF120, SC_CLOSE = 0xF060;

        // colours follow the page's tokens in web/style.css: --bg, --text, --text-3, and --tint-2 laid on --bg
        static readonly Color[] Light = { C(0xEF, 0xEF, 0xF1), C(0x1D, 0x1D, 0x1F), C(0x9D, 0x9D, 0xA4), C(0xDB, 0xDB, 0xDD), C(0xD0, 0xD0, 0xD3) };
        static readonly Color[] Dark = { C(0x0E, 0x0F, 0x12), C(0xEC, 0xEC, 0xEF), C(0x75, 0x75, 0x7E), C(0x2A, 0x2B, 0x2E), C(0x36, 0x37, 0x3A) };
        static readonly Color CloseHover = C(0xC4, 0x2B, 0x1C), CloseDown = C(0xC8, 0x3C, 0x30);   // Windows 11's own
        const int BG = 0, FG = 1, FG_OFF = 2, HOVER = 3, DOWN = 4;
        static Color C(int r, int g, int b) { return Color.FromArgb(r, g, b); }

        // ---- geometry (pure, so the tests cover it) ----------------------------------------------------------

        // client-pixel rectangles of min, max and close, flush with the top-right corner: as tall as the header
        // (which follows the page's zoom), as wide as Windows' own buttons
        public static Rectangle[] ButtonRects(int clientW, double scale, double zoom)
        {
            int w = (int)Math.Round(BtnW * scale), h = (int)Math.Round(HeaderH * zoom * scale);
            return new[] { new Rectangle(clientW - 3 * w, 0, w, h), new Rectangle(clientW - 2 * w, 0, w, h),
                           new Rectangle(clientW - w, 0, w, h) };
        }

        // what Windows should take a point of the form's client area for; only the parts cut out of the page
        // reach the form: the top resize edge and the buttons
        public static int HitTest(int x, int y, int clientW, double scale, int resizeH, bool maximized, double zoom)
        {
            if (!maximized && y < resizeH)
            {
                int corner = resizeH * 2;
                if (x < corner) return HTTOPLEFT;
                if (x >= clientW - corner) return HTTOPRIGHT;
                return HTTOP;
            }
            Rectangle[] r = ButtonRects(clientW, scale, zoom);
            int[] codes = { HTMINBUTTON, HTMAXBUTTON, HTCLOSE };
            for (int i = 0; i < 3; i++)
                if (r[i].Contains(x, y)) return codes[i];
            return HTCAPTION;
        }

        // CSS px the header keeps free on its right for the buttons, at the page's zoom
        public static double CaptionCssWidth(double zoom)
        {
            return Math.Round(BtnW * 3 / (zoom > 0 ? zoom : 1.0), 2);
        }

        public static bool SystemDark()
        {
            try
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
                    return k != null && Equals(k.GetValue("AppsUseLightTheme"), 0);
            }
            catch (Exception) { return false; }
        }

        static int KindOf(IntPtr hit)
        {
            switch ((int)(long)hit)
            {
                case HTMINBUTTON: return MIN;
                case HTMAXBUTTON: return MAX;
                case HTCLOSE: return CLOSE;
                default: return -1;
            }
        }

        // ---- the window ----------------------------------------------------------------------------------------

        readonly Form form;
        readonly Control view;   // the WebView2 control, docked to fill the form
        Color[] colors;
        readonly string iconFont;
        int hover = -1, down = -1;
        bool active = true, tracking;

        public double Zoom { get; private set; }
        public int Hover { get { return hover; } }

        // a bar with no window: its message handling, with what reaches Windows recorded (the tests)
        public readonly List<int> Passed = new List<int>();
        public readonly List<int> Acted = new List<int>();
        readonly bool detached;
        public int TestClientW = 1000, TestDpi = 96;
        public Point TestOrigin = Point.Empty;

        public TitleBar(Form form, Control view)
        {
            this.form = form;
            this.view = view;
            Zoom = 1.0;
            colors = SystemDark() ? Dark : Light;
            iconFont = IconFont();
        }

        TitleBar()
        {
            detached = true;
            Zoom = 1.0;
            colors = Light;
        }

        public static TitleBar Detached() { return new TitleBar(); }

        public void Install()
        {
            form.BackColor = colors[BG];
            form.Paint += OnPaint;
            AssignHandle(form.Handle);
            // recompute the frame now: SWP_FRAMECHANGED | NOSIZE | NOMOVE | NOZORDER | NOACTIVATE
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010);
            // the cut-out follows the page's own size: WinForms lays the page out after the form's WM_SIZE
            view.SizeChanged += delegate { Reshape(); };
            // the page's zoom: WebView2's own event, found by name so this needs no reference to its assembly
            EventInfo ev = view.GetType().GetEvent("ZoomFactorChanged");
            if (ev != null)
                ev.AddEventHandler(view, Delegate.CreateDelegate(ev.EventHandlerType, this,
                    typeof(TitleBar).GetMethod("OnZoom", BindingFlags.NonPublic | BindingFlags.Instance)));
            Reshape();
        }

        void OnZoom(object sender, EventArgs e)
        {
            try
            {
                double z = Convert.ToDouble(view.GetType().GetProperty("ZoomFactor").GetValue(view, null));
                Zoom = z > 0 ? z : 1.0;
                Reshape();
            }
            catch (Exception) { }
        }

        static string IconFont()
        {
            using (var fonts = new InstalledFontCollection())
                foreach (FontFamily f in fonts.Families)
                    if (f.Name == "Segoe Fluent Icons") return f.Name;
            return "Segoe MDL2 Assets";
        }

        // ---- metrics --
        uint Dpi { get { return detached ? (uint)TestDpi : Math.Max(96u, GetDpiForWindow(Handle)); } }
        double Scale { get { return Dpi / 96.0; } }

        int ResizeH()
        {
            try { return GetSystemMetricsForDpi(33, Dpi) + GetSystemMetricsForDpi(92, Dpi); }   // SM_CYSIZEFRAME + SM_CXPADDEDBORDER
            catch (EntryPointNotFoundException) { return (int)Math.Round(8 * Scale); }
        }

        int ClientW()
        {
            if (detached) return TestClientW;
            RECT r;
            GetClientRect(Handle, out r);
            return r.Right;
        }

        bool Maximized { get { return !detached && IsZoomed(Handle); } }

        // ---- the page's cut-out --
        public void Reshape()
        {
            if (detached) return;
            RECT r;
            GetClientRect(view.Handle, out r);
            IntPtr rgn = CreateRectRgn(0, 0, r.Right, r.Bottom);
            IntPtr top = CreateRectRgn(0, 0, r.Right, ResizeH());
            CombineRgn(rgn, rgn, top, 4);   // RGN_DIFF
            DeleteObject(top);
            foreach (Rectangle b in ButtonRects(r.Right, Scale, Zoom))
            {
                IntPtr hole = CreateRectRgn(b.Left, b.Top, b.Right, b.Bottom);
                CombineRgn(rgn, rgn, hole, 4);
                DeleteObject(hole);
            }
            SetWindowRgn(view.Handle, rgn, true);   // the system owns rgn from here on
            InvalidateRect(Handle, IntPtr.Zero, true);
        }

        // ---- painting --
        void OnPaint(object sender, PaintEventArgs e)
        {
            try { Paint(e.Graphics); }
            catch (Exception) { }   // a paint must never take the window down
        }

        void Paint(Graphics g)
        {
            double s = Scale;
            string[] glyphs = { "", Maximized ? "" : "", "" };
            var flags = TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding;
            Rectangle[] rects = ButtonRects(ClientW(), s, Zoom);
            using (var font = new Font(iconFont, (float)(10 * s), GraphicsUnit.Pixel))
                for (int k = 0; k < 3; k++)
                {
                    Color fill = colors[BG], fg = active ? colors[FG] : colors[FG_OFF];
                    if (k == down && k == hover) fill = k == CLOSE ? CloseDown : colors[DOWN];
                    else if (k == hover) fill = k == CLOSE ? CloseHover : colors[HOVER];
                    if (k == CLOSE && k == hover) fg = Color.White;
                    using (var brush = new SolidBrush(fill)) g.FillRectangle(brush, rects[k]);
                    TextRenderer.DrawText(g, glyphs[k], font, rects[k], fg, flags);
                }
        }

        void Repaint()
        {
            if (!detached) InvalidateRect(Handle, IntPtr.Zero, false);
        }

        // ---- messages --
        void Act(int kind)
        {
            Acted.Add(kind);
            if (detached) return;
            int cmd = kind == MIN ? SC_MINIMIZE : kind == CLOSE ? SC_CLOSE : Maximized ? SC_RESTORE : SC_MAXIMIZE;
            PostMessage(Handle, WM_SYSCOMMAND, new IntPtr(cmd), IntPtr.Zero);
        }

        void TrackLeave()
        {
            if (tracking || detached) return;
            var t = new TRACKMOUSEEVENT { cbSize = Marshal.SizeOf(typeof(TRACKMOUSEEVENT)), dwFlags = 0x2 | 0x10, hwndTrack = Handle };
            TrackMouseEvent(ref t);   // TME_LEAVE | TME_NONCLIENT
            tracking = true;
        }

        void PassOn(ref Message m)
        {
            Passed.Add(m.Msg);
            if (detached) { m.Result = new IntPtr(m.Msg == WM_NCHITTEST ? HTCLIENT : 0); return; }
            base.WndProc(ref m);
        }

        // the tests' way in
        public Message Send(int msg, long wParam, long lParam)
        {
            Message m = Message.Create(IntPtr.Zero, msg, new IntPtr(wParam), new IntPtr(lParam));
            Process(ref m);
            return m;
        }

        protected override void WndProc(ref Message m)
        {
            try { Process(ref m); }
            catch (Exception) { PassOn(ref m); }
        }

        void Process(ref Message m)
        {
            switch (m.Msg)
            {
                case WM_NCCALCSIZE:
                    if (m.WParam == IntPtr.Zero) break;
                    {
                        int top = Marshal.ReadInt32(m.LParam, 4);   // NCCALCSIZE_PARAMS.rgrc[0].top
                        PassOn(ref m);                               // the system's left, right and bottom borders stay
                        // maximized, Windows pushes the frame past the screen's edge: keep the page on screen
                        Marshal.WriteInt32(m.LParam, 4, top + (Maximized ? ResizeH() : 0));
                        return;
                    }
                case WM_NCHITTEST:
                    {
                        PassOn(ref m);
                        if ((long)m.Result != HTCLIENT) return;      // the system's own borders
                        long lp = (long)m.LParam;
                        var pt = new POINT { X = (short)(lp & 0xFFFF), Y = (short)((lp >> 16) & 0xFFFF) };
                        if (detached) { pt.X -= TestOrigin.X; pt.Y -= TestOrigin.Y; }
                        else ScreenToClient(Handle, ref pt);
                        m.Result = new IntPtr(HitTest(pt.X, pt.Y, ClientW(), Scale, ResizeH(), Maximized, Zoom));
                        return;
                    }
                case WM_GETTITLEBARINFOEX:
                    {
                        // Windows 11 finds the maximize button through this to open the snap layouts. Its own answer
                        // comes from the top frame removed above: restored, a 1 px strip at the top edge
                        PassOn(ref m);
                        if (m.LParam == IntPtr.Zero) return;
                        Point o = TestOrigin;
                        if (!detached)
                        {
                            var p = new POINT();
                            ClientToScreen(Handle, ref p);
                            o = new Point(p.X, p.Y);
                        }
                        Rectangle[] r = ButtonRects(ClientW(), Scale, Zoom);
                        WriteRect(m.LParam, 4, new Rectangle(o.X, o.Y, ClientW(), r[CLOSE].Height));   // rcTitleBar
                        int[] slot = { 2, 3, 5 };   // rgrect: 0, 1 reserved, 4 the help button
                        for (int k = 0; k < 3; k++)
                            WriteRect(m.LParam, 44 + 16 * slot[k], new Rectangle(r[k].X + o.X, r[k].Y + o.Y, r[k].Width, r[k].Height));
                        return;
                    }
                case WM_NCMOUSEMOVE:
                    {
                        int kind = KindOf(m.WParam);
                        TrackLeave();
                        if (kind != hover) { hover = kind; Repaint(); }
                        // and on to Windows: its own handling of the pointer over HTMAXBUTTON opens the snap layouts
                        break;
                    }
                case WM_NCMOUSELEAVE:
                    tracking = false;
                    if (hover >= 0 || down >= 0) { hover = down = -1; Repaint(); }
                    break;
                case WM_NCLBUTTONDOWN:
                case WM_NCLBUTTONDBLCLK:
                    {
                        int kind = KindOf(m.WParam);
                        if (kind < 0) break;
                        down = hover = kind;
                        Repaint();
                        m.Result = IntPtr.Zero;   // the system would draw its old buttons over ours
                        return;
                    }
                case WM_NCLBUTTONUP:
                    {
                        int kind = KindOf(m.WParam);
                        if (kind >= 0)
                        {
                            int was = down;
                            down = -1;
                            Repaint();
                            if (was == kind) Act(kind);
                            m.Result = IntPtr.Zero;
                            return;
                        }
                        if (down >= 0) { down = -1; Repaint(); }
                        break;
                    }
                case WM_ACTIVATE:
                    active = ((long)m.WParam & 0xFFFF) != 0;
                    Repaint();
                    break;
                case WM_SIZE:
                    PassOn(ref m);
                    Repaint();   // maximize <-> restore glyph
                    return;
                case WM_DPICHANGED:
                    PassOn(ref m);
                    Reshape();
                    return;
                case WM_SETTINGCHANGE:
                    if (m.LParam != IntPtr.Zero && Marshal.PtrToStringUni(m.LParam) == "ImmersiveColorSet")
                    {
                        colors = SystemDark() ? Dark : Light;
                        if (form != null) form.BackColor = colors[BG];
                        Repaint();
                    }
                    break;
                case WM_NCDESTROY:
                    PassOn(ref m);
                    if (!detached) ReleaseHandle();
                    return;
            }
            PassOn(ref m);
        }

        static void WriteRect(IntPtr p, int offset, Rectangle r)
        {
            Marshal.WriteInt32(p, offset, r.Left);
            Marshal.WriteInt32(p, offset + 4, r.Top);
            Marshal.WriteInt32(p, offset + 8, r.Right);
            Marshal.WriteInt32(p, offset + 12, r.Bottom);
        }

        // ---- Win32 --
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
        [StructLayout(LayoutKind.Sequential)] struct TRACKMOUSEEVENT { public int cbSize; public int dwFlags; public IntPtr hwndTrack; public int dwHoverTime; }

        [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out RECT r);
        [DllImport("user32.dll")] static extern bool ScreenToClient(IntPtr h, ref POINT p);
        [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr h, ref POINT p);
        [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr h);
        [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr h);
        [DllImport("user32.dll")] static extern int GetSystemMetricsForDpi(int index, uint dpi);
        [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] static extern int SetWindowRgn(IntPtr h, IntPtr rgn, bool redraw);
        [DllImport("user32.dll")] static extern bool InvalidateRect(IntPtr h, IntPtr rect, bool erase);
        [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, int msg, IntPtr wp, IntPtr lp);
        [DllImport("user32.dll")] static extern bool TrackMouseEvent(ref TRACKMOUSEEVENT t);
        [DllImport("gdi32.dll")] static extern IntPtr CreateRectRgn(int l, int t, int r, int b);
        [DllImport("gdi32.dll")] static extern int CombineRgn(IntPtr dst, IntPtr a, IntPtr b, int mode);
        [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr o);
    }
}
