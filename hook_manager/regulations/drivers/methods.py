# SPDX-License-Identifier: MPL-2.0
"""Check native planning helpers once; never run signing or eligibility here."""

import inspect
import sys

from ...callables import method_group
from ...manager import HookError
from .targets import driver_target


# Generated from the inspected native declarations. Signing delegates date,
# capacity and status calculations to these helpers; checking only sign_driver
# would miss a change in how the final promised terms are calculated.
_DRIVER_GROUPS = {
    'driver': ((
        'current_reserve_cover_weeks',
        'active_contract',
        'active_driver_count',
        'drivers_contracted_next_season',
        'driver_role_contract_limit',
        'driver_team_next_season',
        'normalize_contract_status',
        'driver_overall',
        '_contract_covers_week',
        '_set_contract_status',
        '_next_season_main_contract_entries',
        'normalize_ai_team_hierarchy',
        'ensure_main_status_consistency',
        'contract_status_at_week',
        '_schedule_contract_status_change',
        'reconcile_player_main_statuses',
        '_compatible_teammate_status',
    ), 'b7757e8de56f75fb170e6428b2e8286e65823f9158824f6ef44857800aec090b'),
    'state': ((
        'current_season_start_week',
        'next_season_start',
    ), '126d22c1769b2d17e56e7bd6efaffca841a3e746496383e5e0e1a2ddb1eb2874'),
    'globals': ((
        'canonical_driver_status',
        'normalize_driver_contract_status',
        'driver_contract_capacity',
        'formula_statuses_valid',
    ), '44a8bc6c72f63b1fa046c40e3e8b80a985cc5dbd5610c3392971b9dce695bf6d'),
    'entries': ((
        '_int',
        'is_formula',
        'team_entry_count',
        'entry_rule_profile',
        'entry_count_for_season',
        'entry_count_for_week',
        'next_entry_count',
    ), '9e975ff08a45fb471ecd2e2aeb7f6681cc67aa1e68ef20208cb79496edb1cf08'),
}


def driver_methods(state):
    manager = state.driver_manager
    target = driver_target()
    function = inspect.getattr_static(manager, target.attribute)
    target._check(function)
    if str(inspect.signature(function)) != target.signature:
        raise HookError("Driver signing signature changed")
    # Reuse completed game imports. Planning must not load gameplay modules just
    # to probe compatibility or observe a module halfway through initialization.
    owners = {"driver": manager, "state": state,
              "globals": sys.modules.get("managers.driver"), "entries": sys.modules.get("managers.formula_entries")}
    result = {}
    for role, owner in owners.items():
        if owner is None or getattr(getattr(owner, "__spec__", None), "_initializing", False):
            raise HookError("Driver planning needs completed native imports: " + role)
        names, expected = _DRIVER_GROUPS[role]
        methods, digest = method_group(owner, names)
        if digest != expected:
            raise HookError("Native driver planning helpers changed: " + role)
        result[role] = methods
    return result
