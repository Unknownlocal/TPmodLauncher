# SPDX-License-Identifier: MPL-2.0
"""Explicit, session-bound channels across Steam's console-creating Proton helper."""

import os
from pathlib import Path
import queue
import re
import stat
import threading


class HostChannel:
    """Own a private lifetime reader and control writer, never standard streams.

    Opening a Wine FIFO can block. A daemon performs startup I/O while the caller
    waits for a bounded result before loading dependencies or starting the game.
    There is no polling or ongoing protocol work during gameplay.
    """

    def __init__(self, root, session, *, startup_timeout=40):
        if not re.fullmatch(r"linux-notice\.[A-Za-z0-9]{10}", session):
            raise ValueError("Invalid Linux host session")
        self.session = session
        self.directory = Path(root) / ".state" / session
        self.reader = self.writer = None
        result = queue.Queue(maxsize=1)
        cancelled = threading.Event()

        def connect():
            try:
                self._connect()
            except Exception as error:
                self.close()
                result.put(error)
            else:
                result.put(None)
            finally:
                if cancelled.is_set():
                    self.close()

        threading.Thread(target=connect, name="native-host-startup", daemon=True).start()
        try:
            error = result.get(timeout=startup_timeout)
        except queue.Empty:
            cancelled.set()
            raise TimeoutError("The native Linux launcher handshake did not complete") from None
        if error is not None:
            raise error

    def _connect(self):
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise OSError("The native Linux session directory is unavailable")
        flags = getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        for name, mode, attribute in (("owner", os.O_RDONLY, "reader"),
                                       ("control", os.O_WRONLY, "writer")):
            path = self.directory / name
            metadata = path.lstat()
            # Wine exposes a working FIFO as FILE_TYPE_CHAR. Native POSIX can
            # check its actual type; on Windows reject consoles below instead.
            if stat.S_ISLNK(metadata.st_mode) or (os.name != "nt" and not stat.S_ISFIFO(metadata.st_mode)):
                raise OSError("Invalid native Linux channel endpoint: " + name)
            descriptor = os.open(path, mode | flags)
            setattr(self, attribute, descriptor)
            if os.get_inheritable(descriptor):
                raise OSError("Native Linux channel handles must not be inheritable")
            if os.name == "nt":
                import ctypes as c
                from ctypes import wintypes as w
                import msvcrt

                kernel = c.WinDLL("kernel32", use_last_error=True)
                kernel.GetConsoleMode.argtypes = (w.HANDLE, c.POINTER(w.DWORD))
                kernel.GetConsoleMode.restype = w.BOOL
                value = w.DWORD()
                if kernel.GetConsoleMode(msvcrt.get_osfhandle(descriptor), c.byref(value)):
                    raise OSError("A console cannot be used as a native Linux channel")
        self._handshake()

    def _handshake(self):
        """Confirm both directional endpoints before allowing normal startup."""
        self.send("HELLO")
        expected = f"ML1 {self.session} START\n".encode("ascii")
        received = bytearray()
        while len(received) < len(expected):
            part = os.read(self.reader, 1)
            if not part:
                raise EOFError("The native Linux launcher closed during startup")
            received.extend(part)
            if part == b"\n":
                break
        if received != expected:
            raise ValueError("Invalid native Linux launcher startup challenge")
        self.send("READY")

    def send(self, kind, *fields):
        """Write one bounded ASCII frame; console output is never a delivery path."""
        frame = f"ML1 {self.session} {kind}" + "".join(" " + str(value) for value in fields) + "\n"
        data = frame.encode("ascii")
        if len(data) > 256 or "\n" in frame[:-1] or "\r" in frame:
            raise ValueError("Invalid native Linux control frame")
        if os.write(self.writer, data) != len(data):
            raise OSError("Incomplete native Linux control write")

    def close(self):
        """Release only this channel's private descriptors."""
        for attribute in ("writer", "reader"):
            descriptor = getattr(self, attribute)
            setattr(self, attribute, None)
            if descriptor is not None:
                os.close(descriptor)
