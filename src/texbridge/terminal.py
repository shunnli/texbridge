"""Restore terminal output for the single Windows GUI launcher."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from typing import TextIO


def _attach_terminal(kernel) -> None:
    if kernel.AttachConsole(0xFFFFFFFF):
        return
    # Installed Windows launchers can insert a GUI process between pythonw and
    # the shell. Follow that ancestry rather than allocating a new console.
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260),
        ]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, 0, ctypes.c_void_p(-1).value):
        return
    parents = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            parents[entry.pid] = entry.parent
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    pid = os.getpid()
    seen = {pid}
    for _ in range(8):
        pid = parents.get(pid, 0)
        if not pid or pid in seen:
            break
        seen.add(pid)
        if kernel.AttachConsole(pid):
            break


def connect_output() -> bool:
    """Use inherited output or attach to the parent; never allocate a console.

    pythonw may set sys.stdout/stderr to None even when the parent supplied
    valid pipe handles. Preserve those before AttachConsole can replace them.
    Return False when no output destination exists (e.g. an Explorer launch).
    """
    if os.name != "nt" or (sys.stdout is not None and sys.stderr is not None):
        return True

    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    kernel.AttachConsole.restype = wintypes.BOOL
    process = kernel.GetCurrentProcess()

    def stream(number: int) -> TextIO | None:
        handle = kernel.GetStdHandle(number & 0xFFFFFFFF)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            return None
        duplicate = wintypes.HANDLE()
        if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
            return None
        try:
            fd = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY | os.O_BINARY)
        except OSError:
            kernel.CloseHandle(duplicate)
            return None
        try:
            return os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace", buffering=1, newline="\n")
        except OSError:
            os.close(fd)
            return None

    streams = {"stdout": sys.stdout, "stderr": sys.stderr}
    for name, number in (("stdout", -11), ("stderr", -12)):
        if streams[name] is None:
            streams[name] = stream(number)
    if any(value is None for value in streams.values()):
        _attach_terminal(kernel)
        for name, number in (("stdout", -11), ("stderr", -12)):
            if streams[name] is None:
                streams[name] = stream(number)
    if all(value is None for value in streams.values()):
        return False
    sys.stdout = streams["stdout"] or streams["stderr"]
    sys.stderr = streams["stderr"] or streams["stdout"]
    return True
