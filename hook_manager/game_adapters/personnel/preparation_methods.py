# SPDX-License-Identifier: MPL-2.0
"""Check helpers whose lazy initialization must stay private before signing."""

from ...callables import method_group
from .market_preparation import MarketPreparation


# Check both driver and staff preparation. Runtime compatibility uses signatures;
# native behaviour is checked separately in developer conformance work.
_PREPARATION_GROUPS = {
    'personnel_negotiation_manager': {
        '_normalize_ai_rookie_offer_duration': "(self, row: 'Dict[str, Any]', driver: 'Any' = None) -> 'None'",
        '_candidate_already_committed': "(self, candidate_type: 'str', candidate: 'Any') -> 'bool'",
        '_prestige_eligible': "(self, row: 'Dict[str, Any]', candidate: 'Any') -> 'bool'",
        'player_contract_decision_accept_block_reason': "(self, decision_id: 'str', team_name: 'Optional[str]' = None) -> 'Optional[str]'",
    },
    'driver_negotiation': {
        'driver_contract_block_reason': "(self, team_name: 'str', driver: 'Any', role: 'str', priority: 'str', contract_start_week: 'int', *, entry_id: 'Optional[str]' = None, years: 'int' = 1, check_seat: 'bool' = True, check_prestige: 'bool' = True) -> 'Optional[str]'",
    },
    'staff_manager': {
        'staff_team_next_season': "(self, staff: 'Staff') -> 'Optional[str]'",
        'staff_contracted_next_season': "(self, team_name: 'str', role: 'str') -> 'bool'",
        'required_prestige': "(self, staff: 'Staff') -> 'int'",
        'average_skill': "(self, staff: 'Staff') -> 'float'",
        '_effective_staff_skill_map': "(self, staff: 'Staff') -> 'Dict[str, int]'",
        '_staff_skill_map': "(self, staff: 'Staff') -> 'Dict[str, int]'",
        '_morale_skill_delta': "(self, staff: 'Staff') -> 'float'",
        'morale': "(self, staff: 'Staff') -> 'float'",
        '_clamp_score': "(value: 'Any', default: 'float' = 50.0) -> 'float'",
        '_normalize_skill_profile': "(self, role: 'str', raw_skills: 'Any', fallback_skill: 'int') -> 'Dict[str, int]'",
        'role_skill_keys': "(role: 'str') -> 'List[str]'",
    },
    'driver_manager': {
        'refresh_driver_career_stage': '(self, driver: managers.driver.Driver, *, allow_regression: bool = False) -> str',
        'evaluate_career_stage': '(self, driver: managers.driver.Driver) -> str',
        '_driver_career_totals': '(self, driver: managers.driver.Driver) -> Dict[str, int]',
        '_career_requirement_met': '(self, requirement: str, totals: Dict[str, int]) -> bool',
        'get_season_history': '(self, name: str, *, include_poles: bool = True) -> List[Dict[str, Any]]',
        '_main_series_poles_by_season': '(self, driver_name: str) -> Dict[int, int]',
    },
    'prestige_manager': {
        'ensure_team_state': '(self, team: Dict[str, Any]) -> managers.prestige_manager.TeamPrestigeState',
        '_write_team_state': '(self, team: Dict[str, Any], state: managers.prestige_manager.TeamPrestigeState) -> None',
    },
}


def check_preparation(state):
    MarketPreparation.check(state)
    for role, signatures in _PREPARATION_GROUPS.items():
        method_group(getattr(state, "personnel_negotiation_manager" if role == "driver_negotiation" else role), signatures)
