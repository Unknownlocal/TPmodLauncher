# SPDX-License-Identifier: MPL-2.0
"""Make the launcher's attached console readable for a fatal notice."""

import ctypes as c
from ctypes import wintypes as w


def prepare_console():
    """Return owned console streams without replacing global or redirected streams."""
    kernel = c.WinDLL("kernel32", use_last_error=True)
    user = c.WinDLL("user32", use_last_error=True)
    kernel.GetConsoleWindow.argtypes, kernel.GetConsoleWindow.restype = (), w.HWND
    kernel.AllocConsole.argtypes, kernel.AllocConsole.restype = (), w.BOOL
    kernel.FreeConsole.argtypes, kernel.FreeConsole.restype = (), w.BOOL
    user.ShowWindow.argtypes, user.ShowWindow.restype = (w.HWND, c.c_int), w.BOOL
    hwnd = kernel.GetConsoleWindow()
    if not hwnd:
        if not kernel.AllocConsole():
            error = c.get_last_error()
            # CREATE_NO_WINDOW can leave a process attached to a console with
            # no window. Only this attachment error warrants replacing it.
            if error != 5 or not kernel.FreeConsole() or not kernel.AllocConsole():
                raise c.WinError(c.get_last_error() if error == 5 else error)
        hwnd = kernel.GetConsoleWindow()
    if hwnd:
        user.ShowWindow(hwnd, 9)  # Restore an attached hidden/minimised console.
    incoming = open("CONIN$", "r", encoding="utf-8")
    try:
        outgoing = open("CONOUT$", "w", encoding="utf-8", errors="backslashreplace")
    except BaseException:
        incoming.close()
        raise
    return incoming, outgoing
