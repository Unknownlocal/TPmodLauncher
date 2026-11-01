# SPDX-License-Identifier: MPL-2.0
"""Keep journal encoding and storage off game execution threads."""

import atexit
from queue import SimpleQueue
import threading
from time import monotonic


class JournalWriter:
    """Consume handed-off records without sharing a lock with storage operations."""

    def __init__(self, enqueue_fallback, notifications=None):
        self.enqueue_fallback = enqueue_fallback
        self.notifications = notifications
        self._queue, self._state_lock = SimpleQueue(), threading.Lock()
        self._closed, self.finished = False, threading.Event()
        self._exit_deadline = None
        self._thread = threading.Thread(target=self._run, name="mod-launcher-journal", daemon=True)
        self._thread.start()
        # Only interpreter termination may wait. Ordinary reporting and bridge
        # callbacks enqueue/close and return; a hung filesystem cannot stall play.
        atexit.register(self.finish_on_exit)

    def enqueue_log_record(self, record, journal, failure=None):
        with self._state_lock:
            if not self._closed:
                self._queue.put((record, journal, failure, None))

    def close(self):
        with self._state_lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)

    def finish(self, timeout):
        """Bounded termination drain; never call this from gameplay callbacks."""
        self.close()
        return self.finished.wait(timeout)

    def finish_on_exit(self):
        """Share one one-second budget between fatal handling and interpreter exit."""
        with self._state_lock:
            if self._exit_deadline is None:
                self._exit_deadline = monotonic() + 1.0
            remaining = max(0, self._exit_deadline - monotonic())
        return self.finish(remaining)

    def wait_idle(self, timeout):
        """Allow owned verification code to wait for earlier records without closing."""
        with self._state_lock:
            event = self.finished if self._closed else threading.Event()
            if not self._closed:
                self._queue.put((None, None, None, event))
        return event.wait(timeout)

    def _run(self):
        try:
            while (job := self._queue.get()) is not None:
                record, journal, failure, checkpoint = job
                if checkpoint is not None:
                    checkpoint.set()
                    continue
                try:
                    if failure is not None:
                        # The captured traceback contains no live frames/locals.
                        # Source lookup and formatting stay off gameplay threads.
                        text = "".join(failure.format())
                        record["traceback"] = "".join(c if c.isprintable() or c in "\n\t" else " " for c in text)
                    if journal is not None:
                        journal.append_record(record)
                        if self.notifications is not None:
                            self.notifications.enqueue_journal_notice()
                    else:
                        self.enqueue_fallback(record)
                except Exception as error:
                    self.enqueue_fallback({"level": "WARNING", "component": "activity",
                                           "detail": "Native activity journal failed: " + str(error),
                                           "activity": record.get("activity")})
                    self.enqueue_fallback(record)
        finally:
            if self.notifications is not None:
                # The sender has its own thread: network waits cannot delay any
                # append. Completion includes its final delivery, but only fatal
                # interpreter exit may wait on this event, within the shared budget.
                self.notifications.close()
                self.notifications.finished.wait()
            self.finished.set()
