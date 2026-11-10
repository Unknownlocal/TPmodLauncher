# SPDX-License-Identifier: MPL-2.0
"""Observe native supervisor loss through one private, blocking input handle."""

from contextlib import contextmanager
import os
import sys
import threading


class HostLifetime:
    """Wake the active shutdown/notice owner on EOF, without polling the host.

    The supervisor retains the sole write end. The Proton controller explicitly
    opens its read end independently of console standard handles. Callbacks
    must only request cancellation; the controller retains cleanup ownership.
    """

    def __init__(self, *, descriptor=None, detach_stdin=False):
        """Copy a private endpoint and optionally give the game ordinary NUL input.

        Keep the native lifetime pipe separate from game input and from the
        failure console's keyboard handle. Synchronous Windows pipe reads can
        block Frida's stdio duplication even through a duplicate handle.
        The stdin default remains available for direct pipe-based callers.
        """
        self.lost = threading.Event()
        self._lock, self._callback = threading.Lock(), None
        self._fd = sys.stdin.fileno() if descriptor is None else descriptor
        if os.name == "nt":
            import ctypes as c
            from ctypes import wintypes as w
            import msvcrt

            self._kernel = c.WinDLL("kernel32", use_last_error=True)
            self._kernel.GetCurrentProcess.restype = w.HANDLE
            self._kernel.DuplicateHandle.argtypes = (w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD)
            self._kernel.DuplicateHandle.restype = w.BOOL
            self._kernel.ReadFile.argtypes = (w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p)
            self._kernel.ReadFile.restype = w.BOOL
            self._kernel.CloseHandle.argtypes = (w.HANDLE,)
            process, self._handle = self._kernel.GetCurrentProcess(), w.HANDLE()
            if not self._kernel.DuplicateHandle(process, msvcrt.get_osfhandle(self._fd), process,
                                                c.byref(self._handle), 0, False, 2):
                raise c.WinError(c.get_last_error())
        else:
            self._fd = os.dup(self._fd)
        if detach_stdin:
            with open(os.devnull, "rb") as incoming:
                os.dup2(incoming.fileno(), 0)
            if os.name == "nt":
                self._kernel.SetStdHandle.argtypes = (w.DWORD, w.HANDLE)
                self._kernel.SetStdHandle.restype = w.BOOL
                if not self._kernel.SetStdHandle(-10, msvcrt.get_osfhandle(0)):
                    self._kernel.CloseHandle(self._handle)
                    raise c.WinError(c.get_last_error())
        threading.Thread(target=self._wait, name="native-host-lifetime", daemon=True).start()

    def _wait(self):
        try:
            if os.name == "nt":
                import ctypes as c
                from ctypes import wintypes as w
                # A blocking CRT read holds stdin's descriptor lock and can
                # deadlock Frida's spawn-time duplication of inherited stdio.
                # Read a private, non-inheritable Win32 handle instead.
                data, count = c.create_string_buffer(1), w.DWORD()
                try:
                    self._kernel.ReadFile(self._handle, data, 1, c.byref(count), None)
                finally:
                    self._kernel.CloseHandle(self._handle)
            else:
                try:
                    os.read(self._fd, 1)
                finally:
                    os.close(self._fd)
        except OSError:
            pass  # A broken inherited pipe is also loss of the supervisor.
        finally:
            with self._lock:
                self.lost.set()
                if self._callback is not None:
                    self._callback()

    def check(self):
        if self.lost.is_set():
            raise KeyboardInterrupt("Native launcher supervisor closed")

    @contextmanager
    def watch(self, callback):
        """Bind only the current operation, including loss before registration."""
        with self._lock:
            self._callback = callback
            if self.lost.is_set():
                callback()
        try:
            yield
        finally:
            with self._lock:
                self._callback = None
