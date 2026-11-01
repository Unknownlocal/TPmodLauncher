# SPDX-License-Identifier: MPL-2.0
"""Observe complete native week/save calls without changing their execution."""

from collections import deque
from functools import update_wrapper
from itertools import count
import threading
import weakref
import sys

if __package__:
    from .callables import CallableTarget
    from .diagnostics import build_log_record
    from .manager import Manager
    from launcher.diagnostics.journal_writer import JournalWriter
    from launcher.diagnostics.exception_snapshot import ExceptionSnapshot
    from launcher.diagnostics.reporting_sender import ReportingSender


class NativeActivity(Manager):
    """Keep nested operations and worker failures separate from cached navigation.

    The game still owns week/save execution and exceptions. Its callbacks enqueue
    memory snapshots; a diagnostics worker handles formatting and storage without
    a game-thread wait. No guest calls or game persistence changes are involved.
    """

    TARGETS = (
        ("week", "game_state", "GameState", "advance_week", "(self, off_week=False, *, persist=True)"),
        ("rollover", "game_state", "GameState", "finalize_and_rollover_season", "(self)"),
        ("save", "game_state", "GameState", "save_season", "(self, *, manual=False)"),
        ("start", "ui.week_advance_transition", "WeekAdvanceTransitionMixin", "_start_background_week_advance", "(self)"),
        ("finish", "ui.week_advance_transition", "WeekAdvanceTransitionMixin", "_finish_background_week_advance", "(self, *, show_review=True)"),
    )

    def __init__(self, readiness, journal=None, reporting_endpoint=None):
        super().__init__(lambda record: None)
        self.readiness, self.records = readiness, deque()
        self.journal = journal
        self._ids, self._sequences = count(1), count(1)
        self._lock, self._operations = threading.RLock(), {}
        self._status, self._key = None, None
        notifications = ReportingSender(reporting_endpoint) if reporting_endpoint is not None else None
        self.writer = JournalWriter(notifications.enqueue_log_record if notifications is not None else self.records.append,
                                    notifications)
        self._exception_hook = sys.excepthook
        def unhandled(kind, error, traceback):
            try:
                if hasattr(error, "launcher_activity"):
                    record = build_log_record("CRITICAL", str(error), component="activity")
                    record["activity"] = dict(error.launcher_activity)
                    self.submit_log({**record, "reason": str(error), "fatal": True}, error=error)
                    self.writer.finish_on_exit()  # Gameplay has failed; termination is bounded.
            except Exception:
                pass  # Diagnostic formatting must not replace the native exception hook.
            finally:
                self._exception_hook(kind, error, traceback)
        self._unhandled = unhandled
        sys.excepthook = unhandled
        for name, module, owner, method, signature in self.TARGETS:
            target = CallableTarget("activity." + name, module, (owner,), method, signature)
            self.approve(target)
            try:
                self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            except Exception:
                pass  # Manager retains the compatibility failure; native calls stay intact.

    def store_error(self, error):
        """Retain compatibility history and also queue its complete diagnostic."""
        self.error_record = build_log_record("ERROR", str(error), component="activity")
        self.errors.append(str(error)[:4096])
        del self.errors[:-16]
        self.submit_log(self.error_record, error=error)

    def submit_log(self, record, *, error=None):
        """Capture plain context and enqueue without encoding, storage or console I/O."""
        failure = ExceptionSnapshot(error) if error is not None else None
        with self._lock:
            if self.closed:
                return
            self.writer.enqueue_log_record(record, self.journal, failure)

    def group_intact(self, name):
        """Accept later native decorators that still call our installed observer."""
        group = self.groups.get(name)
        if self.closed or group is None or group["state"] != "active":
            return False
        for owner, attribute, _, installed, _ in group["changes"]:
            current, seen = vars(owner).get(attribute), set()
            while current is not installed and current is not None and id(current) not in seen:
                seen.add(id(current))
                current = getattr(current, "__wrapped__", None)
            if current is not installed:
                return False
        return True

    def _context(self, row):
        return {key: row[key] for key in ("kind", "operation", "parent", "thread", "generation", "sequence")}

    def _visible(self, *, foreground):
        base = self.readiness.current_activity()
        thread = threading.get_ident()
        for operation, row in tuple(self._operations.items()):
            if row["persistent"] and row["screen"]() is None:
                self._operations.pop(operation, None)
        for row in reversed(tuple(self._operations.values())):
            if ((foreground and row["generation"] == base["generation"])
                    or not foreground and row["thread"] == thread
                    and (not row["persistent"] or row["generation"] == base["generation"])):
                return self._context(row)
        return base

    def current_activity(self):
        """Read worker-local context; unrelated workers see cached navigation only."""
        with self._lock:
            return self._visible(foreground=False)

    def snapshot(self):
        """Publish ordered foreground activity for the existing bridge pulses."""
        if not self.closed:
            self.poll()
        with self._lock:
            context = self._visible(foreground=True)
            key = tuple(sorted(context.items()))
            if key != self._key:
                self._key = key
                self._status = {**context, "sequence": next(self._sequences)}
            return dict(self._status)

    def _begin(self, kind, owner, *, persistent=False):
        with self._lock:
            state = owner.state if persistent else owner
            parent = None if persistent else next((row for row in reversed(tuple(self._operations.values()))
                           if row["owner"]() is state and (row["persistent"] or row["thread"] == threading.get_ident())), None)
            base = self.readiness.current_activity()
            row = dict(kind=kind, operation=next(self._ids), parent=parent["operation"] if parent else None,
                       thread=threading.get_ident(), generation=parent["generation"] if parent else base["generation"],
                       sequence=next(self._sequences), owner=weakref.ref(state), persistent=persistent,
                       screen=weakref.ref(owner) if persistent else None)
            self._operations[row["operation"]] = row
            record = {"level": "INFO", "detail": "NATIVE ACTIVITY STARTED", "component": "activity",
                      "transition": True, "activity": self._context(row)}
        self.submit_log(record)
        return row

    def _end(self, row):
        with self._lock:
            self._operations.pop(row["operation"], None)
            context = {**self._visible(foreground=True), "sequence": next(self._sequences)}
            record = {"level": "INFO", "detail": "NATIVE ACTIVITY ENDED", "component": "activity",
                      "transition": True, "operation": str(row["operation"]), "activity": context}
        self.submit_log(record)

    def _store_failure(self, error, row):
        # Keep the same exception for the game's deferred worker re-raise. Nested
        # saving must remain saving when it unwinds through week progression.
        if not hasattr(error, "launcher_activity"):
            error.launcher_activity = self._context(row)
            record = build_log_record("ERROR", "NATIVE OPERATION FAILED: " + str(error), component="activity")
            record["activity"] = dict(error.launcher_activity)
            self.submit_log(record, error=error)

    def _wrapper(self, name, binding, target, original, signature):
        mode = target.name.removeprefix("activity.")
        kind = "saving" if mode == "save" else "week_progression"
        observer = self

        def wrapped(owner, *args, **kwargs):
            if not observer.group_intact(name):
                return original(owner, *args, **kwargs)
            if mode == "finish":
                with observer._lock:
                    row = next((row for row in observer._operations.values()
                                if row["persistent"] and row["screen"]() is owner), None)
                if row is None:
                    return original(owner, *args, **kwargs)
            else:
                row = observer._begin(kind, owner, persistent=mode == "start")
            keep = False
            try:
                result = original(owner, *args, **kwargs)
                keep = mode == "start"
                return result
            except Exception as error:
                observer._store_failure(error, row)
                raise
            finally:
                # The start call creates the game's own worker. Its span ends
                # when the native finish method joins/re-raises, not at start().
                if not keep:
                    observer._end(row)

        update_wrapper(wrapped, original)
        # This is an observational decorator, not a callable-validation owner.
        # Existing regulation callers can wrap it; cleanup removes them first.
        wrapped._tpm_activity_group = name
        return wrapped

    def stop(self):
        """Restore owned methods and discard spans without retaining game objects."""
        super().stop()
        if sys.excepthook is self._unhandled:
            sys.excepthook = self._exception_hook
        with self._lock:
            self._operations.clear()
            self.writer.close()
        return self.writer.finished.is_set()
