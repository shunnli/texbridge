"""Small, non-persistent command bridge. Never edits application settings."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys

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


def launch(argv: list[str]) -> None:
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


class ArgumentError(BridgeError):
    pass


class BridgeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentError(f"{message}\nSee README.md for configuration instructions.")


def parser() -> argparse.ArgumentParser:
    root = BridgeParser(prog="texbridge", add_help=False, allow_abbrev=False)
    # Accepted for compatibility with existing application settings.
    root.add_argument("--gui", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("view", "forward", "inverse"):
        command = commands.add_parser(name, add_help=False, allow_abbrev=False)
        command.add_argument("--gui", action="store_true", default=argparse.SUPPRESS)
        if name == "inverse":
            command.add_argument("--code")
        else:
            command.add_argument("--sumatra")
        command.add_argument("--pdf", required=name in {"view", "forward"})
        if name in {"forward", "inverse"}:
            command.add_argument("--source", required=True)
            command.add_argument("--line", required=True, type=positive)
    return root


def run(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise BridgeError("This version supports local VS Code and SumatraPDF on Windows only")
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
    launch(argv)
    return 0


def show_dialog(message: str, status: int) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "texbridge", 0x10 if status else 0x40)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run navigation silently, reporting invalid arguments and failures in a dialog."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return run(parser().parse_args(arguments))
    except ArgumentError as exc:
        show_dialog(f"texbridge: {exc}", 2)
        return 2
    except (BridgeError, OSError) as exc:
        show_dialog(f"texbridge: {exc}", 1)
        return 1
