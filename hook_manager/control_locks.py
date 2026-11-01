# SPDX-License-Identifier: MPL-2.0
"""Wake parked controls when a contended registry lock is released."""

import threading
import weakref


class ControlLocks:
    """Share one blocking observer per lock, never waiting on the game thread."""

    def __init__(self, events):
        self._events = weakref.ref(events)
        self._lock, self._pending = threading.Lock(), {}
        self._closed = False

    def watch(self, lock, request):
        with self._lock:
            if self._closed or request is None:
                return
            first = lock not in self._pending
            self._pending[lock] = request
        if first:
            threading.Thread(target=self._wait, args=(lock,), name="native-control-lock", daemon=True).start()

    def _wait(self, lock):
        lock.acquire()
        lock.release()
        with self._lock:
            request = self._pending.pop(lock, None)
            closed = self._closed
        events = self._events()
        if events is not None and not closed:
            events.control_ready(request, "operations")

    def close(self):
        # A native owner may never release its lock. Keep only that lock and a
        # weak event owner in the daemon; closing/unloading never joins it.
        with self._lock:
            self._closed = True
            self._pending.clear()
