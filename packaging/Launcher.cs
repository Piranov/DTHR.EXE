using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher {
    // CommandLineToArgvW quoting, including embedded quotes and trailing backslashes.
    static string Quote(string value) {
        var s = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') s.Append('\\', slashes + 1);
            s.Append('\\', slashes);
            slashes = 0;
            s.Append(c);
        }
        return s.Append('\\', slashes * 2).Append('"').ToString();
    }

    [STAThread]
    static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        var arguments = new StringBuilder("-m dthr");
#if DESKTOP
        string python = "pythonw.exe";
        arguments.Append(" gui");
#else
        string python = "python.exe";
        if (args.Length == 0) arguments.Append(" --help");
#endif
        foreach (var arg in args) arguments.Append(" ").Append(Quote(arg));
        try {
            var config = new ProcessStartInfo(Path.Combine(root,"runtime",python), arguments.ToString());
            config.UseShellExecute = false;
            config.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            string location = Path.Combine(root,"storage-location.txt");
            if (File.Exists(location) && String.IsNullOrEmpty(Environment.GetEnvironmentVariable("DTHR_DATA"))) {
                string data = Path.GetFullPath(Path.Combine(root, File.ReadAllText(location).Trim()));
                if (Directory.Exists(data)) config.EnvironmentVariables["DTHR_DEFAULT_DATA"] = data;
            }
            using (var worker = Process.Start(config)) {
                worker.WaitForExit();
                return worker.ExitCode;
            }
        } catch (Exception e) {
#if DESKTOP
            MessageBox.Show(e.Message, "DTHR.EXE");
#else
            Console.Error.WriteLine(e.Message);
#endif
            return 1;
        }
    }
}
