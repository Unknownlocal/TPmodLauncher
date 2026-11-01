# SPDX-License-Identifier: MPL-2.0
"""Owned fake dialog/editor; the production lifetime guard still controls it."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

folder, output, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("owned_presentation_helper", folder / "presentation_helper.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
# A failed test must not leave an owned fixture behind indefinitely.
deadline = threading.Timer(10, lambda: os._exit(86))
deadline.daemon = True
deadline.start()
log_finished = threading.Event()


def open_log(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
        assert "END OF ORIGINAL CAUSE" in text and "Owned cleanup complete" in text
        (output / "log-requested").write_text(path, encoding="utf-8")
        if mode == "failure":
            raise OSError("Fixture editor could not open")
    finally:
        log_finished.set()


def show_dialog(notice, notify):
    assert notice["activity"] == "while saving"
    (output / "helper.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    if mode == "failure":
        raise RuntimeError("Fixture dialog could not open")
    if mode in ("late", "timeout"):
        threading.Event().wait()  # Dismissal must kill startup before any window.
    (output / "dialog-ready").write_text("ready", encoding="utf-8")
    notify("dialog-ready")
    if mode == "dialog":
        assert log_finished.wait(5)
        notify("dialog-closed")
    else:
        threading.Event().wait()


raise SystemExit(helper.main(show_dialog=show_dialog, open_log=open_log))
