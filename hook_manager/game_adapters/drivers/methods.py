# SPDX-License-Identifier: MPL-2.0
"""Check native planning helpers once; never run signing or eligibility here."""

import inspect
import sys

from ...callables import method_group
from ..native_methods import native_signatures, GAME_STATE
from .native_methods import DRIVER, DRIVER_MODULE, FORMULA_ENTRIES
from ...manager import HookError
from .targets import driver_target


# Signing delegates date/capacity/status queries to these named helpers. Check
# their call signatures too, without pinning their bodies or private locals.
_DRIVER_GROUPS = {
    'driver': native_signatures(DRIVER, (
        'current_reserve_cover_weeks active_contract active_driver_count drivers_contracted_next_season '
        'driver_role_contract_limit driver_team_next_season normalize_contract_status driver_overall '
        '_contract_covers_week _set_contract_status _next_season_main_contract_entries '
        'normalize_ai_team_hierarchy ensure_main_status_consistency contract_status_at_week '
        '_schedule_contract_status_change reconcile_player_main_statuses _compatible_teammate_status'
    )),
    'state': native_signatures(GAME_STATE, 'current_season_start_week next_season_start'),
    'globals': native_signatures(DRIVER_MODULE, (
        'canonical_driver_status normalize_driver_contract_status driver_contract_capacity '
        'formula_statuses_valid'
    )),
    'entries': native_signatures(FORMULA_ENTRIES, (
        '_int is_formula team_entry_count entry_rule_profile entry_count_for_season entry_count_for_week '
        'next_entry_count'
    )),
}


def driver_methods(state):
    manager = state.driver_manager
    target = driver_target()
    function = inspect.getattr_static(manager, target.attribute)
    target._check(function)
    # Reuse completed game imports. Planning must not load gameplay modules just
    # to probe compatibility or observe a module halfway through initialization.
    owners = {"driver": manager, "state": state,
              "globals": sys.modules.get("managers.driver"), "entries": sys.modules.get("managers.formula_entries")}
    result = {}
    for role, owner in owners.items():
        if owner is None or getattr(getattr(owner, "__spec__", None), "_initializing", False):
            raise HookError("Driver planning needs completed native imports: " + role)
        result[role] = method_group(owner, _DRIVER_GROUPS[role])
    return result
