# SPDX-License-Identifier: MPL-2.0
"""Bound routine reporting work without discarding failures or control events."""

from collections import OrderedDict
from itertools import count
import threading


def log_delivery(record):
    """Return a safe replacement key and whether an ordinary record may overflow."""
    if (record.get("fatal") or "traceback" in record or "reason" in record
            or record.get("level") not in (None, "DEBUG", "INFO")):
        return None, False
    event = record.get("event", "native-activity")
    if event in ("readiness", "menu", "themes"):
        status = record.get("status", {})
        if type(status) is not dict:
            return None, False
        loading = status.get("loading", {})
        if loading is None:
            loading = {}
        if type(loading) is not dict:
            return None, False
        if (not status.get("detail") and not status.get("traceback")
                and status.get("state") not in ("failed", "unavailable", "error")
                and loading.get("state") != "error" and not loading.get("timed_out")):
            return ("status", event), False
    # Loading attempts, settings saves and import/control events have effects
    # beyond their displayed status. They must never disappear as routine noise.
    return None, event in ("native-activity", "diagnostic")


def build_overflow_record(count, component):
    """Make any loss of routine information visible without inventing a failure."""
    return {"event": "diagnostic", "level": "WARNING", "component": "activity",
            "detail": f"{component} overflow: dropped {count} ordinary informational records",
            "dropped_records": count}


class ReportingQueue:
    """Keep fixed routine capacity, latest safe hints and every protected record.

    Producers only change memory under a short lock. Failure count and message
    size remain unbounded: complete errors cannot also have a hard memory limit.
    """

    def __init__(self):
        self._capacity = 256
        self._condition = threading.Condition()
        self._items, self._ordinary, self._keys = OrderedDict(), OrderedDict(), {}
        self._tokens = count()
        self._dropped = 0
        self._closed = False

    def put(self, value, *, key=None, ordinary=False):
        with self._condition:
            if self._closed:
                return
            if key is not None and key in self._keys:
                # Reinsert the latest hint at its actual arrival position;
                # protected failures keep their original relative order.
                self._remove(self._keys[key])
            if ordinary and len(self._ordinary) >= self._capacity:
                self._remove(next(iter(self._ordinary)))
                self._dropped += 1
            token = next(self._tokens)
            self._items[token] = (value, key)
            if ordinary:
                self._ordinary[token] = None
            if key is not None:
                self._keys[key] = token
            self._condition.notify()

    def _remove(self, token):
        value, key = self._items.pop(token)
        self._ordinary.pop(token, None)
        if key is not None:
            del self._keys[key]
        return value

    def take(self, limit=64, *, block=True):
        """Take ready work and its drop count; final True means closed and drained.

        This only acknowledges queue removal, not completion of storage/delivery.
        """
        with self._condition:
            while block and not self._items and not self._closed:
                self._condition.wait()
            values = []
            while self._items and (limit is None or len(values) < limit):
                values.append(self._remove(next(iter(self._items))))
            dropped, self._dropped = self._dropped, 0
            return values, dropped, self._closed and not self._items

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
