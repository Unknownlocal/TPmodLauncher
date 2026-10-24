# SPDX-License-Identifier: MPL-2.0
"""Disable the native rookie cap while keeping its original test available."""

from functools import update_wrapper
import inspect

from ...manager import HookError, Manager
from .targets import rookie_check_target


def native_rookie_limit(manager):
    """Retain the game's original helper for proposal applicability checks.

    Only our own switch is bypassed. This is private adapter code: mods receive
    its boolean result, never this function or the manager it reads.
    """
    target = rookie_check_target()
    original = inspect.getattr_static(manager, target.attribute)
    if getattr(original, "_tpm_hook_group", None) == target.name:
        original = original.__wrapped__
    target._check(original)
    return original


class RookieContractLengthSwitch(Manager):
    """Own one career's no-cap wrapper using the existing hook journal."""

    @classmethod
    def create(cls, state, *, enabled=True, report):
        """Keep native by default; report and skip incompatible game helpers."""
        if type(enabled) is not bool:
            raise HookError("Rookie contract length check must be a boolean")
        if enabled:
            return None  # The default installs nothing and performs no inspection.
        try:
            return cls(state)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED drivers.rookie-check: "
                   + " ".join(str(error).split())[:512]
                   + "; rookie check switch skipped; continuing native game")
            return None

    def __init__(self, state):
        super().__init__(lambda record: None)
        self.state, self.manager = state, state.driver_manager
        try:
            target = rookie_check_target()
            resolved = target.resolve({})
            if resolved is None or not isinstance(self.manager, resolved[0]):
                raise HookError("Rookie switch needs the loaded native driver manager")
            self.approve(target)
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
        except Exception:
            self.stop()
            raise

    def _wrapper(self, name, binding, target, original, signature):
        state, manager, group = self.state, self.manager, self.groups[name]

        def wrapped(self, team_name, driver, role="main"):
            # None is the game's no-cap result. Skip only this rule; its other
            # signing restrictions still run. Removed/escaped wrappers and
            # calls for another career resume the original helper.
            if (group["state"] == "active" and self is manager
                    and state.driver_manager is manager and manager.state is state):
                return None
            return original(self, team_name, driver, role)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
