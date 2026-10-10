// Resume Studio.exe: a tiny native launcher, so the app can be pinned to Start
// and double-clicked without any file association.
// It only starts scripts/studio.py --window with the skill's own Python; all logic lives there.
// Build: python scripts/build_launcher.py
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
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
        try
        {
            Process.Start(info);
        }
        catch (Exception e)
        {
            return Fail(Text("start") + e.Message + "\n\n" + python);
        }
        return 0;
    }

    // The few messages shown before Python runs, in the computer's language (English otherwise).
    static readonly Dictionary<string, string[]> Messages = new Dictionary<string, string[]>
    {
        // missing studio.py | no Python | Python failed to start
        { "en", new[] {
            "scripts\\studio.py was not found.\n\nResume Studio.exe has to stay in the resume-studio folder; to launch it from elsewhere, make a shortcut or pin it to Start.",
            "Resume Studio needs Python 3.10 or newer.\n\nInstall it from https://www.python.org/downloads/ (tick \"Add python.exe to PATH\"), then open Resume Studio again.",
            "Couldn't start Python: " } },
        { "zh", new[] {
            "找不到 scripts\\studio.py。\n\nResume Studio.exe 要留在 resume-studio 文件夹里；想放到别处，请给它建快捷方式或固定到「开始」。",
            "需要 Python 3.10 或更新版本。\n\n请从 https://www.python.org/downloads/ 安装（勾选 \"Add python.exe to PATH\"），然后再打开 Resume Studio。",
            "无法启动 Python：" } },
        { "ja", new[] {
            "scripts\\studio.py が見つかりません。\n\nResume Studio.exe は resume-studio フォルダーに置いたままにしてください。別の場所から起動したい場合は、ショートカットを作るかスタートにピン留めしてください。",
            "Python 3.10 以降が必要です。\n\nhttps://www.python.org/downloads/ からインストールし（\"Add python.exe to PATH\" にチェック）、もう一度 Resume Studio を開いてください。",
            "Python を起動できません: " } },
        { "ko", new[] {
            "scripts\\studio.py를 찾을 수 없습니다.\n\nResume Studio.exe는 resume-studio 폴더 안에 있어야 합니다. 다른 곳에서 실행하려면 바로 가기를 만들거나 시작 메뉴에 고정하세요.",
            "Python 3.10 이상이 필요합니다.\n\nhttps://www.python.org/downloads/ 에서 설치한 다음(\"Add python.exe to PATH\" 선택) Resume Studio를 다시 여세요.",
            "Python을 시작할 수 없습니다: " } },
        { "es", new[] {
            "No se encuentra scripts\\studio.py.\n\nResume Studio.exe debe quedarse en la carpeta resume-studio; para abrirlo desde otro lugar, crea un acceso directo o ánclalo a Inicio.",
            "Resume Studio necesita Python 3.10 o posterior.\n\nInstálalo desde https://www.python.org/downloads/ (marca \"Add python.exe to PATH\") y vuelve a abrir Resume Studio.",
            "No se pudo iniciar Python: " } },
        { "fr", new[] {
            "scripts\\studio.py est introuvable.\n\nResume Studio.exe doit rester dans le dossier resume-studio ; pour le lancer d'ailleurs, créez un raccourci ou épinglez-le au menu Démarrer.",
            "Resume Studio a besoin de Python 3.10 ou plus récent.\n\nInstallez-le depuis https://www.python.org/downloads/ (cochez \"Add python.exe to PATH\"), puis rouvrez Resume Studio.",
            "Impossible de démarrer Python : " } },
        { "de", new[] {
            "scripts\\studio.py wurde nicht gefunden.\n\nResume Studio.exe muss im Ordner resume-studio bleiben; um es woanders zu starten, lege eine Verknüpfung an oder hefte es an Start an.",
            "Resume Studio braucht Python 3.10 oder neuer.\n\nInstalliere es von https://www.python.org/downloads/ (\"Add python.exe to PATH\" anhaken) und öffne Resume Studio dann erneut.",
            "Python konnte nicht gestartet werden: " } },
    };

    static string Text(string key)
    {
        string lang = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName;
        string[] m = Messages.ContainsKey(lang) ? Messages[lang] : Messages["en"];
        return m[key == "missing" ? 0 : key == "python" ? 1 : 2];
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
