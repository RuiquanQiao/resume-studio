// Resume Studio.exe: a tiny native launcher, so the app can be pinned to Start
// and double-clicked without any file association.
// It only starts scripts/studio.py --window with the skill's own Python; all logic lives there.
// Build: python scripts/build_launcher.py
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher
{
    const string Title = "Resume Studio";

    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "scripts", "studio.py");
        if (!File.Exists(script))
            return Fail(Text("missing"));

        // 1) the skill's own venv; 2) the Python launcher; 3) any pythonw on PATH.
        // 2 and 3 run once: studio.py builds .venv and re-runs itself inside it.
        string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
        string prefix = "";
        if (!File.Exists(python))
        {
            python = FindOnPath("pyw.exe");
            if (python != null) prefix = "-3 ";
            else python = FindOnPath("pythonw.exe");
        }
        if (python == null)
            return Fail(Text("python"));

        string extra = string.Join(" ", args.Select(Quote));
        var info = new ProcessStartInfo(python, prefix + Quote(script) + " --window " + extra)
        {
            WorkingDirectory = root,
            UseShellExecute = false,
        };
        // the startup window stays from the click until the app's window is ready: Python signals `.ready`
        // (scripts/_bootstrap.py splash_done) and waits for `.go`, sent once the foreground is handed over
        string signal = "Local\\ResumeStudio.Start." + Process.GetCurrentProcess().Id;
        var ready = new EventWaitHandle(false, EventResetMode.ManualReset, signal + ".ready");
        var go = new EventWaitHandle(false, EventResetMode.ManualReset, signal + ".go");
        info.EnvironmentVariables["RESUME_STUDIO_SPLASH"] = signal;
        Process proc;
        try
        {
            proc = Process.Start(info);
        }
        catch (Exception e)
        {
            return Fail(Text("start") + e.Message + "\n\n" + python);
        }
        try
        {
            Splash.Run(root, Text("starting"), ready, go, proc);
        }
        catch (Exception)
        {
            go.Set();   // a startup window that can't be drawn must not hold the app back
        }
        return 0;
    }

    // The few messages shown before Python runs, in the computer's language (English otherwise).
    static readonly Dictionary<string, string[]> Messages = new Dictionary<string, string[]>
    {
        // missing studio.py | no Python | Python failed to start | the startup window
        { "en", new[] {
            "scripts\\studio.py was not found.\n\nResume Studio.exe has to stay in the resume-studio folder; to launch it from elsewhere, make a shortcut or pin it to Start.",
            "Resume Studio needs Python 3.10 or newer.\n\nInstall it from https://www.python.org/downloads/ (tick \"Add python.exe to PATH\"), then open Resume Studio again.",
            "Couldn't start Python: ",
            "Starting…" } },
        { "zh", new[] {
            "找不到 scripts\\studio.py。\n\nResume Studio.exe 要留在 resume-studio 文件夹里；想放到别处，请给它建快捷方式或固定到「开始」。",
            "需要 Python 3.10 或更新版本。\n\n请从 https://www.python.org/downloads/ 安装（勾选 \"Add python.exe to PATH\"），然后再打开 Resume Studio。",
            "无法启动 Python：",
            "正在启动…" } },
        { "ja", new[] {
            "scripts\\studio.py が見つかりません。\n\nResume Studio.exe は resume-studio フォルダーに置いたままにしてください。別の場所から起動したい場合は、ショートカットを作るかスタートにピン留めしてください。",
            "Python 3.10 以降が必要です。\n\nhttps://www.python.org/downloads/ からインストールし（\"Add python.exe to PATH\" にチェック）、もう一度 Resume Studio を開いてください。",
            "Python を起動できません: ",
            "起動しています…" } },
        { "ko", new[] {
            "scripts\\studio.py를 찾을 수 없습니다.\n\nResume Studio.exe는 resume-studio 폴더 안에 있어야 합니다. 다른 곳에서 실행하려면 바로 가기를 만들거나 시작 메뉴에 고정하세요.",
            "Python 3.10 이상이 필요합니다.\n\nhttps://www.python.org/downloads/ 에서 설치한 다음(\"Add python.exe to PATH\" 선택) Resume Studio를 다시 여세요.",
            "Python을 시작할 수 없습니다: ",
            "시작하는 중…" } },
        { "es", new[] {
            "No se encuentra scripts\\studio.py.\n\nResume Studio.exe debe quedarse en la carpeta resume-studio; para abrirlo desde otro lugar, crea un acceso directo o ánclalo a Inicio.",
            "Resume Studio necesita Python 3.10 o posterior.\n\nInstálalo desde https://www.python.org/downloads/ (marca \"Add python.exe to PATH\") y vuelve a abrir Resume Studio.",
            "No se pudo iniciar Python: ",
            "Iniciando…" } },
        { "fr", new[] {
            "scripts\\studio.py est introuvable.\n\nResume Studio.exe doit rester dans le dossier resume-studio ; pour le lancer d'ailleurs, créez un raccourci ou épinglez-le au menu Démarrer.",
            "Resume Studio a besoin de Python 3.10 ou plus récent.\n\nInstallez-le depuis https://www.python.org/downloads/ (cochez \"Add python.exe to PATH\"), puis rouvrez Resume Studio.",
            "Impossible de démarrer Python : ",
            "Démarrage…" } },
        { "de", new[] {
            "scripts\\studio.py wurde nicht gefunden.\n\nResume Studio.exe muss im Ordner resume-studio bleiben; um es woanders zu starten, lege eine Verknüpfung an oder hefte es an Start an.",
            "Resume Studio braucht Python 3.10 oder neuer.\n\nInstalliere es von https://www.python.org/downloads/ (\"Add python.exe to PATH\" anhaken) und öffne Resume Studio dann erneut.",
            "Python konnte nicht gestartet werden: ",
            "Wird gestartet…" } },
    };

    static string Text(string key)
    {
        string lang = ChosenLanguage() ?? CultureInfo.CurrentUICulture.TwoLetterISOLanguageName;
        string[] m = Messages.ContainsKey(lang) ? Messages[lang] : Messages["en"];
        return m[key == "missing" ? 0 : key == "python" ? 1 : key == "start" ? 2 : 3];
    }

    // the interface language picked in the app's Settings (core/i18n.py, ui_lang in .local/prefs.json)
    static string ChosenLanguage()
    {
        try
        {
            string folder = Environment.GetEnvironmentVariable("RESUME_STUDIO_PREFS");
            if (string.IsNullOrEmpty(folder)) folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ".local");
            string json = File.ReadAllText(Path.Combine(folder, "prefs.json"));
            var m = System.Text.RegularExpressions.Regex.Match(json, "\"ui_lang\"\\s*:\\s*\"([a-z]{2})");
            return m.Success ? m.Groups[1].Value : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    static string FindOnPath(string exe)
    {
        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string dir in path.Split(Path.PathSeparator))
        {
            try
            {
                string candidate = Path.Combine(dir.Trim().Trim('"'), exe);
                // skip the Microsoft Store placeholder, which only opens the Store
                if (File.Exists(candidate) && new FileInfo(candidate).Length > 0) return candidate;
            }
            catch (ArgumentException) { }
        }
        return null;
    }

    static string Quote(string s)
    {
        return "\"" + s.Replace("\"", "\\\"") + "\"";
    }

    static int Fail(string message)
    {
        MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
        return 1;
    }
}

// The startup window: on screen within a moment of the click, like the splash screens of Office or Visual
// Studio, so starting Python, WebView2 and the page (several seconds) is never a silent wait. It closes once the
// app's window is up, or when Python is gone, or after two minutes.
sealed class Splash : Form
{
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
    [DllImport("shell32.dll")] static extern int SetCurrentProcessExplicitAppUserModelID([MarshalAs(UnmanagedType.LPWStr)] string id);

    const int W = 360, H = 132;     // DIPs
    readonly string status;
    readonly EventWaitHandle ready, go;
    readonly Process proc;
    readonly Icon icon;
    readonly bool dark;
    readonly Stopwatch clock = Stopwatch.StartNew();
    readonly System.Windows.Forms.Timer tick = new System.Windows.Forms.Timer { Interval = 16 };
    double scale = 1;
    long handedOver = -1;           // ms on the clock when the app was told to show its window

    public static void Run(string root, string status, EventWaitHandle ready, EventWaitHandle go, Process proc)
    {
        try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (Exception) { }   // per monitor: crisp at 150 %
        // the same taskbar button as the app's window (scripts/studio.py set_app_id)
        try { SetCurrentProcessExplicitAppUserModelID("RuiquanQiao.ResumeStudio"); } catch (Exception) { }
        Application.EnableVisualStyles();
        Application.Run(new Splash(root, status, ready, go, proc));
    }

    Splash(string root, string status, EventWaitHandle ready, EventWaitHandle go, Process proc)
    {
        this.status = status;
        this.ready = ready;
        this.go = go;
        this.proc = proc;
        Text = "Resume Studio";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        ShowInTaskbar = true;
        Cursor = Cursors.AppStarting;
        DoubleBuffered = true;
        try { icon = new Icon(Path.Combine(root, "assets", "icon.ico"), 256, 256); Icon = icon; } catch (Exception) { }
        dark = SystemDark();
        BackColor = dark ? Color.FromArgb(0x1C, 0x1D, 0x21) : Color.White;
        // where the user is: the screen under the pointer
        Rectangle area = Screen.FromPoint(Cursor.Position).WorkingArea;
        Location = new Point(area.Left + area.Width / 2, area.Top + area.Height / 2);
        Size = new Size(W, H);
        tick.Tick += OnTick;
    }

    protected override CreateParams CreateParams
    {
        get
        {
            CreateParams p = base.CreateParams;
            p.ClassStyle |= 0x20000;   // CS_DROPSHADOW
            return p;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        uint dpi = 96;
        try { dpi = GetDpiForWindow(Handle); } catch (Exception) { }
        scale = (dpi == 0 ? 96 : dpi) / 96.0;
        Rectangle area = Screen.FromPoint(Cursor.Position).WorkingArea;
        int w = (int)Math.Round(W * scale), h = (int)Math.Round(H * scale);
        Bounds = new Rectangle(area.Left + (area.Width - w) / 2, area.Top + (area.Height - h) / 2, w, h);
        int round = 2;   // Windows 11's rounded corners (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        try { DwmSetWindowAttribute(Handle, 33, ref round, 4); } catch (Exception) { }
        tick.Start();
    }

    static bool SystemDark()
    {
        try
        {
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
                return k != null && Equals(k.GetValue("AppsUseLightTheme"), 0);
        }
        catch (Exception)
        {
            return false;
        }
    }

    void OnTick(object sender, EventArgs e)
    {
        long ms = clock.ElapsedMilliseconds;
        if (handedOver < 0)
        {
            if (ready.WaitOne(0))
            {
                // still the foreground process: let the app's window come up in front, then let it show
                AllowSetForegroundWindow(-1);   // ASFW_ANY
                go.Set();
                handedOver = ms;
            }
            else if (proc.HasExited || ms > 120000)
            {
                go.Set();
                Close();
                return;
            }
        }
        else
        {
            // close once the app's window is on screen, so nothing else shows up in between
            if (AppWindowShown() || ms - handedOver > 3000)
            {
                Close();
                return;
            }
        }
        Invalidate();
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string title);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);

    // a visible "Resume Studio" window of another process: the app's own
    static bool AppWindowShown()
    {
        uint me = (uint)Process.GetCurrentProcess().Id;
        IntPtr h = IntPtr.Zero;
        while ((h = FindWindowEx(IntPtr.Zero, h, null, "Resume Studio")) != IntPtr.Zero)
        {
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            if (pid != me && IsWindowVisible(h)) return true;
        }
        return false;
    }

    int Px(double dip) { return (int)Math.Round(dip * scale); }

    protected override void OnPaint(PaintEventArgs e)
    {
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        Color title = dark ? Color.FromArgb(0xEC, 0xEC, 0xEF) : Color.FromArgb(0x1D, 0x1D, 0x1F);
        Color sub = dark ? Color.FromArgb(0x9D, 0x9D, 0xA4) : Color.FromArgb(0x6E, 0x6E, 0x73);
        Color track = dark ? Color.FromArgb(0x2E, 0x2F, 0x34) : Color.FromArgb(0xE8, 0xE8, 0xED);
        Color accent = dark ? Color.FromArgb(0x6F, 0x86, 0xFF) : Color.FromArgb(0x3D, 0x5A, 0xFE);
        int pad = Px(28), iconSize = Px(44), left = pad + iconSize + Px(16), right = ClientSize.Width - pad;
        int top = (ClientSize.Height - Px(56)) / 2;
        if (icon != null)
            using (Icon sized = new Icon(icon, iconSize, iconSize))
                g.DrawIcon(sized, new Rectangle(pad, top + Px(28) - iconSize / 2, iconSize, iconSize));
        using (Font big = new Font("Segoe UI Semibold", (float)(15 * scale), GraphicsUnit.Pixel))
        using (Font small = new Font("Segoe UI", (float)(12.5 * scale), GraphicsUnit.Pixel))
        {
            TextFormatFlags f = TextFormatFlags.NoPadding | TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis;
            TextRenderer.DrawText(g, "Resume Studio", big, new Rectangle(left, top, right - left, Px(20)), title, BackColor, f);
            TextRenderer.DrawText(g, status, small, new Rectangle(left, top + Px(24), right - left, Px(18)), sub, BackColor, f);
        }
        // an indeterminate bar under the text: a segment sliding across a track, every 1.4 s
        int barY = top + Px(53), barH = Math.Max(2, Px(3)), barW = right - left;
        using (var b = new SolidBrush(track)) g.FillRectangle(b, left, barY, barW, barH);
        double t = (clock.ElapsedMilliseconds % 1400) / 1400.0;
        int seg = barW * 35 / 100;
        int x0 = left + (int)((barW + seg) * EaseInOut(t)) - seg;
        Rectangle on = Rectangle.Intersect(new Rectangle(x0, barY, seg, barH), new Rectangle(left, barY, barW, barH));
        if (on.Width > 0)
            using (var b = new SolidBrush(accent)) g.FillRectangle(b, on);
    }

    static double EaseInOut(double t) { return t < 0.5 ? 2 * t * t : 1 - Math.Pow(-2 * t + 2, 2) / 2; }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        tick.Stop();
        base.OnFormClosed(e);
    }
}
