# SPDX-License-Identifier: MPL-2.0
"""Switch off the native staff prestige minimum without changing other checks."""

from functools import update_wrapper

from ...manager import HookError
from ...shared.native_switch_installer import NativeSwitchInstaller
from .targets import staff_prestige_target


class StaffPrestigeSwitch(NativeSwitchInstaller):
    """Own one career's zero-minimum wrapper using the existing hook journal.

    The native helper already returns a nonnegative requirement. Zero removes
    that minimum for its UI, AI and signing consumers. No author code executes
    here; custom refusal conditions still use normal regulation validators.
    Optional queries are trusted launcher callbacks returning a boolean.
    """

    @classmethod
    def create(cls, state, *, enabled=True, submit_log):
        return super().create(state, enabled=enabled, submit_log=submit_log,
                              label='Staff prestige check', name='staff.prestige-check',
                              skipped='prestige switch')

    def __init__(self, state, enabled=False):
        super().__init__(state, enabled, 'staff_manager', staff_prestige_target,
                         'Prestige switch needs the loaded native staff manager')

    def _wrapper(self, name, binding, target, original, signature):
        state, manager, group, enabled = self.state, self.manager, self.groups[name], self.enabled

        def wrapped(self, staff):
            # Class wrappers also see other careers. Escaped wrappers after
            # removal or cleanup must resume native behaviour, even if this
            # manager remains open for other groups.
            if (group["state"] == "active" and self is manager and state.staff_manager is manager
                    and manager.state is state):
                keep = enabled if type(enabled) is bool else enabled()
                if type(keep) is not bool:
                    raise HookError("Staff prestige query must return a boolean")
                if not keep:
                    return 0
            return original(self, staff)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
