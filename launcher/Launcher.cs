// Resume Studio.exe: a tiny native launcher, so the app can be pinned to Start
// and double-clicked without any file association.
// It only starts scripts/studio.py --window with the skill's own Python; all logic lives there.
// Build: python scripts/build_launcher.py
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;

static class Launcher
{
    const string Title = "Resume Studio";

    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "scripts", "studio.py");
        if (!File.Exists(script))
            return Fail("找不到 scripts\\studio.py。\n\nResume Studio.exe 要留在 resume-studio 文件夹里；"
                      + "想放到别处，请给它建快捷方式或固定到「开始」。");

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
            return Fail("需要 Python 3.10 或更新版本。\n\n请从 https://www.python.org/downloads/ 安装"
                      + "（勾选 \"Add python.exe to PATH\"），然后再打开 Resume Studio。");

        string extra = string.Join(" ", args.Select(Quote));
        var info = new ProcessStartInfo(python, prefix + Quote(script) + " --window " + extra)
        {
            WorkingDirectory = root,
            UseShellExecute = false,
        };
        try
        {
            Process.Start(info);
        }
        catch (Exception e)
        {
            return Fail("无法启动 Python：" + e.Message + "\n\n" + python);
        }
        return 0;
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
