# SPDX-License-Identifier: MPL-2.0
"""Wake operations belonging to one retired process without running cleanup."""

import threading

from .process_ended import ProcessEnded


class ProcessCancellation:
    """Own cancellation and pending terminal evidence, without teardown or I/O."""

    def __init__(self):
        self.ended = threading.Event()
        self._lock, self._wakes = threading.Lock(), set()
        self._evidence, self._sealed = {}, False

    def capture(self, message):
        """Retain a delivered diagnostic until handled or the owner is retired."""
        with self._lock:
            if self._sealed:
                return False
            self._evidence[id(message)] = message
            return True

    def consume(self, message):
        """Handle each delivery once, even if final cleanup reached it first."""
        with self._lock:
            return self._evidence.pop(id(message), None) is not None

    def finish_evidence(self):
        """Set the retirement boundary atomically with taking the final messages."""
        with self._lock:
            self._sealed = True
            messages, self._evidence = tuple(self._evidence.values()), {}
            return messages

    def cancel(self):
        """Wake pending work once; return whether this was the first exit signal."""
        with self._lock:
            if self.ended.is_set():
                return False
            self.ended.set()
            wakes = tuple(self._wakes)
        for wake in wakes:
            try:
                wake()
            except Exception:
                pass  # A failed native cancellation cannot suppress other wake-ups.
        return True

    def watch(self, wake):
        """Register a short wake-up, including when the owner has already ended."""
        with self._lock:
            self._wakes.add(wake)
            ended = self.ended.is_set()
        if ended:
            wake()

    def unwatch(self, wake):
        with self._lock:
            self._wakes.discard(wake)

    def check(self):
        if self.ended.is_set():
            raise ProcessEnded()
