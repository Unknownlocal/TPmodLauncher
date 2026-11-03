# SPDX-License-Identifier: MPL-2.0
"""Observe the two native references that request an application restart."""

from functools import update_wrapper
from itertools import count
from inspect import formatannotation
from typing import Optional
import atexit

if __package__:
    from .manager import Manager
    from .callables import CallableTarget
    from .diagnostics import build_log_record


class NativeRestart(Manager):
    """Queue restart attempts without waiting for storage or launcher decisions.

    Both screens import the same helper by value. Observe their references so
    already-imported screens are covered too; ordinary quit paths are untouched.
    """

    TARGETS = (("settings", "ui.screens.options_screen"),
               ("mod-pack", "ui.screens.season_pack_screen"))

    def __init__(self, events):
        self.notifications = events
        self._attempts = count(1)
        super().__init__(lambda record: None)
        for source, module in self.TARGETS:
            target = CallableTarget("restart." + source, module, (), "relaunch_application",
                                    f"(*, series_mode: {formatannotation(Optional[str])} = None) -> None")
            self.approve(target)
            try:
                self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            except Exception:
                pass  # Compatibility errors are retained; native restart remains available.
        # Registered after JournalWriter, so this memory-only check runs before
        # its existing bounded interpreter-exit drain (atexit runs in reverse).
        atexit.register(self.finish_on_exit)

    def finish_on_exit(self):
        """Queue final coverage; only a completed reporting stream can certify it."""
        if self.closed:
            return
        status = self.status()
        status["groups"] = {name: state if state != "active" or self.group_intact(name) else "failed"
                            for name, state in status["groups"].items()}
        try:
            self.notifications.publish("restart-observation", {**status, "final": True})
        except Exception:
            pass  # Without this checkpoint, the launcher retains its grace.

    def stop(self):
        """Restore references and remove the no-longer-owned exit checkpoint."""
        atexit.unregister(self.finish_on_exit)
        return super().stop()

    def store_error(self, error):
        """Retain compatibility history and enqueue its complete warning."""
        self.errors.append(str(error)[:4096])
        del self.errors[:-16]
        self.error_record = build_log_record("WARNING", "Restart observation unavailable: " + str(error),
                                             component="lifecycle")
        self.notifications.host.activity.submit_log({**self.error_record, "event": "diagnostic"}, error=error)

    def _publish(self, status):
        # Reporting failures must never replace a native return or exception.
        try:
            self.notifications.publish("restart", status)
        except Exception as error:
            try:
                self.store_error(error)
            except Exception:
                pass  # Even diagnostic failure must preserve the native outcome.

    def _wrapper(self, name, binding, target, original, signature):
        source = target.name.removeprefix("restart.")
        def wrapped(*args, **kwargs):
            if not self.group_intact(name):
                return original(*args, **kwargs)
            series = kwargs.get("series_mode")
            # Never inspect or stringify arbitrary game objects for a marker.
            series = series if type(series) is str and series in ("formula", "oval") else None
            status = {"attempt": next(self._attempts), "source": source, "series": series}
            self._publish({**status, "state": "requested"})
            try:
                result = original(*args, **kwargs)
            except SystemExit as exit:
                # The inspected helper raises SystemExit only after Popen succeeds.
                state = "committed" if exit.code is None or type(exit.code) is int and exit.code == 0 else "cancelled"
                self._publish({**status, "state": state})
                raise
            except BaseException:
                self._publish({**status, "state": "cancelled"})
                raise
            else:
                # A normal return means teardown did not take the inspected path.
                self._publish({**status, "state": "cancelled"})
                return result
        return update_wrapper(wrapped, original)
