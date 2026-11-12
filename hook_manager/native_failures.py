# SPDX-License-Identifier: MPL-2.0
"""Observe the game's existing crash recorder without replacing its behaviour."""

from functools import update_wrapper

if __package__:
    from .manager import Manager
    from .callables import CallableTarget


class NativeFailures(Manager):
    """Keep native crash recording and submit its original error once."""

    def __init__(self, activity):
        super().__init__(lambda record: None)
        self.activity = activity
        target = CallableTarget("failure.native", "runtime_crash_reporter", ("RuntimeCrashReporter",),
                                "record_exception", "(self, exc_info) -> 'None'")
        self.approve(target)
        try:
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
        except Exception:
            pass  # The generic Python hooks still report if this game API changes.

    def _wrapper(self, name, binding, target, original, signature):
        observer = self
        def record_exception(owner, exc_info):
            if not observer.closed and observer.group_intact(name):
                try:
                    observer.activity.store_exception(exc_info[1], fatal=True)
                except Exception:
                    pass  # Keep the original crash file, error and re-raise path.
            return original(owner, exc_info)
        update_wrapper(record_exception, original)
        record_exception._tpm_activity_group = name
        return record_exception
