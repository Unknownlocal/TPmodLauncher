# SPDX-License-Identifier: MPL-2.0
"""Read and dismiss this fixture's private Windows console, never another app's."""

import ctypes as c
from ctypes import wintypes as w
import importlib.util
import json
import msvcrt
import os
from pathlib import Path
import struct
import sys
import threading
import time

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("_owned_entry", root / "launcher/entry.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
kernel = c.WinDLL("kernel32", use_last_error=True)
kernel.GetConsoleScreenBufferInfo.argtypes = (w.HANDLE, c.c_void_p)
kernel.GetConsoleScreenBufferInfo.restype = w.BOOL
kernel.ReadConsoleOutputCharacterW.argtypes = (w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD, c.POINTER(w.DWORD))
kernel.ReadConsoleOutputCharacterW.restype = w.BOOL
kernel.WriteConsoleInputW.argtypes = (w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD))
kernel.WriteConsoleInputW.restype = w.BOOL


def console_key():
    outgoing, incoming = os.open("CONOUT$", os.O_RDONLY), os.open("CONIN$", os.O_RDWR)
    try:
        deadline = time.monotonic() + 5
        while True:
            info, count = c.create_string_buffer(22), w.DWORD()
            assert kernel.GetConsoleScreenBufferInfo(msvcrt.get_osfhandle(outgoing), info)
            width, _, _, row, *_ = struct.unpack("<hhhhHhhhhhh", info.raw)
            first = max(0, row - 70)
            buffer = c.create_unicode_buffer(width * (row - first + 1) + 1)
            assert kernel.ReadConsoleOutputCharacterW(msvcrt.get_osfhandle(outgoing), buffer,
                                                     len(buffer) - 1, first << 16, c.byref(count))
            text = buffer.value
            if "Press any key" in text:
                break
            assert time.monotonic() < deadline, "No startup notice reached the native console"
            time.sleep(.01)  # Only this owned test fixture observes its screen.
        (root / "console.json").write_text(json.dumps({"text": text}), encoding="utf-8")
        assert sys.stdin.readline() == "key\n"
        record = c.create_string_buffer(struct.pack("<H2xIHHHHI", 1, 1, 1, 88, 0, ord("x"), 0))
        assert kernel.WriteConsoleInputW(msvcrt.get_osfhandle(incoming), record, 1, c.byref(count))
    finally:
        os.close(outgoing)
        os.close(incoming)


threading.Thread(target=console_key, daemon=True).start()
raise SystemExit(entry.run(["--", "inert-game-command"]))
