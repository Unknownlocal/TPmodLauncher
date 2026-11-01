# SPDX-License-Identifier: MPL-2.0
"""Wait for one verified process to exit without scanning running processes."""

import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import select
import socket
import threading


class ProcessExit:
    """Pin process identity and provide a cancellable, one-shot exit notification."""

    def __init__(self, pid, executable, completed):
        self.completed = completed
        self.pid = pid
        self.executable = os.path.normcase(str(Path(executable).resolve()))
        self._thread = None
        self._process = self._cancel = None
        self._reader = self._writer = None
        try:
            if os.name == "nt":
                self._kernel = c.WinDLL("kernel32", use_last_error=True)
                for name, args, result in (
                    ("OpenProcess", (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
                    ("QueryFullProcessImageNameW", (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)), w.BOOL),
                    ("CreateEventW", (c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR), w.HANDLE),
                    ("SetEvent", (w.HANDLE,), w.BOOL),
                    ("WaitForSingleObject", (w.HANDLE, w.DWORD), w.DWORD),
                    ("WaitForMultipleObjects", (w.DWORD, c.POINTER(w.HANDLE), w.BOOL, w.DWORD), w.DWORD),
                    ("CloseHandle", (w.HANDLE,), w.BOOL),
                ):
                    function = getattr(self._kernel, name)
                    function.argtypes, function.restype = args, result
                self._process = self._kernel.OpenProcess(0x1000 | 0x100000, False, pid)
                if not self._process:
                    raise c.WinError(c.get_last_error())
                self._cancel = self._kernel.CreateEventW(None, True, False, None)
                if not self._cancel:
                    raise c.WinError(c.get_last_error())
            elif hasattr(os, "pidfd_open"):
                self._process = os.pidfd_open(pid)
                self._reader, self._writer = socket.socketpair()
            else:
                raise RuntimeError("This host has no supported process exit wait")
            self._verify_image()
        except BaseException:
            self._release()
            raise

    def start(self):
        # Recheck the pinned process after Frida attach, before installing any
        # game hooks. An exited handle must not certify a reused PID.
        self._ensure_alive()
        self._verify_image()
        self._ensure_alive()
        self._thread = threading.Thread(target=self._run, name="owned-process-exit", daemon=True)
        self._thread.start()

    def _ensure_alive(self):
        if self.has_exited():
            raise ProcessLookupError("Owned process exited during attachment")

    def has_exited(self):
        """Check this pinned owner at a work/error boundary, never by its PID."""
        if os.name == "nt":
            state = self._kernel.WaitForSingleObject(self._process, 0)
            if state == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
            return state == 0
        else:
            return bool(select.select([self._process], [], [], 0)[0])

    def _verify_image(self):
        if os.name == "nt":
            path, size = c.create_unicode_buffer(32768), w.DWORD(32768)
            if not self._kernel.QueryFullProcessImageNameW(self._process, 0, path, c.byref(size)):
                raise c.WinError(c.get_last_error())
            actual = path.value
        else:
            actual = str(Path(f"/proc/{self.pid}/exe").resolve(strict=True))
        if os.path.normcase(str(Path(actual).resolve())) != self.executable:
            raise RuntimeError("Exit wait process does not match the owned executable")

    def _run(self):
        try:
            if os.name == "nt":
                handles = (w.HANDLE * 2)(self._cancel, self._process)
                result = self._kernel.WaitForMultipleObjects(2, handles, False, 0xFFFFFFFF)
                if result == 0:
                    return
                if result != 1:
                    raise c.WinError(c.get_last_error())
            else:
                readable, _, _ = select.select([self._process, self._reader], [], [])
                if self._reader in readable:
                    return
            self.completed(None)
        except Exception as error:
            self.completed(error)

    def close(self):
        """Cancel the wait before releasing handles; never terminate the process."""
        if self._thread is not None:
            if os.name == "nt":
                if not self._kernel.SetEvent(self._cancel):
                    raise c.WinError(c.get_last_error())
            else:
                self._writer.send(b"x")
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                raise RuntimeError("Process exit wait did not cancel")
            self._thread = None
        self._release()

    def _release(self):
        if os.name == "nt":
            for handle in (self._process, self._cancel):
                if handle is not None:
                    self._kernel.CloseHandle(handle)
        elif self._process is not None:
            os.close(self._process)
        for connection in (self._reader, self._writer):
            if connection is not None:
                connection.close()
        self._process = self._cancel = self._reader = self._writer = None
