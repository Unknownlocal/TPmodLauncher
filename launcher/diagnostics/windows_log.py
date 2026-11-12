# SPDX-License-Identifier: MPL-2.0
"""Request a native Windows editor independently of the notice process."""

import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import subprocess


class ShellExecuteInfo(c.Structure):
    """Native SHELLEXECUTEINFOW; no editor handle is requested or owned."""

    _fields_ = (("cbSize", w.DWORD), ("fMask", w.ULONG), ("hwnd", w.HWND),
                ("lpVerb", w.LPCWSTR), ("lpFile", w.LPCWSTR), ("lpParameters", w.LPCWSTR),
                ("lpDirectory", w.LPCWSTR), ("nShow", c.c_int), ("hInstApp", w.HINSTANCE),
                ("lpIDList", c.c_void_p), ("lpClass", w.LPCWSTR), ("hkeyClass", w.HKEY),
                ("dwHotKey", w.DWORD), ("hIcon", w.HANDLE), ("hProcess", w.HANDLE))


def open_log(path, *, editor_command=None, show=1):
    """Open one completed absolute path, with no shell command or editor supervision.

    The optional command is for controlled host fixtures, never pack-supplied data.
    The default is the system Notepad executable, avoiding .log association prompts.
    """
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise OSError("The completed launcher log is not available: " + str(path))
    if editor_command is None:
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.GetSystemDirectoryW.argtypes = (w.LPWSTR, w.UINT)
        kernel.GetSystemDirectoryW.restype = w.UINT
        directory = c.create_unicode_buffer(32768)
        length = kernel.GetSystemDirectoryW(directory, len(directory))
        if not length or length >= len(directory):
            raise c.WinError(c.get_last_error())
        editor_command = [str(Path(directory.value) / "notepad.exe")]
    shell = c.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = (c.POINTER(ShellExecuteInfo),)
    shell.ShellExecuteExW.restype = w.BOOL
    ole = c.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = (c.c_void_p, w.DWORD)
    ole.CoInitializeEx.restype = w.LONG
    ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = (), None
    status = ole.CoInitializeEx(None, 0x6)  # Apartment-threaded; disable legacy OLE DDE.
    if status not in (0, 1):
        raise OSError("Could not initialise Windows log opening: " + hex(status & 0xFFFFFFFF))
    try:
        request = ShellExecuteInfo()
        request.cbSize = c.sizeof(request)
        # NOASYNC completes shell dispatch before this worker exits. FLAG_NO_UI
        # keeps a failed editor launch from creating an unowned system error box.
        request.fMask = 0x100 | 0x400
        request.lpVerb, request.lpFile = "open", str(editor_command[0])
        request.lpParameters = subprocess.list2cmdline([*editor_command[1:], str(path)])
        request.nShow = show
        if not shell.ShellExecuteExW(c.byref(request)):
            raise c.WinError(c.get_last_error())
    finally:
        ole.CoUninitialize()
