# SPDX-License-Identifier: MPL-2.0
"""An independent visible editor fixture, launched through ShellExecuteExW."""

import ctypes as c
from ctypes import wintypes as w
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

folder, output, path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
def unhandled(kind, error, trace):
    (output / "editor-error.txt").write_text("".join(traceback.format_exception(kind, error, trace)), encoding="utf-8")
sys.excepthook = unhandled
spec = importlib.util.spec_from_file_location("owned_editor_dialog", folder / "windows_dialog.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
deadline = threading.Timer(12, lambda: os._exit(86))
deadline.daemon = True
deadline.start()
text = path.read_text(encoding="utf-8")


def ready(event, detail=None):
    if event != "dialog-ready":
        return
    data = {"pid": os.getpid(), "window": detail["window"], "path": str(path), "text": text, "argv": sys.argv[3:]}
    temporary = output / "editor.json.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(output / "editor.json")
    def finish():
        while not (output / "close-editor").exists():
            time.sleep(.01)  # Fixture command gate, never installed in the game.
        user = c.WinDLL("user32")
        user.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        user.PostMessageW(detail["window"], 0x10, 0, 0)
    threading.Thread(target=finish, daemon=True).start()


notice = {"session_id": "owned-log-editor", "title": "Owned launcher.log editor fixture",
          "activity": "while viewing a completed log", "reason": text, "log_path": str(path), "log_written": True}
module.WindowsDialog().show(notice, ready)
