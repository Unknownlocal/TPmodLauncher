# SPDX-License-Identifier: MPL-2.0
"""Wait for one console key, with cancellation and no periodic input checks."""

import os
import select
import socket
import struct
import threading


class ConsoleInput:
    """Own only the cancellation handles; the caller still owns its input stream."""

    def __init__(self, stream, submit):
        self.stream, self.submit = stream, submit
        self._thread = None
        self._cancel = self._reader = self._writer = None
        self._handler = None
        self._handler_installed = False
        self._previous_mode = None

    def start(self):
        try:
            if self.stream is None or not self.stream.isatty():
                raise OSError("No interactive console input is available")
            self.fd = self.stream.fileno()
            if os.name == "nt":
                import ctypes as c
                from ctypes import wintypes as w
                import msvcrt

                self._kernel = c.WinDLL("kernel32", use_last_error=True)
                for name, args, result in (
                    ("CreateEventW", (c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR), w.HANDLE),
                    ("SetEvent", (w.HANDLE,), w.BOOL),
                    ("CloseHandle", (w.HANDLE,), w.BOOL),
                    ("GetConsoleMode", (w.HANDLE, c.POINTER(w.DWORD)), w.BOOL),
                    ("SetConsoleMode", (w.HANDLE, w.DWORD), w.BOOL),
                    ("WaitForMultipleObjects", (w.DWORD, c.POINTER(w.HANDLE), w.BOOL, w.DWORD), w.DWORD),
                    ("ReadConsoleInputW", (w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)), w.BOOL),
                ):
                    function = getattr(self._kernel, name)
                    function.argtypes, function.restype = args, result
                self.handle = msvcrt.get_osfhandle(self.fd)
                mode = w.DWORD()
                if not self._kernel.GetConsoleMode(self.handle, c.byref(mode)):
                    raise OSError("No interactive console input is available")
                # Physical Ctrl+C must reach ReadConsoleInput even when the
                # launch environment inherited an ignored Ctrl+C signal flag.
                if not self._kernel.SetConsoleMode(self.handle, mode.value & ~1):
                    raise c.WinError(c.get_last_error())
                self._previous_mode = mode.value
                self._cancel = self._kernel.CreateEventW(None, True, False, None)
                if not self._cancel:
                    raise c.WinError(c.get_last_error())
                handler_type = c.WINFUNCTYPE(w.BOOL, w.DWORD)
                self._kernel.SetConsoleCtrlHandler.argtypes = (handler_type, w.BOOL)
                self._kernel.SetConsoleCtrlHandler.restype = w.BOOL
                def control(event):
                    if event in (0, 1):  # CTRL_C / CTRL_BREAK must also wake Queue.get.
                        self.submit("key", None)
                        return True
                    return False  # Window close still ends the console owner normally.
                self._handler = handler_type(control)
                if not self._kernel.SetConsoleCtrlHandler(self._handler, True):
                    raise c.WinError(c.get_last_error())
                self._handler_installed = True
            else:
                self._reader, self._writer = socket.socketpair()
            self._thread = threading.Thread(target=self._run, name="failure-console-input", daemon=True)
            self._thread.start()
        except Exception as error:
            self.submit("input-unavailable", str(error))

    def _run(self):
        try:
            if os.name == "nt":
                self._windows_key()
            else:
                self._terminal_key()
        except Exception as error:
            self.submit("input-unavailable", str(error))

    def _windows_key(self):
        import ctypes as c
        from ctypes import wintypes as w

        handles = (w.HANDLE * 2)(self._cancel, self.handle)
        # INPUT_RECORD has a WORD event type, padding, and a 16-byte union.
        # Only KEY_EVENT and its bKeyDown member are needed for any-key input.
        record, count = c.create_string_buffer(20), w.DWORD()
        while True:
            result = self._kernel.WaitForMultipleObjects(2, handles, False, 0xFFFFFFFF)
            if result == 0:
                return
            if result != 1 or not self._kernel.ReadConsoleInputW(self.handle, record, 1, c.byref(count)):
                raise c.WinError(c.get_last_error())
            if count.value and struct.unpack_from("<H2xI", record.raw) == (1, 1):
                self.submit("key", None)
                return

    def _terminal_key(self):
        import termios

        previous = termios.tcgetattr(self.fd)
        mode = termios.tcgetattr(self.fd)
        mode[3] &= ~(termios.ICANON | termios.ECHO)
        mode[6][termios.VMIN], mode[6][termios.VTIME] = 1, 0
        try:
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
            readable, _, _ = select.select([self._reader, self.fd], [], [])
            if self._reader in readable:
                return
            if os.read(self.fd, 32):
                self.submit("key", None)
            else:
                self.submit("input-unavailable", "Console input reached EOF")
        finally:
            termios.tcsetattr(self.fd, termios.TCSANOW, previous)

    def close(self):
        """Cancel the wait and restore terminal mode without injecting keystrokes."""
        if self._thread is not None and self._thread.is_alive():
            if os.name == "nt":
                self._kernel.SetEvent(self._cancel)
            else:
                self._writer.send(b"x")
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                raise RuntimeError("Console input did not stop")
            self._thread = None
        if self._cancel is not None:
            self._kernel.CloseHandle(self._cancel)
            self._cancel = None
        if self._handler_installed:
            self._kernel.SetConsoleCtrlHandler(self._handler, False)
            self._handler_installed = False
        if self._previous_mode is not None:
            self._kernel.SetConsoleMode(self.handle, self._previous_mode)
            self._previous_mode = None
        for connection in (self._reader, self._writer):
            if connection is not None:
                connection.close()
        self._reader = self._writer = None
