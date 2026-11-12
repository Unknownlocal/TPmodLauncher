# SPDX-License-Identifier: MPL-2.0
"""Publish native changes through the shared background reporting outbox."""

from copy import deepcopy
from contextlib import contextmanager
from itertools import count
import threading
import weakref

if __package__:
    from .manager import Manager
    from .native_loading import NativeLoading
    from .native_failures import NativeFailures
    from .native_restart import NativeRestart
    from .workshop_metadata import WorkshopMetadata
    from .import_handover import ImportHandover
    from .control_locks import ControlLocks
    from .diagnostics import build_log_record


class NativeEvents:
    """Collect on actual changes and wake controls on named completion events."""

    def __init__(self, host):
        self.host, self.cache = host, {}
        self.reporting = host.reporting
        self.active = self.closed = self._refreshing = False
        self._batch_depth, self._refresh_pending = 0, False
        self._polled, self._preload_dirty = set(), False
        self._sequence = count(1)
        self._lock, self._pending = threading.Lock(), set()
        self._loading_done = []
        self._managers = weakref.WeakSet()
        self.handover = ImportHandover(self)
        self.control_locks = ControlLocks(self)
        self._previous = Manager.event_sink
        Manager.event_sink = self
        self.control_imports, self.control_id = None, None
        for manager in (host.manager, host.readiness, host.activity):
            self.bind(manager)
        self.control_imports = Manager()
        self.loading = NativeLoading(host.readiness, host.activity)
        self.failures = NativeFailures(host.activity)
        self.restart = NativeRestart(self)
        self.workshop = WorkshopMetadata(host.activity)
        host.settings.changed = self.refresh
        host.readiness.changed = self.readiness_changed
        host.menu.changed = self.refresh
        host.adapters.changed = self.refresh

    def bind(self, manager):
        with self._lock:
            if self.closed:
                manager.events = None
                return
            manager.events = self
            self._managers.add(manager)
        self.handover.watch(manager.wanted)

    def watch_imports(self, names):
        self.handover.watch(names)

    def control_ready(self, request, dependency):
        """Notify only the matching control; no native callback address is retained."""
        if not self.closed:
            self.reporting.control_ready(request, dependency)

    def watch_control_imports(self, names):
        """Observe the actual imports that can hold up native default definitions."""
        request = self.control_id
        def completed(module):
            self.control_ready(request, "imports")
            return True
        for name in names:
            self.control_imports.observe(name, completed)

    def watch_control_lock(self, lock):
        self.control_locks.watch(lock, self.control_id)

    def submit_log(self, record):
        """Route compatibility notices now, rather than waiting for a status reply."""
        self.reporting.submit_log({**record, "event": "diagnostic"})
        self.manager_changed(self.host.manager)

    def publish(self, topic, status):
        if not self.active or self.closed or self.cache.get(topic) == status:
            return
        value = deepcopy(status)
        self.cache[topic] = value
        if topic == "pack" and value.get("state") not in ("ready", "unavailable"):
            return
        self.reporting.enqueue_event({"event": topic, "event_sequence": next(self._sequence),
                                       "pack" if topic == "pack" else "status": value})

    def imported(self, manager, name=None):
        """A worker never installs hooks; its completion requests main-thread service."""
        with self._lock:
            if self.closed:
                return
            first = not self._pending
            self._pending.add(manager)
            # An import-lock handover may name only the foundation manager.
            # Wake every actual observer of that module, not every manager.
            if name is not None:
                self._pending.update(owner for owner in tuple(self._managers) if name in owner.wanted)
        if self.control_imports is not None and name in self.control_imports.wanted:
            self.control_ready(self.control_id, "imports")
        if first and not self.closed:
            self.reporting.service_ready()

    def manager_changed(self, manager):
        if threading.current_thread() is not threading.main_thread():
            self.imported(manager)
        else:
            self.refresh()

    def loading_finished(self, attempt):
        with self._lock:
            if self.closed:
                return
            self._loading_done.append(attempt)
        self.imported(self.host.manager)

    def readiness_changed(self):
        """Recheck a captured preload only when navigation/loading can invalidate it."""
        self._preload_dirty = True
        self.refresh()

    def poll_manager(self, manager, *, force=False):
        """Share dependency installation within a pass without repeated polling."""
        with self.batch():
            if force:
                self._polled.discard(manager)
            if self.closed or manager.closed or manager in self._polled:
                return manager.status()
            self._polled.add(manager)
            return manager.poll()

    def service(self):
        """Service named pending owners, then collect one final state snapshot."""
        if self.closed:
            return
        with self._lock:
            pending, self._pending = self._pending, set()
            completed, self._loading_done = self._loading_done, []
        with self.batch():
            # A new completion may supersede work earlier in this control.
            self._polled.difference_update(pending)
            for attempt in completed:
                self.host.readiness.end_loading(attempt)
            # Career preload depends on the shared navigation hooks. Its own
            # poll must see navigation installation from this same completion.
            for manager in sorted(pending, key=lambda owner: owner is not self.host.readiness):
                if not manager.closed:
                    self.poll_manager(manager)
            self.refresh(force=True)

    @contextmanager
    def batch(self):
        """Defer nested collection until a control or main-thread import settles.

        Worker imports never hold this main-thread boundary while importing.
        Their completion only queues the managers that need main-thread work.
        """
        main_thread = threading.current_thread() is threading.main_thread()
        if main_thread:
            if not self._batch_depth and not self._refreshing:
                self._polled.clear()
            self._batch_depth += 1
        try:
            yield
        finally:
            if main_thread:
                self._batch_depth -= 1
                if not self._batch_depth and self._refresh_pending:
                    self.refresh()
                # Dependency checks inside collection share the same pass.
                if not self._batch_depth and not self._refreshing:
                    self._polled.clear()

    def ready_ui(self, imported):
        """Run import-dependent UI work before the native consumer uses it."""
        host = self.host
        with self.batch():
            host.menu.poll(imported)
            host.themes.poll(imported)
            host.settings.poll(imported)
            self.refresh()
        return True

    def refresh(self, *, force=False):
        """Request main-thread collection, deferring nested changes until batch end."""
        if not self.active or self.closed:
            return
        if threading.current_thread() is not threading.main_thread():
            self.imported(self.host.manager)
            return
        if self._refreshing:
            return
        self._refresh_pending = True
        if self._batch_depth and not force:
            return
        self._refresh_pending = False
        self._refreshing = True
        try:
            readiness, pack, menu = self._prepare_state()
            self._collect_and_publish(readiness, pack, menu)
        except Exception as error:
            self.reporting.submit_log({**build_log_record("WARNING", "Native event collection failed: " + str(error)),
                                           "event": "diagnostic"}, error=error)
        finally:
            self._refreshing = False
            if not self._batch_depth:
                self._polled.clear()

    def _prepare_state(self):
        """Settle loading changes before collecting theme, settings and adapter state."""
        host = self.host
        if self._preload_dirty:
            self._preload_dirty = False
            if host.adapters.preload is not None:
                self.poll_manager(host.adapters.preload)
        # Availability can notify preload departure. Read it before adapter
        # status so that departure is included in this same collection.
        readiness = host.readiness.snapshot(poll=False)
        readiness["activity"] = host.activity.snapshot(poll=False)
        pack, menu = host.discovery.poll(), host.menu.status()
        loading = menu.get("loading", {})
        if (loading.get("state") in ("complete", "error") or loading.get("timed_out")
                or pack.get("state") == "unavailable"):
            if host.themes.finish_loading():
                host.settings.refresh()
        return readiness, pack, menu

    def _collect_and_publish(self, readiness, pack, menu):
        """Publish changed values and cache adapters after state preparation."""
        host = self.host
        self.publish("restart-observation", self.restart.status())
        self.publish("pack", pack)
        self.publish("menu", menu)
        self.publish("themes", host.themes.status())
        self.publish("settings", host.settings.status())
        self.publish("readiness", readiness)
        adapters = host.adapters.status(poll=False)
        self.cache["adapters"] = deepcopy(adapters)
        if host.adapters.preload is not None:
            self.publish("career-preload", adapters.get("career_preload"))

    def start(self):
        self.active = True
        # Bootstrap is the one full installation pass. Later service requests
        # use actual pending owners, including managers created by adapters.
        with self._lock:
            self._pending.update(self._managers)
        self.service()

    def status(self):
        """Return cached plain values for an explicit status request."""
        return {"pack": self.cache.get("pack", {"state": "pending"}),
                "menu": self.cache.get("menu", {"state": "pending"}),
                "themes": self.cache.get("themes", {"state": "pending"}),
                "settings": self.cache.get("settings", {"state": "idle"}),
                "readiness": self.cache.get("readiness", {"watching": True}),
                "adapters": self.cache.get("adapters", {"state": "idle"}),
                "events_enabled": True, "workshop_metadata": self.workshop.status()}

    def close(self):
        self.closed = True
        self.control_locks.close()
        self.control_imports.stop()
        self.handover.close()
        if Manager.event_sink is self:
            Manager.event_sink = self._previous
        with self._lock:
            self._pending.clear()
            self._loading_done.clear()
        self.loading.stop()
        self.failures.stop()
        self.restart.stop()
        self.workshop.stop()
        for manager in self._managers:
            if manager.events is self:
                manager.events = None
        for owner in (self.host.settings, self.host.readiness, self.host.menu, self.host.adapters):
            owner.changed = None
