import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from texbridge import cli
from texbridge.terminal import connect_output


class TerminalTests(unittest.TestCase):
    def test_existing_output_is_preserved(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
            self.assertTrue(connect_output())
            self.assertIs(sys.stdout, output)
            self.assertIs(sys.stderr, errors)

    def test_gui_does_not_attach_to_terminal(self):
        with patch.object(cli, "connect_output") as connect, patch.object(cli, "show_dialog") as dialog:
            self.assertEqual(cli.main(["--gui", "--version"]), 0)
        connect.assert_not_called()
        dialog.assert_called_once()

    def test_no_output_destination_falls_back_to_dialog(self):
        with patch.object(cli, "connect_output", return_value=False), patch.object(cli, "show_dialog") as dialog:
            self.assertEqual(cli.main(["--version"]), 0)
        dialog.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows pythonw output handling")
    def test_pythonw_preserves_redirected_unicode_output_and_exit_code(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("pythonw.exe unavailable")
        script = (
            "import ctypes, sys; "
            "from texbridge.terminal import connect_output; "
            "assert not ctypes.windll.kernel32.GetConsoleWindow(); "
            "assert connect_output(); "
            "print('Unicode: \\u4e2d\\u6587'); "
            "print('error output', file=sys.stderr); "
            "sys.exit(7)"
        )
        result = subprocess.run([str(pythonw), "-c", script], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("Unicode: 中文", result.stdout.decode("utf-8"))
        self.assertIn("error output", result.stderr.decode("utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows console attachment")
    def test_attach_through_gui_launcher_to_hidden_parent_console(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("pythonw.exe unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "attached.txt"
            errors = root / "errors.txt"
            error_hook = (
                "import traceback\n"
                f"sys.excepthook = lambda *exc: open({str(errors)!r}, 'w', encoding='utf-8').write(''.join(traceback.format_exception(*exc)))\n"
            )
            child = root / "child.py"
            child.write_text(
                "import ctypes, sys\n"
                + error_hook +
                "from pathlib import Path\n"
                "from texbridge.terminal import connect_output\n"
                "kernel = ctypes.windll.kernel32\n"
                "kernel.FreeConsole()\n"
                "assert not kernel.GetConsoleWindow()\n"
                "kernel.SetStdHandle(-11, None)\n"
                "kernel.SetStdHandle(-12, None)\n"
                "sys.stdout = sys.stderr = None\n"
                "assert connect_output()\n"
                "assert kernel.GetConsoleWindow()\n"
                "print('attached output')\n"
                f"Path({str(marker)!r}).write_text('ok')\n",
                encoding="utf-8",
            )
            middle = root / "middle.py"
            middle.write_text(
                "import ctypes, subprocess, sys\n"
                + error_hook +
                "ctypes.windll.kernel32.FreeConsole()\n"
                "assert not ctypes.windll.kernel32.GetConsoleWindow()\n"
                f"sys.exit(subprocess.run([{str(pythonw)!r}, {str(child)!r}], timeout=10).returncode)\n",
                encoding="utf-8",
            )
            parent_script = (
                "import subprocess, sys; "
                f"sys.exit(subprocess.run([{str(pythonw)!r}, {str(middle)!r}], timeout=15).returncode)"
            )
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                [sys.executable, "-c", parent_script],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                startupinfo=startup, timeout=20, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, errors.read_text(encoding="utf-8") if errors.exists() else result.stderr)
            self.assertEqual(marker.read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
