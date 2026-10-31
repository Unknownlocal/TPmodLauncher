# SPDX-License-Identifier: MPL-2.0
"""Fixed native method definitions used by academy integrations."""

from ..native_methods import define_methods


ACADEMY = define_methods('managers.academy_manager', 'AcademyManager', {
    '_seasons_until_age_cap': '(self, driver) -> int',
    'signing_cost_m': '(self, driver) -> float',
    'contract_for': '(self, driver_name: str) -> Optional[Dict]',
    'ensure_team_state': '(self, team: Dict) -> Dict',
    'max_slots': '(self, team_or_name: Union[str, Dict]) -> int',
    'is_eligible': '(self, driver) -> bool',
    '_has_main_series_history': '(self, driver) -> bool',
    'academy_owner': '(self, driver_name: str) -> Optional[str]',
    '_ai_candidate_pool': '(self) -> List',
    '_ai_meets_thresholds': '(self, team: Dict) -> bool',
    '_team_prestige_value': '(self, team: Dict) -> float',
    'max_renew_years': '(self, driver_name: str, season: int) -> int',
    'seasons_remaining': '(self, contract: Optional[Dict], season: int) -> int',
    'sign_driver': '(self, team_name: str, driver_name: str, seasons: int = 1) -> Tuple[bool, str]',
    'renew_contract': '(self, team_name: str, driver_name: str, extra_seasons: int) -> Tuple[bool, str]',
    'ai_sign_drivers': '(self, season: int) -> None',
    'ai_renew_contracts': '(self, current_season: int) -> None',
})


ACADEMY_SCREEN = define_methods('ui.screens.academy_screen', 'AcademyScreen', {
    'handle_event': '(self, event)',
    'draw': '(self, surface)',
})
