# SPDX-License-Identifier: MPL-2.0
"""Host-independent helper lifetime; platform dialog/editor adapters plug in here."""

import json
import os
import sys
import threading
import traceback


def main(*, show_dialog=None, open_log=None):
    """Monitor owner loss before UI starts; each helper belongs to one notice.

    A dialog runs in this process, so exiting the helper destroys its windows.
    An independently opened editor is not terminated. Platform adapters must
    signal dialog-ready only after display and dialog-closed on user dismissal.
    """
    line = sys.stdin.buffer.readline(1024 * 1024 + 1)
    if not line:
        return 0
    if len(line) > 1024 * 1024:
        return 1
    notice = json.loads(line)
    lock = threading.Lock()

    def notify(event, detail=None, *, operation=None):
        with lock:
            print(json.dumps({"event": event, "detail": detail, "operation": operation}, ensure_ascii=True), flush=True)

    def owner_lifetime():
        # EOF also covers console closure, a killed parent and startup races.
        # Do not wait for a GUI thread that may be stuck before window creation.
        sys.stdin.buffer.read(1)
        os._exit(0)

    threading.Thread(target=owner_lifetime, name="notice-owner-lifetime", daemon=True).start()

    def request_log():
        if not notice["log_written"]:
            return
        if open_log is None:
            notify("error", "Automatic log opening is unavailable; use the path printed in the console", operation="log")
            return
        try:
            open_log(notice["log_path"])
            notify("log-requested")
        except Exception:
            notify("error", traceback.format_exc(), operation="log")

    threading.Thread(target=request_log, name="notice-log-opening", daemon=True).start()
    if show_dialog is None:
        notify("error", "Error dialog unavailable on this host; the console remains available", operation="dialog")
        threading.Event().wait()
    try:
        show_dialog(notice, notify)
    except Exception:
        notify("error", traceback.format_exc(), operation="dialog")
        # Keep the editor request independent even if the dialog fails early.
        threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
