# SPDX-License-Identifier: MPL-2.0
"""Switch off the native staff prestige minimum without changing other checks."""

from functools import update_wrapper

from ...manager import HookError, Manager
from .targets import staff_prestige_target


class StaffPrestigeSwitch(Manager):
    """Own one career's zero-minimum wrapper using the existing hook journal.

    The native helper already returns a nonnegative requirement. Zero removes
    that minimum for its UI, AI and signing consumers. No author code executes
    here; custom refusal conditions still use normal regulation validators.
    """

    @classmethod
    def create(cls, state, *, enabled=True, report):
        """Keep native behaviour by default; report and skip incompatible helpers."""
        if type(enabled) is not bool:
            raise HookError("Staff prestige check must be a boolean")
        if enabled:
            return None  # Native is the default; no wrapper or inspection needed.
        try:
            return cls(state)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED staff.prestige-check: "
                   + " ".join(str(error).split())[:512]
                   + "; prestige switch skipped; continuing native game")
            return None

    def __init__(self, state):
        super().__init__(lambda record: None)
        self.state, self.manager = state, state.staff_manager
        try:
            target = staff_prestige_target()
            resolved = target.resolve({})
            if resolved is None or not isinstance(self.manager, resolved[0]):
                raise HookError("Prestige switch needs the loaded native staff manager")
            self.approve(target)
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
        except Exception:
            self.stop()
            raise

    def _wrapper(self, name, binding, target, original, signature):
        state, manager, group = self.state, self.manager, self.groups[name]

        def wrapped(self, staff):
            # Class wrappers also see other careers. Escaped wrappers after
            # removal or cleanup must resume native behaviour, even if this
            # manager remains open for other groups.
            if (group["state"] == "active" and self is manager and state.staff_manager is manager
                    and manager.state is state):
                return 0
            return original(self, staff)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
