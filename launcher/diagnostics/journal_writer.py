# SPDX-License-Identifier: MPL-2.0
"""Keep journal encoding and storage off game execution threads."""

import atexit
import json
import threading
from time import monotonic

if __package__:
    from .reporting_queue import ReportingQueue, log_delivery, build_overflow_record


class JournalWriter:
    """Consume handed-off records without sharing a lock with storage operations."""

    def __init__(self, enqueue_fallback, notifications=None):
        self.enqueue_fallback = enqueue_fallback
        self.notifications = notifications
        self._queue, self._state_lock = ReportingQueue(), threading.Lock()
        self._last_journal = None
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
                key, ordinary = log_delivery(record) if failure is None else (None, False)
                self._last_journal = journal
                self._queue.put((record, journal, failure, None), key=key, ordinary=ordinary)

    def close(self):
        with self._state_lock:
            if not self._closed:
                self._closed = True
                self._queue.close()

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
            while True:
                jobs, dropped, done = self._queue.take()
                if dropped:
                    jobs.insert(0, (build_overflow_record(dropped, "Native journal queue"), self._last_journal, None, None))
                records, destination = [], None
                for record, journal, failure, checkpoint in jobs:
                    if records and (checkpoint is not None or journal is not destination):
                        self._write_batch(records, destination)
                        records = []
                    if checkpoint is not None:
                        checkpoint.set()
                        continue
                    if failure is not None:
                        # Source lookup/formatting stay on this worker; snapshots
                        # contain no live frames or local game values.
                        try:
                            text = "".join(failure.format())
                            record["traceback"] = "".join(c if c.isprintable() or c in "\n\t" else " " for c in text)
                        except Exception as error:
                            record["traceback"] = ("Deferred error formatting failed: " + str(error) + "\n"
                                + json.dumps({"root": failure.root, "nodes": failure.nodes}, ensure_ascii=True))
                    destination = journal
                    records.append(record)
                    # Do not postpone a failure's sync to format a later batch.
                    if record.get("fatal") or record.get("level") in ("ERROR", "CRITICAL"):
                        self._write_batch(records, destination)
                        records = []
                if records:
                    self._write_batch(records, destination)
                if done:
                    break
        finally:
            if self.notifications is not None:
                # The sender has its own thread: network waits cannot delay any
                # append. Completion includes its final delivery, but only fatal
                # interpreter exit may wait on this event, within the shared budget.
                self.notifications.close()
                self.notifications.finished.wait()
            self.finished.set()

    def _write_batch(self, records, journal):
        """Write one destination's batch, retaining all records on storage failure."""
        try:
            if journal is None:
                for record in records:
                    self.enqueue_fallback(record)
            else:
                journal.append_records(records)
                if self.notifications is not None:
                    self.notifications.enqueue_journal_notice()
        except Exception as error:
            self.enqueue_fallback({"level": "WARNING", "component": "activity",
                                   "detail": "Native activity journal failed: " + str(error)})
            for record in records:
                self.enqueue_fallback(record)
