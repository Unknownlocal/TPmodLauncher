# SPDX-License-Identifier: MPL-2.0
"""Receive native reporting events without periodic file or socket checks."""

from collections import deque
import secrets
import select
import socket
import threading
from time import monotonic

from .reporting_wire import reporting_read, REPORTING_TRANSFER_SECONDS


class ReportingReceiver:
    """Validate one owned native connection and wake its controller with plain data."""

    def __init__(self, pid, session, enqueue_event):
        self.pid, self.session, self.enqueue_event = pid, session, enqueue_event
        self.records = deque()
        self.connected, self.finished = threading.Event(), threading.Event()
        self.confirmed = False
        self.control_ready = None
        self.control_failed = None
        self.expected = False
        self._closed = False
        self._thread = self._timer = None
        self._listener = socket.socket()
        self._interrupt_read = self._interrupt_write = None
        try:
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(4)
            self._interrupt_read, self._interrupt_write = socket.socketpair()
            self._interrupt_write.setblocking(False)
            self.endpoint = {"port": self._listener.getsockname()[1], "token": secrets.token_hex(32), "session": session}
        except BaseException:
            self.close()
            raise

    def start(self):
        self._thread = threading.Thread(target=self._run, name="native-reporting-receiver", daemon=True)
        self._thread.start()

    def expect_connection(self):
        """Start a single deadline only after Python actually created its observer."""
        self.expected = True
        if self._timer is None and not self.connected.is_set() and not self._closed:
            self._timer = threading.Timer(REPORTING_TRANSFER_SECONDS, self._connection_expired)
            self._timer.daemon = True
            self._timer.start()

    def _connection_expired(self):
        if not self.connected.is_set() and not self._closed:
            self._failed("Native reporting did not connect after manager startup")

    def _failed(self, detail):
        if self.control_failed is not None:
            self.control_failed(detail)
        self.enqueue_event("reporting-closed", detail)

    def _run(self):
        authenticated = False
        try:
            while True:
                readable, _, _ = select.select([self._listener, self._interrupt_read], [], [])
                if self._interrupt_read in readable:
                    return
                connection, _ = self._listener.accept()
                with connection:
                    connection.setblocking(False)
                    try:
                        sequence, hello = reporting_read(connection, interrupt=self._interrupt_read,
                                                         deadline=monotonic() + REPORTING_TRANSFER_SECONDS, limit=1024)
                        if (sequence != 0 or type(hello) is not dict or set(hello) != {"version", "session", "token", "pid"}
                                or type(hello["version"]) is not int or hello["version"] != 1
                                or type(hello["pid"]) is not int or hello["pid"] != self.pid
                                or hello["session"] != self.session or type(hello["token"]) is not str
                                or len(hello["token"]) != 64 or any(c not in "0123456789abcdef" for c in hello["token"])
                                or not secrets.compare_digest(hello["token"], self.endpoint["token"])):
                            continue  # A foreign peer cannot latch or receive owner data.
                        authenticated = True
                        self.connected.set()
                        if self._timer is not None:
                            self._timer.cancel()
                        while True:
                            next_sequence, message = reporting_read(connection, interrupt=self._interrupt_read)
                            if next_sequence != sequence + 1 or type(message) is not dict:
                                raise ValueError("Invalid native reporting sequence/message")
                            sequence = next_sequence
                            kind = message.get("kind")
                            if kind == "stopped" and set(message) == {"kind"}:
                                self.confirmed = True
                                return
                            if kind == "control-ready" and set(message) == {"kind", "request", "dependency"}:
                                if (type(message["request"]) is not str or len(message["request"]) != 32
                                        or any(c not in "0123456789abcdef" for c in message["request"])
                                        or message["dependency"] not in ("imports", "operations")):
                                    raise ValueError("Invalid native control completion")
                                if self.control_ready is not None:
                                    self.control_ready(message["request"], message["dependency"])
                                continue
                            if kind == "fallback" and set(message) == {"kind", "record"} and type(message["record"]) is dict:
                                self.records.append(message["record"])
                            elif kind != "journal-ready" or set(message) != {"kind"}:
                                raise ValueError("Unknown native reporting message")
                            self.enqueue_event("journal-ready", None)
                    except (EOFError, ValueError, OSError):
                        if authenticated:
                            raise
        except Exception as error:
            if not self._closed:
                self._failed(str(error))
        finally:
            self.finished.set()

    def close(self, *, drain=False):
        """Finish acknowledged delivery before cancellation; called only by the launcher."""
        if self._closed:
            return
        complete = not drain or self._thread is None or self.finished.wait(1)
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
        if self._interrupt_write is not None:
            try:
                self._interrupt_write.send(b"x")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._listener.close()
        for connection in (self._interrupt_read, self._interrupt_write):
            if connection is not None:
                connection.close()
        if not complete or self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Native reporting final delivery was not confirmed")
