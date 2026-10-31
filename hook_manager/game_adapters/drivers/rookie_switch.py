# SPDX-License-Identifier: MPL-2.0
"""Switch the native rookie cap while keeping its original test available."""

from functools import update_wrapper
import inspect

from ...manager import HookError
from ...shared.native_switch_installer import NativeSwitchInstaller
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


class RookieContractLengthSwitch(NativeSwitchInstaller):
    """Own a career's cap switch; trusted callers choose when it applies."""

    @classmethod
    def create(cls, state, *, enabled=True, submit_log):
        return super().create(state, enabled=enabled, submit_log=submit_log,
                              label='Rookie contract length check', name='drivers.rookie-check',
                              skipped='rookie check switch')

    def __init__(self, state, enabled=False):
        super().__init__(state, enabled, 'driver_manager', rookie_check_target,
                         'Rookie switch needs the loaded native driver manager')

    def _wrapper(self, name, binding, target, original, signature):
        state, manager, group, enabled = self.state, self.manager, self.groups[name], self.enabled

        def wrapped(self, team_name, driver, role="main"):
            # None is the game's no-cap result. Skip only this rule; its other
            # signing restrictions still run. Removed/escaped wrappers and
            # calls for another career resume the original helper.
            if (group["state"] == "active" and self is manager
                    and state.driver_manager is manager and manager.state is state):
                keep = enabled if type(enabled) is bool else enabled(team_name, driver, role)
                if type(keep) is not bool:
                    raise HookError("Rookie check query must return a boolean")
                if not keep:
                    return None
            return original(self, team_name, driver, role)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
