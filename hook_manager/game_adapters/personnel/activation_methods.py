# SPDX-License-Identifier: MPL-2.0
"""Check the native methods used to describe future employment activation."""

import sys

from ...callables import method_group
from ...manager import HookError
from ..drivers.methods import _DRIVER_GROUPS
from ..staff.methods import _STAFF_GROUPS


def activation_methods(state, kinds):
    """Retain activation helpers by call signature after native imports finish."""
    modules = {"state": "game_state", **{kind: "managers." + ("driver" if kind == "driver" else "staff_manager")
                                        for kind in kinds}}
    # Do not start game imports here: constructing a planner must not re-enter a
    # partially initialized gameplay module. Only requested personnel kinds count.
    for name in modules.values():
        module = sys.modules.get(name)
        if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
            raise HookError("Activation planning needs completed native imports: " + name)
    result = {"state": method_group(state, {
        "current_season_start_week": "(self)", "next_season_start": "(self)",
        **{"_activate_future_" + kind + "_contracts": "(self, current_week: int) -> None" for kind in kinds},
    })}
    if any(getattr(function, "_tpm_hook_group", None) is not None for function, _ in result["state"].values()):
        # Retain the original bodies before wrappers can route preparation back
        # into interception. Compatibility uses signatures, not method-body hashes.
        raise HookError("Activation planning requires native methods before interception")
    if "driver" in kinds:
        result["driver"] = method_group(state.driver_manager, {
            "normalize_contract_status": _DRIVER_GROUPS["driver"]["normalize_contract_status"],
        })
        # The manager normalizer delegates to these module helpers; their call
        # signatures belong to the same compatibility boundary.
        method_group(sys.modules[modules["driver"]], {
            name: _DRIVER_GROUPS["globals"][name]
            for name in ("canonical_driver_status", "normalize_driver_contract_status")
        })
    if "staff" in kinds:
        result["staff"] = method_group(state.staff_manager, {
            name: _STAFF_GROUPS["staff"][name] for name in ("normalize_role", "_supplier_by_id")
        })
    return result
