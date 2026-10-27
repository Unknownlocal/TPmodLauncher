# SPDX-License-Identifier: MPL-2.0
"""Disable the native rookie cap while keeping its original test available."""

from functools import update_wrapper
import inspect

from ...manager import HookError, Manager
from ...callables import method_group
from ..regulations.native_rule_selection import NativeRuleSelection
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
        if type(enabled) is not bool and type(enabled) is not NativeRuleSelection:
            raise HookError("Rookie contract length check must be a boolean or admitted selection")
        if enabled is True:
            return None  # The default installs nothing and performs no inspection.
        try:
            return cls(state, enabled)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED drivers.rookie-check: "
                   + " ".join(str(error).split())[:512]
                   + "; rookie check switch skipped; continuing native game")
            return None

    def __init__(self, state, enabled=False):
        super().__init__(lambda record: None)
        self.state, self.manager = state, state.driver_manager
        self.enabled = enabled
        try:
            target = rookie_check_target()
            resolved = target.resolve({})
            if resolved is None or not isinstance(self.manager, resolved[0]):
                raise HookError("Rookie switch needs the loaded native driver manager")
            if type(enabled) is NativeRuleSelection:
                # UI/AI queries have no explicit contract date. Use the same
                # native start-week inference as ordinary offer scoring.
                self.start = method_group(self.manager, {"_negotiation_start_week":
                    "(self, team_name: str, driver: managers.driver.Driver, role: str = 'main') -> int"})["_negotiation_start_week"][0]
            self.approve(target)
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
        except Exception:
            self.stop()
            raise

    def _wrapper(self, name, binding, target, original, signature):
        state, manager, group, enabled = self.state, self.manager, self.groups[name], self.enabled
        start = getattr(self, "start", None)

        def wrapped(self, team_name, driver, role="main"):
            # None is the game's no-cap result. Skip only this rule; its other
            # signing restrictions still run. Removed/escaped wrappers and
            # calls for another career resume the original helper.
            if (group["state"] == "active" and self is manager
                    and state.driver_manager is manager and manager.state is state):
                if enabled is False:
                    return None
                if type(enabled) is NativeRuleSelection:
                    season = None
                    if enabled.scoped_season() is None and enabled.varies_by_season():
                        week = start(self, team_name, driver, role)
                        season = state.season_no + int(week >= state.next_season_start())
                    if not enabled.enabled(season):
                        return None
            return original(self, team_name, driver, role)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
