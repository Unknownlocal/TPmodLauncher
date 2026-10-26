# SPDX-License-Identifier: MPL-2.0
"""Owned console controller and GUI helper; never starts or searches for the game."""

import ctypes as c
from ctypes import wintypes as w
import importlib.util
import json
import os
from pathlib import Path
import sys


def publish_ready(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)


def window(output):
    """Run a hidden native window that records normal WM_CLOSE and Python exit."""
    user, kernel = c.WinDLL("user32"), c.WinDLL("kernel32")
    callback = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WindowClass(c.Structure):
        _fields_ = [("style", w.UINT), ("proc", callback), ("class_extra", c.c_int),
                    ("window_extra", c.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                    ("cursor", w.HANDLE), ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

    for function, arguments, result in (
        (kernel.GetModuleHandleW, (w.LPCWSTR,), w.HMODULE),
        (user.RegisterClassW, (c.POINTER(WindowClass),), w.WORD),
        (user.CreateWindowExW, (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int,
                               c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID), w.HWND),
        (user.DefWindowProcW, (w.HWND, w.UINT, w.WPARAM, w.LPARAM), c.c_ssize_t),
        (user.DestroyWindow, (w.HWND,), w.BOOL),
        (user.GetMessageW, (c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT), w.BOOL),
        (user.TranslateMessage, (c.POINTER(w.MSG),), w.BOOL),
        (user.DispatchMessageW, (c.POINTER(w.MSG),), c.c_ssize_t),
        (user.PostQuitMessage, (c.c_int,), None),
    ):
        function.argtypes, function.restype = arguments, result

    @callback
    def receive(hwnd, message, wp, lp):
        if message == 0x0010:
            output.with_suffix(".closed").write_text("normal window close", encoding="utf-8")
            user.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, message, wp, lp)

    definition = WindowClass()
    definition.proc, definition.instance, definition.name = receive, kernel.GetModuleHandleW(None), "OwnedCloseProbe"
    assert user.RegisterClassW(c.byref(definition))
    hwnd = user.CreateWindowExW(0, definition.name, "Owned close probe", 0x00CF0000,
                                0, 0, 100, 100, None, None, definition.instance, None)
    assert hwnd
    publish_ready(output.with_suffix(".json"), {"pid": os.getpid(), "hwnd": hwnd})
    message = w.MSG()
    while user.GetMessageW(c.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(c.byref(message))
        user.DispatchMessageW(c.byref(message))
    output.with_suffix(".exited").write_text("clean Python exit", encoding="utf-8")


if sys.argv[1] == "window":
    window(Path(sys.argv[2]))
else:
    root = Path(__file__).resolve().parent.parent

    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, root / "launcher" / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    frida = load("test_binding", "native_test_binding.py").load(root)
    load("dependencies", "bundled_dependencies.py").load_source_packages(root)
    from launcher.process_lifecycle import run_launcher

    kernel = c.WinDLL("kernel32")
    kernel.GetConsoleWindow.restype = w.HWND
    publish_ready(root / "console.json", {"hwnd": kernel.GetConsoleWindow()})
    raise SystemExit(run_launcher(frida, sys.argv[2:], root / ".state/controller.lock",
                                 root / "logs/console-test.log", restart_grace=.2))
