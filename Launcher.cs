// PalmSentinel V2 -- launcher for a SOURCE checkout.
//
//   csc.exe -nologo -target:winexe -r:System.Windows.Forms.dll -out:PalmSentinel.exe Launcher.cs
//
// This executable is deliberately *not* a standalone build and says so.  It
// runs the application from a source checkout with a Python interpreter that
// already exists on the machine.  The standalone build -- the one that needs no
// Python at all -- is `dist/PalmSentinelV2/PalmSentinelV2.exe`, produced by
// `pyinstaller PalmSentinelV2.spec`.
//
// The previous version's launcher was that name for the opposite promise.  It
// searched a list of guessed Python locations, ran `desktop_app.py` with whatever
// interpreter it found, and reported nothing when the five packages the
// application needs were missing from that interpreter -- so a double-click on a
// machine with a bare Python produced a launcher that exited with a code and no
// window and no explanation.  Two changes fix that class of failure outright:
//
//   1. A preflight imports every runtime dependency in the chosen interpreter
//      before the window is started, and any that are absent are named, together
//      with the one command that installs them.
//   2. A failure is always shown as a dialog and always written to a log next to
//      the executable, because a GUI process has no console to print to.
//
// It also prefers the packaged build when one is sitting beside it, so the same
// double-click does the right thing in either layout.

using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace PalmSentinelLauncher
{
    public static class Program
    {
        private const string AppName = "PalmSentinel V2";

        //: Modules the application imports at runtime.  Kept in one place so the
        //: preflight and its message cannot drift apart.
        private static readonly string[] RequiredModules =
        {
            "flask", "webview", "bottle", "cv2", "numpy", "PIL"
        };

        private const string InstallHint =
            "pip install -r requirements.txt";

        [STAThread]
        public static int Main(string[] args)
        {
            string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            bool quiet = IsCheckMode(args);
            StringBuilder log = new StringBuilder();
            log.AppendLine(AppName + " launcher");
            log.AppendLine("  launcher   : " + System.Reflection.Assembly.GetExecutingAssembly().Location);
            log.AppendLine("  directory  : " + appDir);
            log.AppendLine("  arguments  : " + (args == null || args.Length == 0 ? "(none)" : string.Join(" ", args)));

            try
            {
                // 1. A packaged build beside the launcher wins: it carries its own
                //    interpreter and needs nothing installed.
                string packaged = FindPackagedBuild(appDir);
                if (packaged != null)
                {
                    log.AppendLine("  mode       : packaged build");
                    log.AppendLine("  target     : " + packaged);
                    return Run(packaged, args, appDir, quiet, log);
                }

                // 2. Otherwise run from source, but only with an interpreter that
                //    can actually import what the application needs.
                string script = Path.Combine(appDir, "desktop_app.py");
                if (!File.Exists(script))
                {
                    return Fail(quiet, log,
                        "Could not find desktop_app.py next to the launcher, and no packaged build was found either.\r\n\r\n" +
                        "Looked in:\r\n  " + appDir + "\r\n\r\n" +
                        "Either run this from the project folder, or use the packaged build at\r\n" +
                        "  dist\\PalmSentinelV2\\PalmSentinelV2.exe");
                }

                string[] candidates = FindPythonCandidates(appDir);
                if (candidates.Length == 0)
                {
                    return Fail(quiet, log,
                        "No Python interpreter was found, so the application cannot be started from source.\r\n\r\n" +
                        "This launcher needs Python 3.11 or newer on PATH (or a venv in .venv).\r\n" +
                        "The packaged build needs neither Python nor any package:\r\n" +
                        "  dist\\PalmSentinelV2\\PalmSentinelV2.exe");
                }

                log.AppendLine("  python     : searched " + candidates.Length + " interpreter(s)");

                string chosen = null;
                string missing = null;
                StringBuilder tried = new StringBuilder();
                foreach (string python in candidates)
                {
                    string result = Preflight(python);
                    log.AppendLine("    - " + python + " -> " + (result.Length == 0 ? "ok" : result));
                    if (result.Length == 0)
                    {
                        chosen = python;
                        break;
                    }
                    missing = result;
                    tried.AppendLine("  " + python + ": " + result);
                }

                if (chosen == null)
                {
                    return Fail(quiet, log,
                        "Python was found, but no interpreter had the packages " + AppName + " needs.\r\n\r\n" +
                        tried.ToString() + "\r\n" +
                        "Install them into the interpreter you intend to use:\r\n\r\n" +
                        "    " + InstallHint + "\r\n\r\n" +
                        "Or use the packaged build, which needs nothing installed:\r\n" +
                        "  dist\\PalmSentinelV2\\PalmSentinelV2.exe");
                }

                log.AppendLine("  mode       : source checkout");
                log.AppendLine("  interpreter: " + chosen);

                // A check run needs the interpreter's output, so it uses
                // python.exe.  An ordinary launch must not flash a console, which
                // is what pythonw.exe is for -- the previous version had that
                // right and only ever guessed at the interpreter's contents.
                string runner = quiet ? chosen : Windowless(chosen);
                return Run(runner, new[] { script }.Combine(args), appDir, quiet, log);
            }
            catch (Exception ex)
            {
                return Fail(quiet, log, "Unexpected failure: " + ex.Message);
            }
        }

        private static bool IsCheckMode(string[] args)
        {
            if (args == null || args.Length == 0) return false;
            string first = args[0];
            return first == "--check" || first == "--test" || first == "-v" || first == "--version"
                || first == "--selftest";
        }

        /// <summary>A packaged build next to this launcher, if there is one.</summary>
        private static string FindPackagedBuild(string appDir)
        {
            string[] candidates =
            {
                Path.Combine(appDir, @"dist\PalmSentinelV2\PalmSentinelV2.exe"),
                Path.Combine(appDir, "PalmSentinelV2.exe")
            };
            foreach (string candidate in candidates)
            {
                if (File.Exists(candidate)) return candidate;
            }
            return null;
        }

        /// <summary>Interpreters worth trying, most specific first.</summary>
        private static string[] FindPythonCandidates(string appDir)
        {
            System.Collections.Generic.List<string> found = new System.Collections.Generic.List<string>();

            foreach (string venv in new[] { @".venv\Scripts\python.exe", @"venv\Scripts\python.exe" })
            {
                string path = Path.Combine(appDir, venv);
                if (File.Exists(path)) found.Add(path);
            }

            string[] versions = { "Python314", "Python313", "Python312", "Python311" };
            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string progFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            foreach (string version in versions)
            {
                string[] roots = { Path.Combine(localApp, "Programs", "Python"), progFiles, @"C:\" };
                foreach (string root in roots)
                {
                    string path = Path.Combine(root, version, "python.exe");
                    if (File.Exists(path)) found.Add(path);
                }
            }

            string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
            foreach (string dir in pathEnv.Split(';'))
            {
                if (string.IsNullOrWhiteSpace(dir)) continue;
                try
                {
                    string candidate = Path.Combine(dir.Trim(), "python.exe");
                    if (File.Exists(candidate)) found.Add(candidate);
                }
                catch { }
            }

            // De-duplicate, preserving order.
            System.Collections.Generic.List<string> unique = new System.Collections.Generic.List<string>();
            foreach (string item in found)
            {
                if (!unique.Contains(item)) unique.Add(item);
            }
            return unique.ToArray();
        }

        /// <summary>
        /// Import every runtime dependency in <paramref name="python"/>.
        /// Returns "" on success, or the line naming what was missing.
        /// </summary>
        private static string Preflight(string python)
        {
            StringBuilder script = new StringBuilder();
            script.Append("import importlib, sys; missing = []\n");
            script.Append("for name in " + ToPythonList(RequiredModules) + ":\n");
            script.Append("    try:\n");
            script.Append("        importlib.import_module(name)\n");
            script.Append("    except Exception:\n");
            script.Append("        missing.append(name)\n");
            script.Append("print(','.join(missing))\n");

            ProcessStartInfo info = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "-c \"" + script.ToString().Replace("\"", "\\\"") + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            try
            {
                using (Process process = Process.Start(info))
                {
                    string output = process.StandardOutput.ReadToEnd().Trim();
                    bool exited = process.WaitForExit(30000);
                    if (!exited)
                    {
                        try { process.Kill(); } catch { }
                        return "timed out while checking for packages";
                    }
                    if (process.ExitCode != 0)
                    {
                        return "could not be run (exit " + process.ExitCode + ")";
                    }
                    if (output.Length == 0)
                    {
                        return "";
                    }
                    return "missing " + output;
                }
            }
            catch (Exception ex)
            {
                return "could not be run (" + ex.Message + ")";
            }
        }

        private static string ToPythonList(string[] items)
        {
            StringBuilder builder = new StringBuilder("[");
            for (int i = 0; i < items.Length; i++)
            {
                if (i > 0) builder.Append(", ");
                builder.Append("'").Append(items[i]).Append("'");
            }
            return builder.Append("]").ToString();
        }

        /// <summary>
        /// Start <paramref name="target"/> and wait for it.
        ///
        /// ``capture`` is set for check and self-test runs: those finish on their
        /// own, and their output has to come back to us because this launcher is a
        /// GUI-subsystem process with no console -- anything the child writes is
        /// otherwise discarded, which is how a failure degenerates into a bare exit
        /// code with no explanation.  An ordinary launch captures nothing and waits
        /// without a deadline, because it is the application window.
        /// </summary>
        private static int Run(string target, string[] args, string appDir, bool capture, StringBuilder log)
        {
            ProcessStartInfo info = new ProcessStartInfo
            {
                FileName = target,
                WorkingDirectory = appDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = capture,
                RedirectStandardError = capture
            };
            info.Arguments = BuildArguments(target, args);

            log.AppendLine("  command    : " + target + " " + info.Arguments);

            using (Process process = Process.Start(info))
            {
                if (process == null) return Fail(capture, log, "The process could not be started.");

                string stdout = capture ? process.StandardOutput.ReadToEnd() : string.Empty;
                string stderr = capture ? process.StandardError.ReadToEnd() : string.Empty;

                bool exited;
                if (capture)
                {
                    exited = process.WaitForExit(900000);
                }
                else
                {
                    // An ordinary launch: wait for as long as the application runs.
                    process.WaitForExit();
                    exited = true;
                }

                if (!exited)
                {
                    try { process.Kill(); } catch { }
                    log.AppendLine("  timed out");
                }
                else
                {
                    log.AppendLine("  exit code  : " + process.ExitCode);
                }

                if (stdout.Length > 0) log.AppendLine("-- output --").AppendLine(stdout.TrimEnd());
                if (stderr.Length > 0) log.AppendLine("-- errors --").AppendLine(stderr.TrimEnd());
                WriteLog(appDir, capture, log);
                if (capture && stdout.Length > 0) Console.Out.Write(stdout);
                if (capture && stderr.Length > 0) Console.Error.Write(stderr);
                return process.HasExited ? process.ExitCode : 1;
            }
        }

        /// <summary>python.exe -> pythonw.exe, when a windowless sibling exists.</summary>
        private static string Windowless(string python)
        {
            string directory = Path.GetDirectoryName(python);
            string windowless = Path.Combine(directory ?? string.Empty, "pythonw.exe");
            return File.Exists(windowless) ? windowless : python;
        }

        /// <summary>
        /// Assemble the child's command line, quoting every element that needs it.
        ///
        /// Quoting has to be decided per argument, not from the executable name.
        /// The previous attempt here tested whether the *interpreter* path ended in
        /// ".py" and therefore never quoted anything for python.exe -- so a script
        /// under a directory with a space in its name (this project's own folder is
        /// "Palm Sentinel (V2)") was split at the space and Python reported
        /// "can't open file 'E:\Freebuff\Palm'".
        /// </summary>
        private static string BuildArguments(string target, string[] args)
        {
            System.Collections.Generic.List<string> parts = new System.Collections.Generic.List<string>();
            if (target.EndsWith(".py", StringComparison.OrdinalIgnoreCase))
            {
                parts.Add(Quote(target));
            }
            if (args != null)
            {
                foreach (string argument in args)
                {
                    if (argument != null) parts.Add(Quote(argument));
                }
            }
            return string.Join(" ", parts.ToArray());
        }

        /// <summary>
        /// Quote one argument the way the Windows C runtime will unquote it.
        /// Backslashes that precede a quote are doubled, and a run of backslashes
        /// before the closing quote is doubled too.
        /// </summary>
        private static string Quote(string value)
        {
            if (value.Length > 0 && value.IndexOf(' ') < 0 && value.IndexOf('\t') < 0
                && value.IndexOf('"') < 0)
            {
                return value;
            }

            StringBuilder quoted = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char c in value)
            {
                if (c == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (c == '"')
                {
                    quoted.Append('\\', backslashes * 2 + 1).Append('"');
                    backslashes = 0;
                    continue;
                }
                quoted.Append('\\', backslashes).Append(c);
                backslashes = 0;
            }
            quoted.Append('\\', backslashes * 2).Append('"');
            return quoted.ToString();
        }

        private static int Fail(bool quiet, StringBuilder log, string message)
        {
            log.AppendLine();
            log.AppendLine("FAILED");
            log.AppendLine(message);
            WriteLog(AppDomain.CurrentDomain.BaseDirectory, quiet, log);
            if (!quiet)
            {
                MessageBox.Show(message, AppName,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            else
            {
                Console.Error.WriteLine(message);
            }
            return 1;
        }

        /// <summary>
        /// Always record what happened.  A GUI process has no console, so without
        /// this a failed launch is completely silent -- which is precisely how the
        /// previous version behaved.
        /// </summary>
        private static void WriteLog(string directory, bool quiet, StringBuilder log)
        {
            try
            {
                File.WriteAllText(
                    Path.Combine(directory, "launcher.log"),
                    log.ToString(),
                    new UTF8Encoding(false));
            }
            catch { }
            // Nothing is echoed here.  A check run writes the child's own stdout
            // and stderr straight through from Run(); the full record -- chosen
            // interpreter, command line, exit code -- is in launcher.log, which is
            // the only place some of it can survive a GUI process with no console.
        }
    }

    internal static class ArrayExtensions
    {
        /// <summary>prepend a single element (kept here to avoid LINQ).</summary>
        public static string[] Combine(this string[] first, string[] rest)
        {
            if (rest == null || rest.Length == 0) return first;
            string[] combined = new string[first.Length + rest.Length];
            first.CopyTo(combined, 0);
            rest.CopyTo(combined, first.Length);
            return combined;
        }
    }
}
