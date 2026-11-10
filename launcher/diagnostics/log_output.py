# SPDX-License-Identifier: MPL-2.0
"""Fan out stable records to independent console and file queues."""

from copy import copy
import importlib.util
import json
import logging
from pathlib import Path
import sys
import threading
from time import monotonic

if __package__:
    from .console_output import ConsoleOutput
    from .log_file_handler import LogFileHandler
    from .output_writer import OutputWriter
    from .exception_snapshot import ExceptionSnapshot
else:
    def _load(name):
        spec = importlib.util.spec_from_file_location("_log_output_" + name, Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    ConsoleOutput = _load("console_output").ConsoleOutput
    LogFileHandler = _load("log_file_handler").LogFileHandler
    OutputWriter = _load("output_writer").OutputWriter
    ExceptionSnapshot = _load("exception_snapshot").ExceptionSnapshot


class LogOutput(logging.Handler):
    """A standard logger handler whose lock never covers destination I/O.

    File and console workers use plain output objects, not logging handlers:
    logging.shutdown cannot join them or acquire their blocked stream locks.
    """

    file_type = LogFileHandler

    def __init__(self, path, outcome, *, minimum=20, console=None, ownership=None, mode="w", activity=None):
        super().__init__()
        self.minimum, self.outcome = minimum, outcome
        self._state_lock = threading.Lock()
        self._activity = activity
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        self.file = None
        console = sys.stderr if console is None else console
        self.console = OutputWriter("console", lambda: ConsoleOutput(console), self._store_console_error)
        if path is not None:
            if ownership is not None:
                ownership.retain()
            try:
                self.file = OutputWriter("file", lambda: self.file_type(path, mode=mode), self._store_file_error,
                                         release=ownership.release if ownership is not None else None,
                                         activity=activity() if activity is not None else None)
            except BaseException:
                if ownership is not None:
                    ownership.release()
                self.console.close()
                raise

    def _store_file_error(self, error, activity):
        # Never print on the file worker. Even the warning about storage failure
        # must remain independent of console responsiveness.
        # Preserve causes and stack locations without reading source files.
        # OutputWriter publishes this state before allowing a waiter to give up.
        detail = "".join(ExceptionSnapshot(error).format(lookup_lines=False))
        with self._state_lock:
            self.outcome["log_written"] = False
            first = self.outcome["log_error"] is None
            if first:
                self.outcome["log_error"] = detail
                self.outcome["log_activity"] = activity
        if first:
            self.console.enqueue_log_record("Launcher log could not be completed: " + self.outcome["log_path"]
                                            + "\n" + detail + "\n")

    def _store_console_error(self, error, activity):
        detail = str(error)
        with self._state_lock:
            self.outcome.setdefault("console_error", detail)
        file = getattr(self, "file", None)
        if file is not None:
            file.enqueue_log_record("WARNING modLauncher.logging Console output failed: " + detail + "\n", activity=activity)

    def emit(self, record):
        """Freeze time/message on submission; worker threads only write text."""
        fatal = getattr(record, "launcher_fatal", False)
        if record.levelno < self.minimum and not fatal:
            return
        text = self.format(copy(record)) + "\n"
        activity = self._activity() if self._activity is not None else None
        ordinary = (record.levelno < logging.WARNING and not fatal
                    and not record.exc_info and not getattr(record, "launcher_protected", False))
        if self.file is not None:
            self.outcome["log_written"] = False
            self.file.enqueue_log_record(text, ordinary=ordinary, activity=activity)
        self.console.enqueue_log_record(text, ordinary=ordinary, activity=activity)

    def sync_file(self, timeout=1):
        activity = self._activity() if self._activity is not None else None
        complete = (self.file is not None and not self.file.failed.is_set()
                    and self.file.checkpoint(timeout, activity=activity))
        with self._state_lock:
            self.outcome["log_written"] = bool(complete and self.outcome["log_error"] is None)
        return self.outcome["log_written"]

    def presentation_error(self, detail):
        self.console.enqueue_log_record(detail + "\n")
        if self.file is not None:
            self.file.enqueue_log_record("\nPRESENTATION ERROR " + json.dumps(detail, ensure_ascii=True) + "\n")

    def finish(self, *, deadline=None):
        """Share one deadline; console completion never gates the file checkpoint."""
        deadline = monotonic() + 1 if deadline is None else deadline
        if self.file is not None:
            self.file.close()
            complete = self.file.wait(0 if self.file.failed.is_set() else max(0, deadline - monotonic()))
            with self._state_lock:
                self.outcome["log_written"] = complete and self.outcome["log_error"] is None
        # Keep the console open until any final file error has been enqueued.
        # It has been writing concurrently throughout the file wait.
        self.console.close()
        self.console.wait(max(0, deadline - monotonic()))
        self.close()

    def close(self):
        # Standard logging may call close again at interpreter exit. This only
        # seals memory queues and must never wait on either worker's I/O.
        if getattr(self, "file", None) is not None:
            self.file.close()
        if getattr(self, "console", None) is not None:
            self.console.close()
        super().close()
