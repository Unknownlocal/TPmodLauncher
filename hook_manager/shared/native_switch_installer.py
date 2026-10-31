# SPDX-License-Identifier: MPL-2.0
"""Shared installation and rollback for fixed native validation switches."""

from ..diagnostics import build_log_record
from ..manager import HookError, Manager


class NativeSwitchInstaller(Manager):
    """Own switch setup while subclasses supply their specific native wrapper."""

    @classmethod
    def create(cls, state, *, enabled, submit_log, label, name, skipped):
        """Keep native defaults; warn and skip a switch when setup is incompatible."""
        if type(enabled) is not bool and not callable(enabled):
            raise HookError(label + " needs a boolean or trusted query")
        if enabled is True:
            return None  # Native defaults need no target lookup or wrapper.
        try:
            return cls(state, enabled)
        except Exception as error:
            submit_log(build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED " + name + ": "
                       + str(error) + "; " + skipped + " skipped; continuing native game",
                       error=error, component="regulations"))
            return None

    def __init__(self, state, enabled, manager_attribute, target_factory, missing_message):
        """Install one fixed wrapper against the career's mapped native manager."""
        super().__init__(lambda record: None)
        self.state, self.manager = state, getattr(state, manager_attribute)
        self.enabled = enabled
        try:
            target = target_factory()
            resolved = target.resolve({})
            if resolved is None or not isinstance(self.manager, resolved[0]):
                raise HookError(missing_message)
            self.approve(target)
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
        except Exception:
            # Manager's journal restores even an assignment that wrote before raising.
            self.stop()
            raise
