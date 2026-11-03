# SPDX-License-Identifier: MPL-2.0
"""Use actual Windows presentation with an owned editor application."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import threading

folder, output, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]


def load(name):
    spec = importlib.util.spec_from_file_location("owned_" + name, folder / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marker(name, value):
    temporary = output / (name + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(output / name)


deadline = threading.Timer(12, lambda: os._exit(86))
deadline.daemon = True
deadline.start()
helper = load("presentation_helper")
editor = load("windows_log")


def open_log(path):
    command = [sys._base_executable, "-I", "-S", "-B", str(Path(__file__).with_name("windows_editor_probe.py")),
               str(folder), str(output)]
    if mode == "windows-editor-failure":
        command[0] = str(output / "editor-does-not-exist.exe")
    try:
        editor.open_log(path, editor_command=command, show=0)
    except Exception:
        marker("editor-failed.json", {"failed": True})
        raise


def invalid_dialog(notice, notify):
    marker("helper.json", {"pid": os.getpid(), "window": None})
    dialog = load("windows_dialog").WindowsDialog()
    # A complete, bounded template with an unregistered native class produces
    # a real CreateDialog failure, without passing malformed memory to Win32.
    def template(value):
        text = lambda s: (s + "\0").encode("utf-16-le")
        return struct.pack("<IIHhhhh", 0x80C80080, 0, 0, 0, 0, 200, 100) + b"\0\0" + text("MissingOwnedDialogClass") + text("Owned failed dialog")
    dialog._template = template
    try:
        dialog.show(notice, notify)
    except Exception:
        marker("dialog-failed.json", {"failed": True})
        raise


raise SystemExit(helper.main(show_dialog=invalid_dialog if mode == "windows-dialog-failure" else None,
                             open_log=open_log))
