# SPDX-License-Identifier: MPL-2.0
"""Observe completion of imports whose loaders predate this launcher scope."""

import importlib._bootstrap
from queue import SimpleQueue
import sys
import threading
from types import ModuleType
import weakref


class ImportHandover:
    """Wait on existing import locks, never polling or blocking a game caller."""

    def __init__(self, events):
        self._events = weakref.ref(events)
        self._queue, self._seen = SimpleQueue(), weakref.WeakSet()
        self._thread, self._closed = None, False

    def watch(self, names):
        if self._closed:
            return
        for name in names:
            module = sys.modules.get(name)
            if (type(module) is ModuleType and getattr(getattr(module, "__spec__", None), "_initializing", False)
                    and module not in self._seen):
                self._seen.add(module)
                self._queue.put(name)
        if self._thread is None and self._seen:
            self._thread = threading.Thread(target=self._run, name="native-import-handover", daemon=True)
            self._thread.start()

    def _run(self):
        while (name := self._queue.get()) is not None:
            # Importlib releases this lock after execution and its final state
            # update. Release it before requesting any main-thread work.
            lock = importlib._bootstrap._get_module_lock(name)
            lock.acquire()
            lock.release()
            events = self._events()
            if events is not None and not self._closed:
                events.imported(events.host.manager, name)
            del events

    def close(self):
        self._closed = True
        self._queue.put(None)
        # An original import may itself be hung. This daemon owns no game lock
        # while waiting and keeps only a weak reference to the reporting scope.
