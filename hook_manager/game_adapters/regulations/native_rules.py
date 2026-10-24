# SPDX-License-Identifier: MPL-2.0
"""Install the two fixed native check switches from admitted booleans."""

from ...manager import HookError
from ..drivers.rookie_check import RookieContractLengthSwitch
from ..staff.prestige_switch import StaffPrestigeSwitch


def install_native_rules(state, values, *, report):
    """Return owned hooks for trusted setup to stop on policy/career removal.

    No file paths, author functions or game-method names are accepted. This
    helper alone does not declare a policy ready or enable launcher admission.
    """
    if (type(values) is not dict or values.keys() - {"rookie_contract_length_check", "staff_prestige_check"}
            or any(type(value) is not bool for value in values.values())):
        raise HookError("Expected native regulation check booleans")
    # Validate the whole record before touching either class. Missing or true
    # installs nothing; false disables that one native restriction.
    hooks = []
    try:
        for name, factory in (("rookie_contract_length_check", RookieContractLengthSwitch),
                              ("staff_prestige_check", StaffPrestigeSwitch)):
            hook = factory.create(state, enabled=values.get(name, True), report=report)
            if hook is not None:
                hooks.append(hook)
        return tuple(hooks)
    except BaseException:
        for hook in reversed(hooks):
            hook.stop()
        raise
