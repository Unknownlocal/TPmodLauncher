# SPDX-License-Identifier: MPL-2.0
"""Keep plain activity context separate from game objects and error presentation."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from itertools import count
import threading
from time import monotonic


class ActivityTracker:
    """Track nested host work and ordered native observations per owned process.

    Operations can span generator yields, so their lifetime is stored explicitly.
    Focus is temporary and local to each execution context; another process or
    worker cannot inherit a paused loader's activity by accident.
    """

    WORDING = {"launcher_startup": "while starting the launcher", "game_loading": "while loading the game",
               "mod_loading": "while loading code mods", "save_loading": "while loading your save",
               "in_save": "while in a save", "menu": "while in the game menus",
               "week_progression": "while progressing forward a week in game", "saving": "while saving",
               "shutdown": "while closing the game", "unknown": "during an unknown game activity"}

    def __init__(self, submit_log=lambda message: None):
        self.submit_log = submit_log
        self._ids, self._lock = count(1), threading.RLock()
        self._focus = ContextVar("launcher_activity_focus", default=(None, False))
        self._processes, self._operations, self._foreground = {}, {}, None
        self._default = self._record("launcher_startup")

    def _record(self, kind, **context):
        """Timestamp host entry/observation, without claiming the game's start time."""
        description = self.WORDING[kind]
        if kind == "mod_loading" and context.get("mod"):
            description = "while loading " + context["mod"]
        return dict(kind=kind, description=description, operation=next(self._ids),
                    started=monotonic(), thread=threading.get_ident(), **context)

    def _submit_activity(self, record):
        import json

        self.submit_log("ACTIVITY " + json.dumps(record, ensure_ascii=True))

    def attach(self, pid):
        with self._lock:
            record = self._record("game_loading", pid=pid, process_session=next(self._ids))
            self._processes[pid] = record
            self._operations = {key: row for key, row in self._operations.items() if row.get("pid") != pid}
            self._foreground = pid
        self._submit_activity(record)

    def retire(self, pid):
        with self._lock:
            self._processes.pop(pid, None)
            self._operations = {key: row for key, row in self._operations.items() if row.get("pid") != pid}
            if self._foreground == pid:
                self._foreground = next(reversed(self._processes), None)
            if not self._processes:
                self._default = self._record("unknown")

    @staticmethod
    def _native_operations(status):
        """Keep native operation identity distinct from this controller's records."""
        return {"native_" + key: status[key] for key in ("operation", "parent", "thread")
                if type(status.get(key)) is int and status[key] > 0}

    def observe(self, pid, status):
        """Accept only newer native activity from an already verified session."""
        if type(status) is not dict or status.get("kind") not in ("game_loading", "save_loading", "in_save", "menu", "unknown", "week_progression", "saving"):
            return
        sequence, generation = status.get("sequence"), status.get("generation")
        if type(sequence) is not int or sequence < 0 or type(generation) is not int or generation < 0:
            return
        with self._lock:
            previous = self._processes.get(pid)
            if previous is None or sequence <= previous.get("sequence", -1):
                return
            if generation < previous.get("generation", 0):
                return
            record = self._record(status["kind"], pid=pid, process_session=previous["process_session"],
                                  generation=generation, sequence=sequence, **self._native_operations(status))
            self._processes[pid] = record
            self._foreground = pid
        self._submit_activity(record)

    def native_context(self, pid, status):
        """Keep an older diagnostic's career context without rewinding live state."""
        if (type(status) is not dict or status.get("kind") not in ("game_loading", "save_loading", "in_save", "menu", "unknown", "week_progression", "saving")
                or type(status.get("sequence")) is not int or status["sequence"] < 0
                or type(status.get("generation")) is not int or status["generation"] < 0):
            return None
        with self._lock:
            process = self._processes.get(pid)
            if process is not None:
                return self._record(status["kind"], pid=pid, process_session=process["process_session"],
                                    generation=status["generation"], sequence=status["sequence"], **self._native_operations(status))

    def snapshot(self, pid=None, *, native=False):
        with self._lock:
            focused, native_focus = self._focus.get()
            if pid is None:
                pid = focused if focused is not None else self._foreground
            native = native or (focused == pid and native_focus)
            base = self._processes.get(pid, self._default)
            if not native:
                for row in reversed(tuple(self._operations.values())):
                    if row.get("pid") in (None, pid) and row["thread"] == threading.get_ident():
                        return deepcopy(row)
            return deepcopy(base)

    @staticmethod
    def _preserve(error, record):
        if not hasattr(error, "launcher_activity"):
            error.launcher_activity = deepcopy(record)

    @contextmanager
    def focus(self, pid, *, native=False):
        """Limit one controller callback/poll to the process it actually services."""
        token = self._focus.set((pid, native))
        try:
            yield
        except Exception as error:
            self._preserve(error, self.snapshot(pid, native=native))
            raise
        finally:
            self._focus.reset(token)

    @contextmanager
    def scope(self, kind, **context):
        """Keep work active across yields and preserve its innermost failure context."""
        with self._lock:
            parent = self.snapshot(context.get("pid"))
            pid = context.get("pid", parent.get("pid"))
            fields = {key: parent[key] for key in ("process_session", "generation") if pid is not None and key in parent}
            fields.update(context)
            if pid is not None:
                fields["pid"] = pid
            record = self._record(kind, parent=parent["operation"], **fields)
            self._operations[record["operation"]] = record
        try:
            self._submit_activity(record)
            yield record
        except Exception as error:
            self._preserve(error, record)
            raise
        finally:
            with self._lock:
                self._operations.pop(record["operation"], None)
