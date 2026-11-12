# SPDX-License-Identifier: MPL-2.0
"""Own a presentation helper, never the editor or another launch's windows."""

import json
import os
import subprocess
import threading
import traceback


class PresentationChild:
    """Launch asynchronously; helper stdin is also its owner's lifetime signal.

    The trusted helper must monitor stdin before showing UI and exit on EOF.
    This closes a late dialog even if the parent dies during process creation.
    Explicit cancellation also kills only this Popen-owned helper handle.
    """

    def __init__(self, command, notice, submit, *, startup_timeout=5):
        self.command, self.notice, self.submit = command, notice, submit
        self._lock, self._closed = threading.Lock(), False
        self.process = self._thread = None
        self._timer = threading.Timer(startup_timeout, self._timeout)
        self._timer.daemon = True

    def start(self):
        self._timer.start()
        self._thread = threading.Thread(target=self._run, name="failure-presentation", daemon=True)
        self._thread.start()

    def _timeout(self):
        self.submit("error", "Error dialog startup timed out; the console remains available")
        self.close()

    def _run(self):
        process = None
        try:
            with self._lock:
                if self._closed:
                    return
            process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            with self._lock:
                self.process = process
                closed = self._closed
            if closed:
                process.kill()
                return
            process.stdin.write(json.dumps(self.notice, ensure_ascii=True).encode("utf-8") + b"\n")
            process.stdin.flush()
            while line := process.stdout.readline(1024 * 1024 + 1):
                if len(line) > 1024 * 1024:
                    raise ValueError("Presentation helper sent an oversized response")
                message = json.loads(line)
                if type(message) is not dict or message.get("event") not in ("dialog-ready", "dialog-closed", "log-requested", "error"):
                    raise ValueError("Invalid presentation helper response")
                # Log-opening results do not establish dialog readiness.
                if message["event"] in ("dialog-ready", "dialog-closed") or message.get("operation") == "dialog":
                    self._timer.cancel()
                self.submit(message["event"], message.get("detail"))
            code = process.wait()
            self.submit("helper-exited", code)
        except Exception:
            with self._lock:
                closed = self._closed
            if not closed:
                self.submit("error", "Presentation helper failed:\n" + traceback.format_exc())
        finally:
            self._timer.cancel()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                for stream in (process.stdin, process.stdout):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass  # A killed helper may close its pipe during a buffered flush.

    def close(self):
        """End only our helper; closing the editor is deliberately not part of this."""
        self._timer.cancel()
        with self._lock:
            self._closed = True
            process = self.process
        if process is not None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        if self._thread is not None and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1)
