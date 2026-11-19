import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from texbridge import cli


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.file("VS Code/Code.exe")
        self.sumatra = self.file("Sumatra PDF/SumatraPDF.exe")
        self.bridge = self.file("tools/texbridge.exe")
        self.source = self.file("中文 & spaces/chapter.tex")
        self.pdf = self.file("中文 & spaces/main.pdf")

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path.resolve()

    def test_forward_preserves_unicode_and_argument_boundaries(self):
        args = cli.parser().parse_args([
            "forward", "--sumatra", str(self.sumatra), "--pdf", str(self.pdf),
            "--source", str(self.source), "--line", "42",
        ])
        with patch.object(cli, "launch") as launch:
            self.assertEqual(cli.run(args), 0)
        launch.assert_called_once_with([
            str(self.sumatra), "-reuse-instance", "-forward-search",
            str(self.source), "42", str(self.pdf),
        ], dry_run=False)

    def test_inverse_uses_public_cli(self):
        args = cli.parser().parse_args([
            "inverse", "--code", str(self.code), "--source", str(self.source), "--line", "7",
        ])
        with patch.object(cli, "launch") as launch:
            cli.run(args)
        launch.assert_called_once_with([
            str(self.code), "--reuse-window", "--goto", f"{self.source}:7",
        ], dry_run=False)

    def test_inverse_opens_project_and_source_in_one_command(self):
        (self.root / ".git").mkdir()
        with patch.object(cli, "launch") as launch:
            status = cli.main(["inverse", "--code", str(self.code),
                               "--source", str(self.source), "--line", "7"])
        self.assertEqual(status, 0)
        launch.assert_called_once_with([
            str(self.code), "--new-window", str(self.root), "--goto", f"{self.source}:7",
        ], dry_run=False)

    def test_inverse_uses_explicit_pdf_directory(self):
        with patch.object(cli, "launch") as launch:
            status = cli.main(["inverse", "--code", str(self.code), "--pdf", str(self.pdf),
                               "--source", str(self.source), "--line", "7"])
        self.assertEqual(status, 0)
        launch.assert_called_once_with([
            str(self.code), "--new-window", str(self.pdf.parent), "--goto", f"{self.source}:7",
        ], dry_run=False)

    def test_view_needs_no_synctex(self):
        args = cli.parser().parse_args(["view", "--sumatra", str(self.sumatra), "--pdf", str(self.pdf)])
        with patch.object(cli, "launch") as launch:
            cli.run(args)
        launch.assert_called_once_with([str(self.sumatra), "-reuse-instance", str(self.pdf)], dry_run=False)

    def test_dry_run_does_not_spawn(self):
        output = io.StringIO()
        with patch.object(cli.subprocess, "Popen") as spawn, redirect_stdout(output):
            cli.launch([str(self.code), "--goto", f"{self.source}:1"], dry_run=True)
        spawn.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["argv"][-1], f"{self.source}:1")

    def test_spawn_cleans_only_electron_node_flag(self):
        with patch.dict(os.environ, {"ELECTRON_RUN_AS_NODE": "1", "TEXBRIDGE_TEST": "keep"}):
            with patch.object(cli.subprocess, "Popen") as spawn:
                cli.launch([str(self.code)], dry_run=False)
            options = spawn.call_args.kwargs
            self.assertFalse(options["shell"])
            self.assertNotIn("ELECTRON_RUN_AS_NODE", options["env"])
            self.assertEqual(options["env"]["TEXBRIDGE_TEST"], "keep")
            self.assertEqual(os.environ["ELECTRON_RUN_AS_NODE"], "1")

    def test_spawn_failure_is_actionable(self):
        with patch.object(cli.subprocess, "Popen", side_effect=OSError("denied")):
            with self.assertRaisesRegex(cli.BridgeError, "Failed to launch"):
                cli.launch([str(self.code)], dry_run=False)

    def test_config_quotes_inverse_placeholder_and_uses_gui_option(self):
        settings, inverse = cli.configuration(self.code, self.sumatra)
        self.assertTrue(inverse.startswith("texbridge inverse --gui "))
        self.assertIn("inverse --gui", inverse)
        for key in ("viewer", "synctex"):
            self.assertEqual(settings[f"latex-workshop.view.pdf.external.{key}.command"], "texbridge")
            self.assertIn("--gui", settings[f"latex-workshop.view.pdf.external.{key}.args"])
        self.assertIn('--source "%f" --line %l', inverse)
        self.assertNotIn("cli.js", inverse)
        self.assertNotIn("-inverse-search", json.dumps(settings))

    def test_configuration_uses_forward_slashes_in_paths(self):
        settings, inverse = cli.configuration(self.code, self.sumatra)
        for key in ("viewer", "synctex"):
            arguments = settings[f"latex-workshop.view.pdf.external.{key}.args"]
            self.assertEqual(arguments[arguments.index("--sumatra") + 1], self.sumatra.as_posix())
        self.assertIn(self.code.as_posix(), inverse)
        self.assertNotIn("\\", inverse)

    def test_gui_navigation_is_silent_on_success(self):
        with patch.object(cli, "launch") as launch, patch.object(cli, "show_dialog") as dialog:
            status = cli.main(["inverse", "--gui", "--code", str(self.code),
                               "--source", str(self.source), "--line", "1"])
        self.assertEqual(status, 0)
        launch.assert_called_once()
        dialog.assert_not_called()

    def test_gui_reports_parse_and_runtime_errors(self):
        for arguments, expected_status in (
            (["inverse", "--gui"], 2),
            (["--gui", "inverse", "--code", str(self.code),
              "--source", str(self.root / "missing.tex"), "--line", "1"], 1),
        ):
            with self.subTest(arguments=arguments), patch.object(cli, "show_dialog") as dialog:
                self.assertEqual(cli.main(arguments), expected_status)
                dialog.assert_called_once()
                self.assertEqual(dialog.call_args.args[1], expected_status)

    def test_gui_dry_run_displays_arguments_without_launching(self):
        with patch.object(cli.subprocess, "Popen") as spawn, patch.object(cli, "show_dialog") as dialog:
            status = cli.main(["view", "--gui", "--dry-run", "--sumatra", str(self.sumatra),
                               "--pdf", str(self.pdf)])
        self.assertEqual(status, 0)
        spawn.assert_not_called()
        self.assertEqual(json.loads(dialog.call_args.args[0])["argv"][-1], str(self.pdf))

    def test_config_only_prints_and_does_not_spawn_or_write(self):
        args = cli.parser().parse_args(["config", "--code", str(self.code), "--sumatra", str(self.sumatra)])
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = io.StringIO()
        with patch.object(cli, "bridge_entry", return_value=self.bridge), patch.object(cli.subprocess, "Popen") as spawn, redirect_stdout(output):
            self.assertEqual(cli.run(args), 0)
        self.assertIn("No settings have been changed", output.getvalue())
        spawn.assert_not_called()
        after = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_ambiguous_discovery_requires_selection(self):
        with patch.object(cli, "candidates", return_value=[self.code, self.sumatra]):
            with self.assertRaisesRegex(cli.BridgeError, "Multiple"):
                cli.executable("code", None)

    def test_missing_executable(self):
        with patch.object(cli, "candidates", return_value=[]):
            with self.assertRaisesRegex(cli.BridgeError, "Cannot find"):
                cli.executable("code", None)

    def test_discovery_from_code_cmd(self):
        shim = self.file("VS Code/bin/code.cmd")
        with patch.dict(os.environ, {}, clear=True), patch.object(cli.shutil, "which", side_effect=lambda name: str(shim) if name == "code.cmd" else None):
            self.assertEqual(cli.candidates("code"), [self.code])

    def test_scripts_not_accepted_as_executables(self):
        with self.assertRaisesRegex(cli.BridgeError, "must point to an .exe"):
            cli.executable("code", str(self.file("code.cmd")))

    def test_rejects_bad_paths_and_unexpanded_placeholders(self):
        for value in ('bad"name', "bad\nname", "%TEX%", "%PDF%", "%f"):
            with self.subTest(value=value), self.assertRaises(cli.BridgeError):
                cli.checked_file(value, "Source file")

    def test_rejects_nonpositive_lines_and_extra_arguments(self):
        for tail in (["--line", "0"], ["--line", "-1"], ["--line", "abc"], ["--line", "2", "--install-extension", "evil"], ["--li", "2"]):
            with self.subTest(tail=tail), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.parser().parse_args(["inverse", "--source", str(self.source), *tail])
            self.assertEqual(error.exception.code, 2)

    def test_doctor_checks_only_pdf_directory(self):
        self.file("中文 & spaces/.aux/main.synctex.gz")
        args = argparse.Namespace(code=str(self.code), sumatra=str(self.sumatra), pdf=str(self.pdf))
        with patch.object(cli, "bridge_entry", return_value=self.bridge), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.diagnose(args), 1)
            self.file("中文 & spaces/main.synctex.gz")
            self.assertEqual(cli.diagnose(args), 0)
            self.pdf.with_suffix(".synctex.gz").write_bytes(b"")
            self.assertEqual(cli.diagnose(args), 1)

    def test_missing_source_never_launches(self):
        with patch.object(cli, "launch") as launch, redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["inverse", "--code", str(self.code), "--source", str(self.root / "missing.tex"), "--line", "1"]), 1)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
