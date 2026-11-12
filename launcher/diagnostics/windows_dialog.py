# SPDX-License-Identifier: MPL-2.0
"""Show an owned, modeless Windows failure dialog using only system APIs."""

import ctypes as c
from ctypes import wintypes as w
import struct


class WindowsDialog:
    """Keep the window in its presentation process, separate from game and editor."""

    def __init__(self):
        self.user = c.WinDLL("user32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        for name, args, result in (
            ("CreateDialogIndirectParamW", (w.HINSTANCE, c.c_void_p, w.HWND, self.callback_type, w.LPARAM), w.HWND),
            ("DestroyWindow", (w.HWND,), w.BOOL),
            ("ShowWindow", (w.HWND, c.c_int), w.BOOL),
            ("UpdateWindow", (w.HWND,), w.BOOL),
            ("IsWindowVisible", (w.HWND,), w.BOOL),
            ("SetForegroundWindow", (w.HWND,), w.BOOL),
            ("GetMessageW", (c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT), c.c_int),
            ("IsDialogMessageW", (w.HWND, c.POINTER(w.MSG)), w.BOOL),
            ("TranslateMessage", (c.POINTER(w.MSG),), w.BOOL),
            ("DispatchMessageW", (c.POINTER(w.MSG),), c.c_ssize_t),
            ("PostQuitMessage", (c.c_int,), None),
        ):
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.GetModuleHandleW.argtypes = (w.LPCWSTR,)
        self.kernel.GetModuleHandleW.restype = w.HMODULE
        self.hwnd = None

    @staticmethod
    def _template(notice):
        """Construct DWORD-aligned Win32 templates with Unicode text and system font."""
        def text(value):
            return (value + "\0").encode("utf-16-le")
        # WS_POPUP | WS_CAPTION | WS_SYSMENU | DS_MODALFRAME | DS_SETFONT | DS_CENTER.
        data = bytearray(struct.pack("<IIHhhhh", 0x80C808C0, 0, 3, 0, 0, 340, 186))
        data.extend(b"\0\0\0\0" + text(notice["title"]) + struct.pack("<H", 9) + text("Segoe UI"))
        path_label = "Full details: " if notice["log_written"] else "No completed log for this launch: "
        details = notice["reason"] + "\r\n\r\n" + path_label + notice["log_path"]
        controls = (
            (1001, 0x82, 0, 10, 10, 320, 26, "An error occurred " + notice["activity"] + "."),
            # Read-only multiline text keeps long paths/reasons accessible by scrolling/copying.
            (1002, 0x81, 0x00A10844, 10, 40, 320, 106, details),
            (1, 0x80, 0x00010001, 260, 158, 70, 18, "Close"),
        )
        for identifier, kind, style, x, y, width, height, label in controls:
            data.extend(b"\0" * (-len(data) % 4))
            data.extend(struct.pack("<IIhhhhH", 0x50000000 | style, 0, x, y, width, height, identifier))
            data.extend(struct.pack("<HH", 0xFFFF, kind) + text(label) + b"\0\0")
        return bytes(data)

    def show(self, notice, notify):
        """Publish readiness only after an actual visible window has been painted."""
        def dialog(hwnd, message, word, parameter):
            if message == 0x0110:  # WM_INITDIALOG: native dialog manager selects focus.
                return True
            if message == 0x0010 or message == 0x0111 and word & 0xFFFF in (1, 2):
                self.user.DestroyWindow(hwnd)  # WM_CLOSE / Close button / Escape.
                return True
            if message == 0x0002:  # WM_DESTROY.
                self.hwnd = None
                self.user.PostQuitMessage(0)
                return True
            return False
        callback = self.callback_type(dialog)
        template = c.create_string_buffer(self._template(notice))
        hwnd = self.user.CreateDialogIndirectParamW(self.kernel.GetModuleHandleW(None), template, None, callback, 0)
        if not hwnd:
            raise c.WinError(c.get_last_error())
        self.hwnd = hwnd
        try:
            self.user.ShowWindow(hwnd, 1)
            # The first ShowWindow can inherit a hidden startup setting. A fatal
            # notice must still become visible when its console started hidden.
            self.user.ShowWindow(hwnd, 9)
            if not self.user.UpdateWindow(hwnd) or not self.user.IsWindowVisible(hwnd):
                raise OSError("The Windows failure dialog could not be displayed")
            self.user.SetForegroundWindow(hwnd)  # Windows may decline a focus request.
            notify("dialog-ready", {"window": int(hwnd), "session_id": notice["session_id"]})
            message = w.MSG()
            while True:
                result = self.user.GetMessageW(c.byref(message), None, 0, 0)
                if result == -1:
                    raise c.WinError(c.get_last_error())
                if result == 0:
                    break
                if not self.user.IsDialogMessageW(hwnd, c.byref(message)):
                    self.user.TranslateMessage(c.byref(message))
                    self.user.DispatchMessageW(c.byref(message))
            notify("dialog-closed")
        finally:
            if self.hwnd is not None:
                self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
