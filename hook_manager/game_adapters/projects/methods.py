# SPDX-License-Identifier: MPL-2.0
"""Check helper call signatures and retain native functions for project planning."""

import inspect

from ...callables import method_group
from ..native_methods import native_signatures, GAME_STATE
from .native_methods import AERO, CHASSIS, ERS
from .targets import project_targets


# Check native arguments, defaults and annotations while allowing changes
# to method implementations.
_GROUPS = {
    'aero': native_signatures(AERO, (
        '_absolute_week budget_options default_budget_key _sanitize_budget_key _budget_meta '
        '_staff_capacity staff_in_use available_staff technical_staff_capacity available_technical_staff '
        '_clamp_staff _clamp_technical_staff _project_store'
    )),
    'chassis': native_signatures(CHASSIS, (
        '_absolute_week staff_capacity cfd_capacity technical_staff_capacity _upkeep_staff_factor '
        'effective_staff_capacity effective_cfd_capacity effective_technical_staff_capacity '
        'technical_staff_in_use technical_staff_available chassis_staff_in_use _project_store '
        '_available_staff _clamp_staff _clamp_technical_staff _sanitize_budget_key '
        '_default_budget_option _default_budget_options _budget_amount_for_project_type '
        '_sanitize_current_package_key _canonical_current_package_key _current_package_budget_multiplier '
        '_is_spec_b_chassis_package_key'
    )),
    'ers': native_signatures(ERS, 'is_open regulation_mode'),
    'state': native_signatures(GAME_STATE, 'get_hq_upkeep_pct'),
}


def project_methods(state):
    targets = project_targets()
    owners = {"aero": state.aero_manager, "chassis": state.development_manager,
              "ers": getattr(state, "ers_development_manager", None), "state": state}
    for target in targets:
        owner = owners["aero" if "aero" in target.name else "chassis"]
        target._check(inspect.getattr_static(owner, target.attribute))
    result = {}
    for role, owner in owners.items():
        if role == "ers" and owner is None:
            continue  # The native capacity helper treats missing ERS as open.
        result[role] = method_group(owner, _GROUPS[role])
    return result
