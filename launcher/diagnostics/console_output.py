# SPDX-License-Identifier: MPL-2.0
"""Write through a private console handle without locking Python's stderr."""

import os


class ConsoleOutput:
    """Own a duplicate descriptor; supplied in-memory streams stay caller-owned."""

    def __init__(self, stream):
        self.stream, self.fd = stream, None
        self.encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            self.fd = os.dup(stream.fileno())
        except (AttributeError, OSError, ValueError):
            return  # Capture streams used by hosts/tests need their normal write API.
        self._console = False
        if os.name == "nt":
            import ctypes as c
            from ctypes import wintypes as w
            import msvcrt
            self._kernel = c.WinDLL("kernel32", use_last_error=True)
            self._kernel.GetConsoleMode.argtypes = (w.HANDLE, c.POINTER(w.DWORD))
            self._kernel.GetConsoleMode.restype = w.BOOL
            self._kernel.WriteConsoleW.argtypes = (w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p)
            self._kernel.WriteConsoleW.restype = w.BOOL
            self._handle = msvcrt.get_osfhandle(self.fd)
            # NUL is a TTY according to the Windows CRT too; only an actual
            # console handle accepts WriteConsoleW.
            mode = w.DWORD()
            self._console = bool(self._kernel.GetConsoleMode(self._handle, c.byref(mode)))

    def write(self, text):
        if self.fd is None:
            self.stream.write(text)
            self.stream.flush()
        elif self._console:
            import ctypes as c
            from ctypes import wintypes as w
            # A private raw write avoids stderr's buffered lock at interpreter
            # exit. Count UTF-16 units, including both halves of non-BMP text.
            buffer = c.create_unicode_buffer(text)
            offset, count = 0, w.DWORD()
            while offset < len(buffer) - 1:
                if not self._kernel.WriteConsoleW(self._handle, c.byref(buffer, offset * c.sizeof(c.c_wchar)),
                                                  min(16384, len(buffer) - 1 - offset), c.byref(count), None):
                    raise c.WinError(c.get_last_error())
                if not count.value:
                    raise OSError("Console write made no progress")
                offset += count.value
        else:
            data = text.encode(self.encoding, errors="backslashreplace")
            while data:
                count = os.write(self.fd, data)
                if not count:
                    raise OSError("Console write made no progress")
                data = data[count:]

    def sync(self):
        pass  # Raw descriptor writes have no Python buffer to flush.

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
