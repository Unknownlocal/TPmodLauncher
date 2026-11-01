# SPDX-License-Identifier: MPL-2.0
"""Publish native changes through the shared background reporting outbox."""

from copy import deepcopy
from itertools import count
import threading
import weakref

if __package__:
    from .manager import Manager
    from .native_loading import NativeLoading
    from .native_failures import NativeFailures
    from .import_handover import ImportHandover
    from .diagnostics import build_log_record


class NativeEvents:
    """Collect only on actual changes, keeping periodic status replies cached."""

    def __init__(self, host):
        self.host, self.cache = host, {}
        self.active = self.closed = self._refreshing = False
        self._sequence = count(1)
        self._lock, self._pending = threading.Lock(), set()
        self._loading_done = []
        self._managers = weakref.WeakSet()
        self.handover = ImportHandover(self)
        self._previous = Manager.event_sink
        Manager.event_sink = self
        for manager in (host.manager, host.readiness, host.activity):
            self.bind(manager)
        self.loading = NativeLoading(host.readiness, host.activity)
        self.failures = NativeFailures(host.activity)
        host.settings.changed = self.refresh
        host.readiness.changed = self.refresh
        host.menu.changed = self.refresh
        host.adapters.changed = self.refresh

    def bind(self, manager):
        if self.closed:
            manager.events = None
            return
        manager.events = self
        self._managers.add(manager)
        self.handover.watch(manager.wanted)

    def watch_imports(self, names):
        self.handover.watch(names)

    def submit_log(self, record):
        """Route compatibility notices now, rather than waiting for a status reply."""
        self.host.activity.submit_log({**record, "event": "diagnostic"})
        self.manager_changed(self.host.manager)

    def publish(self, topic, status):
        if not self.active or self.closed or self.cache.get(topic) == status:
            return
        value = deepcopy(status)
        self.cache[topic] = value
        if topic == "pack" and value.get("state") not in ("ready", "unavailable"):
            return
        self.host.activity.submit_log({"event": topic, "event_sequence": next(self._sequence),
                                       "pack" if topic == "pack" else "status": value})

    def imported(self, manager):
        """A worker never installs hooks; its completion requests main-thread service."""
        with self._lock:
            if self.closed:
                return
            first = not self._pending
            self._pending.add(manager)
        if first and not self.closed:
            self.host.activity.submit_log({"event": "native-service"})

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

    def service(self):
        with self._lock:
            self._pending.clear()
            completed, self._loading_done = self._loading_done, []
        for attempt in completed:
            self.host.readiness.end_loading(attempt)
        for manager in tuple(self._managers):
            if not manager.closed:
                manager.poll()
        self.refresh()

    def ready_ui(self, imported):
        """Run import-dependent UI work before the native consumer uses it."""
        host = self.host
        host.menu.poll(imported)
        host.themes.poll(imported)
        host.settings.poll(imported)
        self.refresh()
        return True

    def refresh(self):
        if not self.active or self.closed or self._refreshing:
            return
        if threading.current_thread() is not threading.main_thread():
            self.imported(self.host.manager)
            return
        self._refreshing = True
        try:
            host = self.host
            self.publish("pack", host.discovery.poll())
            menu = host.menu.status()
            loading = menu.get("loading", {})
            if (loading.get("state") in ("complete", "error") or loading.get("timed_out")
                    or self.cache["pack"].get("state") == "unavailable"):
                if host.themes.finish_loading():
                    host.settings.refresh()
            self.publish("menu", menu)
            self.publish("themes", host.themes.status())
            self.publish("settings", host.settings.status())
            readiness = host.readiness.snapshot()
            readiness["activity"] = host.activity.snapshot()
            self.publish("readiness", readiness)
            if host.adapters.preload is not None:
                host.adapters.preload.poll()
            adapters = host.adapters.status()
            self.cache["adapters"] = deepcopy(adapters)
            preload = host.adapters.preload
            if preload is not None:
                preload.changed = self.refresh
                self.bind(preload)
                self.publish("career-preload", adapters.get("career_preload"))
        except Exception as error:
            self.host.activity.submit_log({**build_log_record("WARNING", "Native event collection failed: " + str(error)),
                                           "event": "diagnostic"}, error=error)
        finally:
            self._refreshing = False

    def start(self):
        self.active = True
        self.service()

    def status(self):
        """Return the latest plain values; an idle bridge pulse performs no collection."""
        return {"pack": self.cache.get("pack", {"state": "pending"}),
                "menu": self.cache.get("menu", {"state": "pending"}),
                "themes": self.cache.get("themes", {"state": "pending"}),
                "settings": self.cache.get("settings", {"state": "idle"}),
                "readiness": self.cache.get("readiness", {"watching": True}),
                "adapters": self.cache.get("adapters", {"state": "idle"}),
                "events_enabled": True}

    def close(self):
        self.closed = True
        self.handover.close()
        if Manager.event_sink is self:
            Manager.event_sink = self._previous
        with self._lock:
            self._pending.clear()
            self._loading_done.clear()
        self.loading.stop()
        self.failures.stop()
        for manager in self._managers:
            if manager.events is self:
                manager.events = None
        for owner in (self.host.settings, self.host.readiness, self.host.menu, self.host.adapters):
            owner.changed = None
