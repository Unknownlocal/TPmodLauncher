# SPDX-License-Identifier: MPL-2.0
"""Own one output stream and keep every storage operation on its worker."""

import importlib.util
from pathlib import Path
import threading

if __package__:
    from .reporting_queue import ReportingQueue
else:
    _spec = importlib.util.spec_from_file_location("_output_queue", Path(__file__).with_name("reporting_queue.py"))
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    ReportingQueue = _module.ReportingQueue


class OutputWriter:
    """Submit in memory; only explicit checkpoints/shutdown may wait briefly."""

    def __init__(self, name, open_output, store_error, *, release=None, activity=None):
        self.name, self.open_output = name, open_output
        self.store_error, self.release = store_error, release
        self.activity = activity
        self.queue, self.finished, self.failed = ReportingQueue(), threading.Event(), threading.Event()
        self._error_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="launcher-" + name, daemon=True)
        self._thread.start()

    def enqueue_log_record(self, text, *, ordinary=False, activity=None):
        self.queue.put((text, None, activity), ordinary=ordinary)

    def _store_error(self, error):
        # The callback only records plain state and enqueues warnings. Publish
        # it before failed: an owner may stop waiting as soon as it sees failed.
        with self._error_lock:
            if not self.failed.is_set():
                self.store_error(error, self.activity)
                self.failed.set()

    def checkpoint(self, timeout, *, activity=None):
        if not self.finished.is_set():
            complete = threading.Event()
            self.queue.put((None, complete, activity))
            if not complete.wait(timeout):
                self._store_error(TimeoutError(self.name + " output did not synchronise before its deadline"))
        return not self.failed.is_set()

    def close(self):
        self.queue.close()

    def wait(self, timeout):
        if not self.finished.wait(timeout):
            self._store_error(TimeoutError(self.name + " output did not close before its deadline"))
        return not self.failed.is_set()

    def _run(self):
        output = None
        try:
            try:
                output = self.open_output()
            except Exception as error:
                self._store_error(error)
            while True:
                jobs, dropped, done = self.queue.take()
                if dropped and output is not None:
                    try:
                        output.write(f"WARNING modLauncher.logging {self.name} output overflow: dropped {dropped} ordinary records\n")
                    except Exception as error:
                        self._store_error(error)
                for text, checkpoint, activity in jobs:
                    # Keep the failed operation's context while a later producer
                    # submits unrelated work, including during a blocked write.
                    self.activity = activity
                    try:
                        if output is not None:
                            if checkpoint is None:
                                output.write(text)
                            else:
                                output.sync()
                    except Exception as error:
                        self._store_error(error)
                    finally:
                        if checkpoint is not None:
                            checkpoint.set()
                if done:
                    break
            if output is not None:
                output.sync()
        except Exception as error:
            self._store_error(error)
        finally:
            try:
                if output is not None:
                    output.close()
            except Exception as error:
                self._store_error(error)
            finally:
                try:
                    if self.release is not None:
                        self.release()
                finally:
                    self.finished.set()
