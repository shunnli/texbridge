"""Small, non-persistent command bridge. Never edits application settings."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__
from .terminal import connect_output
from .workspace import find_project


class BridgeError(Exception):
    pass


def positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def checked_file(value: str, label: str) -> Path:
    # Reject unresolved placeholders and characters that could break the Windows
    # inverse-search command template. Never forward arbitrary editor switches.
    if any(token in value for token in ('"', '\n', '\r', '\x00')):
        raise BridgeError(f"{label} contains invalid characters")
    if value in {"%PDF%", "%TEX%", "%f"}:
        raise BridgeError(f"{label} placeholder was not expanded: {value}")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise BridgeError(f"{label} does not exist: {path}")
    return path


def candidates(kind: str) -> list[Path]:
    names = ("Code.exe",) if kind == "code" else ("SumatraPDF.exe",)
    found: list[Path] = []
    for name in names:
        match = shutil.which(name)
        if match:
            found.append(Path(match))
    locations = {
        "code": [
            ("LOCALAPPDATA", "Programs/Microsoft VS Code/Code.exe"),
            ("ProgramFiles", "Microsoft VS Code/Code.exe"),
            ("ProgramFiles(x86)", "Microsoft VS Code/Code.exe"),
        ],
        "sumatra": [
            ("LOCALAPPDATA", "SumatraPDF/SumatraPDF.exe"),
            ("LOCALAPPDATA", "Programs/SumatraPDF/SumatraPDF.exe"),
            ("ProgramFiles", "SumatraPDF/SumatraPDF.exe"),
            ("ProgramFiles(x86)", "SumatraPDF/SumatraPDF.exe"),
        ],
    }
    for variable, relative in locations[kind]:
        root = os.environ.get(variable)
        if root:
            found.append(Path(root) / relative)
    # code.cmd is often on PATH while its sibling Code.exe is not.
    if kind == "code":
        shim = shutil.which("code.cmd")
        if shim:
            found.append(Path(shim).parent.parent / "Code.exe")
    unique: dict[str, Path] = {}
    for path in found:
        if path.is_file():
            resolved = path.resolve()
            unique.setdefault(str(resolved).casefold(), resolved)
    return list(unique.values())


def executable(kind: str, explicit: str | None) -> Path:
    if explicit:
        path = checked_file(explicit, kind)
        if path.suffix.lower() != ".exe":
            raise BridgeError(f"--{kind} must point to an .exe, not a .cmd file or script")
        return path
    matches = candidates(kind)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise BridgeError(f"Cannot find {kind}; use --{kind} with the full path to its .exe")
    paths = "\n  ".join(str(path) for path in matches)
    raise BridgeError(f"Multiple {kind} installations found; select one with --{kind}:\n  {paths}")


def bridge_entry() -> Path:
    # Prefer this installation, not an unrelated texbridge earlier on PATH.
    for path in (Path(sys.executable).parent / "texbridge.exe", Path(sys.argv[0])):
        if path.name.lower() == "texbridge.exe" and path.is_file():
            return path.resolve()
    raise BridgeError("Installed texbridge.exe not found; run uv tool install . before texbridge config")


def configuration(code: Path, sumatra: Path) -> tuple[dict, str]:
    settings = {
        "latex-workshop.view.pdf.viewer": "external",
        "latex-workshop.view.pdf.external.viewer.command": "texbridge",
        "latex-workshop.view.pdf.external.viewer.args": [
            "view", "--gui", "--sumatra", sumatra.as_posix(), "--pdf", "%PDF%",
        ],
        "latex-workshop.view.pdf.external.synctex.command": "texbridge",
        "latex-workshop.view.pdf.external.synctex.args": [
            "forward", "--gui", "--sumatra", sumatra.as_posix(), "--source", "%TEX%",
            "--line", "%LINE%", "--pdf", "%PDF%",
        ],
    }
    # %f must always be quoted, even though the literal placeholder has no spaces.
    prefix = subprocess.list2cmdline(["texbridge", "inverse", "--gui", "--code", code.as_posix()])
    inverse = prefix + ' --source "%f" --line %l'
    return settings, inverse


def launch(argv: list[str], *, dry_run: bool) -> None:
    if dry_run:
        print(json.dumps({"argv": argv, "remove_env": ["ELECTRON_RUN_AS_NODE"]}, ensure_ascii=False, indent=2))
        return
    environment = os.environ.copy()
    # A GUI process inherited from an Electron child must not run as Node.
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    try:
        subprocess.Popen(
            argv,
            shell=False,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise BridgeError(f"Failed to launch process: {exc}") from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="VS Code / SumatraPDF SyncTeX bridge; never edits application settings", allow_abbrev=False)
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--gui", action="store_true", help="Show output and errors in a dialog instead of the terminal")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("config", "Print manual configuration instructions"),
        ("view", "Open a PDF"),
        ("forward", "Jump from source to PDF"),
        ("inverse", "Jump from PDF to source"),
        ("doctor", "Check application paths and synchronization files (read-only)"),
    ):
        command = commands.add_parser(name, help=help_text, allow_abbrev=False)
        command.add_argument("--gui", action="store_true", default=argparse.SUPPRESS,
                             help="Show output and errors in a dialog instead of the terminal")
        if name in {"config", "inverse", "doctor"}:
            command.add_argument("--code", help="Full path to Code.exe")
        if name in {"config", "view", "forward", "doctor"}:
            command.add_argument("--sumatra", help="Full path to SumatraPDF.exe")
        if name in {"view", "forward", "inverse", "doctor"}:
            command.add_argument("--pdf", required=name in {"view", "forward"},
                                 help="PDF path (optional context for inverse project discovery)")
        if name in {"forward", "inverse"}:
            command.add_argument("--source", required=True)
            command.add_argument("--line", required=True, type=positive)
        if name in {"view", "forward", "inverse"}:
            command.add_argument("--dry-run", action="store_true", help="Print arguments without launching a process")
    return root


def diagnose(args: argparse.Namespace) -> int:
    failures = 0
    for kind in ("code", "sumatra"):
        try:
            print(f"[OK] {kind}: {executable(kind, getattr(args, kind))}")
        except BridgeError as exc:
            print(f"[FAIL] {exc}")
            failures += 1
    try:
        print(f"[OK] bridge: {bridge_entry()}")
    except BridgeError as exc:
        print(f"[FAIL] {exc}")
        failures += 1
    if args.pdf:
        try:
            pdf = checked_file(args.pdf, "PDF")
            print(f"[OK] PDF: {pdf}")
            sync = [pdf.with_suffix(suffix) for suffix in (".synctex", ".synctex.gz")]
            present = [path for path in sync if path.is_file()]
            if not present:
                raise BridgeError("No matching .synctex or .synctex.gz beside the PDF; check that compilation enables -synctex=1")
            for path in present:
                if path.stat().st_size == 0:
                    raise BridgeError(f"Synchronization file is empty: {path}")
                print(f"[OK] SyncTeX: {path}")
                if path.stat().st_mtime < pdf.stat().st_mtime:
                    print("[WARN] SyncTeX is older than the PDF; timestamps suggest possible staleness but cannot prove a mismatch")
            if len(present) > 1:
                print("[WARN] Both compressed and uncompressed SyncTeX files exist; check that both belong to the current build")
        except BridgeError as exc:
            print(f"[FAIL] {exc}")
            failures += 1
    print("[INFO] Checks paths only; SyncTeX contents, application settings and actual UI navigation are not verified.")
    print("[INFO] Inverse search opens a discovered project folder, or falls back to a single file.")
    return 1 if failures else 0


def run(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise BridgeError("This version supports local VS Code and SumatraPDF on Windows only")
    if args.command == "config":
        code = executable("code", args.code)
        sumatra = executable("sumatra", args.sumatra)
        settings, inverse = configuration(code, sumatra)
        print("1. VS Code: run Preferences: Open User Settings (JSON). Merge these fields into the existing object; preserve other settings.")
        print(json.dumps(settings, ensure_ascii=False, indent=2))
        print("\n2. SumatraPDF: paste this entire line into the inverse-search command-line setting:")
        print(inverse)
        print("\nIf either application cannot find texbridge, replace it with the full path to texbridge.exe; quote that path in the Sumatra command.")
        print("\nIf the inverse-search option is hidden, manually set EnableTeXEnhancements = true in Advanced Settings.")
        print("Recommended manual settings: ReuseInstance = true and ReloadModifiedDocuments = true.")
        print("\n3. Compile with -synctex=1 and keep the matching synchronization file beside the PDF.")
        print("Run SyncTeX from cursor in VS Code; double-click document text in SumatraPDF for inverse search.")
        print("Inverse search looks for a Git root or a nearby matching PDF folder; otherwise it opens only the source file.")
        print("If installation paths change, rerun config and update settings manually. No settings have been changed.")
        return 0
    if args.command == "doctor":
        return diagnose(args)
    if args.command == "inverse":
        code = executable("code", args.code)
        source = checked_file(args.source, "Source file")
        pdf = checked_file(args.pdf, "PDF") if args.pdf else None
        project = find_project(source, pdf)
        target = ["--new-window", str(project)] if project is not None else ["--reuse-window"]
        argv = [str(code), *target, "--goto", f"{source}:{args.line}"]
    else:
        sumatra = executable("sumatra", args.sumatra)
        pdf = checked_file(args.pdf, "PDF")
        argv = [str(sumatra), "-reuse-instance"]
        if args.command == "forward":
            source = checked_file(args.source, "Source file")
            argv += ["-forward-search", str(source), str(args.line)]
        argv.append(str(pdf))
    launch(argv, dry_run=args.dry_run)
    return 0


def console_main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (BridgeError, OSError) as exc:
        print(f"texbridge: {exc}", file=sys.stderr)
        return 1


def show_dialog(message: str, status: int) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "texbridge", 0x10 if status else 0x40)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """One entry point with terminal output by default and optional dialogs."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Select output routing before parsing so argument errors are visible too.
    if "--gui" not in arguments and connect_output():
        return console_main(arguments)

    import io
    from contextlib import redirect_stderr, redirect_stdout

    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        try:
            status = console_main(arguments)
        except SystemExit as exc:
            status = int(exc.code or 0)
    if output.getvalue():
        show_dialog(output.getvalue(), status)
    return status
