# SPDX-License-Identifier: MPL-2.0
"""Send journal notifications without holding up the journal writer or gameplay."""

import os
import socket
import threading

if __package__:
    from .reporting_wire import reporting_write
    from .reporting_queue import ReportingQueue, log_delivery, build_overflow_record


class ReportingSender:
    """Own one authenticated connection; callers only enqueue plain messages."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self._queue = ReportingQueue()
        self.finished = threading.Event()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="mod-launcher-reporting", daemon=True)
        self._thread.start()

    def enqueue_journal_notice(self):
        self._queue.put({"kind": "journal-ready"}, key="journal-ready")

    def enqueue_control_ready(self, request, dependency):
        """Wake one parked control without waiting for journal storage."""
        self._queue.put({"kind": "control-ready", "request": request, "dependency": dependency},
                        key=("control-ready", request, dependency))

    def enqueue_log_record(self, record):
        """Preserve a complete diagnostic when storage could not carry it."""
        key, ordinary = log_delivery(record)
        self._queue.put({"kind": "fallback", "record": record}, key=key, ordinary=ordinary)

    def close(self):
        """Called once by the journal worker after its final record, without waiting."""
        self._queue.close()

    def _run(self):
        try:
            with socket.create_connection(("127.0.0.1", self.endpoint["port"]), timeout=2) as connection:
                connection.setblocking(False)
                reporting_write(connection, 0, {"version": 1, "session": self.endpoint["session"],
                                               "token": self.endpoint["token"], "pid": os.getpid()})
                sequence = 0
                while True:
                    messages, dropped, done = self._queue.take(1)
                    if dropped:
                        messages.insert(0, {"kind": "fallback", "record": build_overflow_record(dropped, "Native socket queue")})
                    for message in messages:
                        sequence += 1
                        reporting_write(connection, sequence, message)
                    if done:
                        break
                reporting_write(connection, sequence + 1, {"kind": "stopped"})
        except Exception as error:
            # The receiver diagnoses EOF or its one-off connection deadline.
            # Never fall back to a future game-thread pulse or native stderr.
            self.error = str(error)
        finally:
            self.finished.set()
