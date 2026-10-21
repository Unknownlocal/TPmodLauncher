# SPDX-License-Identifier: MPL-2.0
"""Check native planning helpers once; never run signing or eligibility here."""

import inspect
import sys

from ...callables import method_group
from ...manager import HookError
from .targets import driver_target


# Signing delegates date/capacity/status queries to these named helpers. Check
# their call signatures too, without pinning their bodies or private locals.
_DRIVER_GROUPS = {
    'driver': {
        'current_reserve_cover_weeks': '(self, team_name, driver, role, contract_start_week)',
        'active_contract': '(self, driver: managers.driver.Driver, week: Optional[int] = None) -> Optional[Dict[str, Any]]',
        'active_driver_count': "(self, team_name: str, role: str = 'main', week: Optional[int] = None) -> int",
        'drivers_contracted_next_season': "(self, team_name: str, role: str = 'main') -> int",
        'driver_role_contract_limit': '(self, team_name: str, role: str, *, season_no=None, week=None) -> int',
        'driver_team_next_season': '(self, driver: managers.driver.Driver) -> Optional[str]',
        'normalize_contract_status': '(self, contract: Any) -> Any',
        'driver_overall': '(self, driver: managers.driver.Driver) -> float',
        '_contract_covers_week': '(self, contract: Dict[str, Any], week: int) -> bool',
        '_set_contract_status': '(self, contract: Dict[str, Any], status: Any) -> None',
        '_next_season_main_contract_entries': '(self, team_name: str) -> List[Tuple[managers.driver.Driver, Dict[str, Any]]]',
        'normalize_ai_team_hierarchy': '(self, team_name: str) -> None',
        'ensure_main_status_consistency': '(self, team_name: str, week: int, *, apply_effects: bool = False) -> None',
        'contract_status_at_week': '(self, contract: Any, week: Optional[int] = None) -> str',
        '_schedule_contract_status_change': "(self, driver: managers.driver.Driver, contract: Dict[str, Any], new_status: Any, start_week: int, *, reason: str = 'Status changed by teammate signing', category: str = 'status_reconciliation', apply_effects: bool = True) -> bool",
        'reconcile_player_main_statuses': '(self, team_name: str, signed_driver: managers.driver.Driver, signed_contract: Dict[str, Any], start_week: int) -> None',
        '_compatible_teammate_status': '(status: Any) -> str',
    },
    'state': {
        'current_season_start_week': '(self)',
        'next_season_start': '(self)',
    },
    'globals': {
        'canonical_driver_status': '(value: Any) -> str',
        'normalize_driver_contract_status': '(contract: Any) -> Any',
        'driver_contract_capacity': "(manager, team_name, role='main', *, week=None)",
        'formula_statuses_valid': '(statuses, capacity)',
    },
    'entries': {
        '_int': '(value, default=0)',
        'is_formula': '(state)',
        'team_entry_count': '(team, season_no=None, current_season=1)',
        'entry_rule_profile': '(state, season_no=None)',
        'entry_count_for_season': '(state, team_name, season_no=None)',
        'entry_count_for_week': '(state, team_name, week)',
        'next_entry_count': '(state, team_name)',
    },
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
