# SPDX-License-Identifier: MPL-2.0
"""Reuse one waiting thread for an Engine's successive execution deadlines.

Starting a fresh timer for every short mod check can itself exhaust the action
budget. This thread starts once and sleeps between deadlines. Cancellation holds
the same lock as epoch interruption, so a previous timeout cannot reach the next
owner after cancellation returns.
"""

import threading
import time


class DeadlineWatchdog:
    def __init__(self, engine):
        self.engine = engine
        self.condition = threading.Condition()
        self.thread = None
        self.deadline = self.token = None
        self.closed = False

    def arm(self, deadline):
        """Set an absolute deadline and return its private cancellation token."""
        with self.condition:
            if self.closed:
                raise RuntimeError("Deadline watchdog is closed")
            if self.thread is None:
                # Preparation alone needs no thread; start on the first timed call.
                thread = threading.Thread(target=self.run, name="mod-deadline", daemon=True)
                thread.start()
                self.thread = thread
            self.deadline, self.token = deadline, object()
            self.condition.notify()
            return self.token

    def cancel(self, token):
        """Disarm this deadline, waiting for an already-started interruption."""
        if token is None:
            return
        with self.condition:
            if self.token is token:
                self.deadline = self.token = None
                self.condition.notify()

    def run(self):
        with self.condition:
            while not self.closed:
                if self.deadline is None:
                    self.condition.wait()
                    continue
                remaining = self.deadline - time.monotonic()
                if remaining > 0:
                    # A wake-up can replace or cancel the deadline: read it again.
                    self.condition.wait(remaining)
                    continue
                self.deadline = self.token = None
                # Cancel and close must wait until this native call finishes.
                # The Engine stays alive until close has joined this thread.
                self.engine.increment_epoch()

    def close(self):
        """Stop and join before the compiled-code owner frees the Engine."""
        with self.condition:
            self.closed = True
            self.deadline = self.token = None
            self.condition.notify()
        if self.thread is not None:
            self.thread.join()
