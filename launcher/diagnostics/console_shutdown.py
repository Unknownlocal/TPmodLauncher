# SPDX-License-Identifier: MPL-2.0
"""Close owned game processes when their Windows launcher console closes."""

from .core import bind_reporter

import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import threading
import time


class ConsoleShutdown:
    """Keep the console alive briefly while the controller and game clean up."""

    def __init__(self, executable, submit_log):
        self.executable = os.path.normcase(str(Path(executable).resolve()))
        self.submit_log = bind_reporter(submit_log, component="shutdown")
        self.requested, self.finished = threading.Event(), threading.Event()
        self._lock, self._processes, self._posted = threading.RLock(), {}, set()
        self._handler, self._deadline = None, None
        self._forced = False
        self.result = "not_requested"
        self.wake = None

    def __enter__(self):
        if os.name != "nt":
            return self
        self._kernel = c.WinDLL("kernel32", use_last_error=True)
        self._user = c.WinDLL("user32", use_last_error=True)
        self._enumerator = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        handler_type = c.WINFUNCTYPE(w.BOOL, w.DWORD)
        for function, arguments, result in (
            (self._kernel.SetConsoleCtrlHandler, (handler_type, w.BOOL), w.BOOL),
            (self._kernel.OpenProcess, (w.DWORD, w.BOOL, w.DWORD), w.HANDLE),
            (self._kernel.QueryFullProcessImageNameW, (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)), w.BOOL),
            (self._kernel.WaitForSingleObject, (w.HANDLE, w.DWORD), w.DWORD),
            (self._kernel.CloseHandle, (w.HANDLE,), w.BOOL),
            (self._kernel.TerminateProcess, (w.HANDLE, w.UINT), w.BOOL),
            (self._user.EnumWindows, (self._enumerator, w.LPARAM), w.BOOL),
            (self._user.GetWindowThreadProcessId, (w.HWND, c.POINTER(w.DWORD)), w.DWORD),
            (self._user.PostMessageW, (w.HWND, w.UINT, w.WPARAM, w.LPARAM), w.BOOL),
        ):
            function.argtypes, function.restype = arguments, result
        self._handler = handler_type(self._signal)
        if not self._kernel.SetConsoleCtrlHandler(self._handler, True):
            raise c.WinError(c.get_last_error())
        return self

    def track(self, pid):
        """Pin an already owned game process, rechecking its executable path."""
        if os.name != "nt":
            return
        # Retain termination rights now: reopening a PID during shutdown could
        # target a replacement process rather than the game we verified.
        handle = self._kernel.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
        if not handle:
            raise c.WinError(c.get_last_error())
        try:
            path, size = c.create_unicode_buffer(32768), w.DWORD(32768)
            if not self._kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(size)):
                raise c.WinError(c.get_last_error())
            if os.path.normcase(str(Path(path.value).resolve())) != self.executable:
                raise RuntimeError("Console shutdown process does not match the owned game")
            with self._lock:
                previous = self._processes.get(pid)
                self._processes[pid] = handle
                if previous:
                    self._kernel.CloseHandle(previous)
                handle = None
                if self.requested.is_set():
                    self._post_close()
        finally:
            if handle:
                self._kernel.CloseHandle(handle)

    def _alive(self):
        # WAIT_TIMEOUT means the process handle has not signalled its exit.
        return any(self._kernel.WaitForSingleObject(handle, 0) == 258
                   for handle in self._processes.values())

    def _post_close(self):
        """Post the same message as the window's Close button, without blocking it."""
        @self._enumerator
        def visit(window, _):
            pid = w.DWORD()
            self._user.GetWindowThreadProcessId(window, c.byref(pid))
            handle = self._processes.get(pid.value)
            if handle and self._kernel.WaitForSingleObject(handle, 0) == 258 and window not in self._posted:
                if self._user.PostMessageW(window, 0x0010, 0, 0):  # WM_CLOSE
                    self._posted.add(window)
            return True

        self._user.EnumWindows(visit, 0)

    def request(self):
        """Request exit once; ordinary mod and Frida cleanup stays on the controller."""
        with self._lock:
            if not self.requested.is_set():
                # Windows normally allows five seconds for CTRL_CLOSE_EVENT.
                # Leave time for the callback to return after bounded cleanup.
                self._deadline = time.monotonic() + 4.0
                self.requested.set()
                wake = self.wake
                if wake is not None:
                    wake()
            if os.name == "nt":
                self._post_close()

    def _signal(self, event):
        if event not in (0, 1, 2):  # Ctrl+C, Ctrl+Break and console Close.
            return False
        self.request()
        # Windows runs this on another thread. Returning immediately would let
        # it terminate Python before the controller releases mods and hooks.
        if not self.finished.wait(4.0):
            # The controller may itself be stuck in native cleanup. This OS
            # callback must enforce the fallback before Windows ends Python.
            self._force_close()
            self.finished.wait(.4)
        return True

    def _force_close(self):
        """Terminate surviving owned handles after the normal-close allowance."""
        with self._lock:
            for handle in self._processes.values():
                if self._kernel.WaitForSingleObject(handle, 0) == 258:
                    if self._kernel.TerminateProcess(handle, 1):
                        self._forced = True

    def __exit__(self, *_):
        try:
            if self.requested.is_set() and os.name == "nt":
                # A just-started process may create its window after the close
                # event. Retry window discovery, posting only once per window.
                while time.monotonic() < self._deadline:
                    with self._lock:
                        self._post_close()
                        if not self._alive():
                            break
                    time.sleep(.05)
                self._force_close()
                # TerminateProcess is asynchronous. Bound confirmation across
                # all handles, rather than granting every process a new wait.
                deadline = time.monotonic() + .25
                with self._lock:
                    for handle in self._processes.values():
                        self._kernel.WaitForSingleObject(handle, max(0, int((deadline - time.monotonic()) * 1000)))
                    pending = self._alive()
                self.result = "pending" if pending else "forced" if self._forced else "complete"
                self.submit_log("GAME CLOSE " + ("pending; process exit not confirmed" if pending else self.result),
                            level="ERROR" if pending else "WARNING" if self._forced else "INFO")
        finally:
            if self._handler is not None:
                self._kernel.SetConsoleCtrlHandler(self._handler, False)
                with self._lock:
                    for handle in self._processes.values():
                        self._kernel.CloseHandle(handle)
                    self._processes.clear()
            self.finished.set()
