# SPDX-License-Identifier: MPL-2.0
"""Fixed native method definitions used by drivers integrations."""

from ..native_methods import define_methods


DRIVER = define_methods('managers.driver', 'DriverManager', {
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
    'refresh_driver_career_stage': '(self, driver: managers.driver.Driver, *, allow_regression: bool = False) -> str',
    'evaluate_career_stage': '(self, driver: managers.driver.Driver) -> str',
    '_driver_career_totals': '(self, driver: managers.driver.Driver) -> Dict[str, int]',
    '_career_requirement_met': '(self, requirement: str, totals: Dict[str, int]) -> bool',
    'get_season_history': '(self, name: str, *, include_poles: bool = True) -> List[Dict[str, Any]]',
    '_main_series_poles_by_season': '(self, driver_name: str) -> Dict[int, int]',
    'is_structurally_valid_contract': '(contract: Any) -> bool',
    'sign_driver': "(self, team_name: str, driver: managers.driver.Driver, salary_m: float, years: int, cost_points: int = 0, role: str = 'main', priority: str = 'equal', bonuses: Optional[Dict[str, float]] = None, entry_id: Optional[str] = None, contract_start_week: Optional[int] = None) -> bool",
    'formula_rookie_contract_year_limit': "(self, team_name: str, driver: managers.driver.Driver, role: str = 'main') -> Optional[int]",
    'main_contract_entries_for_week': '(self, team_name: str, week: int, *, exclude_driver: Optional[managers.driver.Driver] = None) -> List[Tuple[managers.driver.Driver, Dict[str, Any]]]',
    'driver_personalities': '(self, driver: managers.driver.Driver) -> List[str]',
    'career_stage_modifiers': '(self, driver: managers.driver.Driver) -> Dict[str, Any]',
})


DRIVER_MODULE = define_methods('managers.driver', None, {
    'canonical_driver_status': '(value: Any) -> str',
    'normalize_driver_contract_status': '(contract: Any) -> Any',
    'driver_contract_capacity': "(manager, team_name, role='main', *, week=None)",
    'formula_statuses_valid': '(statuses, capacity)',
})


FORMULA_ENTRIES = define_methods('managers.formula_entries', None, {
    '_int': '(value, default=0)',
    'is_formula': '(state)',
    'team_entry_count': '(team, season_no=None, current_season=1)',
    'entry_rule_profile': '(state, season_no=None)',
    'entry_count_for_season': '(state, team_name, season_no=None)',
    'entry_count_for_week': '(state, team_name, week)',
    'next_entry_count': '(state, team_name)',
})
