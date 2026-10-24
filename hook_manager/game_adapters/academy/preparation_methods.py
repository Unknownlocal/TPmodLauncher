# SPDX-License-Identifier: MPL-2.0
"""Check the call signatures used by private academy AI preparation."""

from ...callables import method_group
from ...manager import HookError
import sys


_PREPARATION_METHODS = {
    # These native queries run on the private AI view. Check their public call
    # shapes once; changed implementations with matching signatures stay supported.
    'academy': {
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
    },
    'driver': {
        'active_contract': '(self, driver: managers.driver.Driver, week: Optional[int] = None) -> Optional[Dict[str, Any]]',
        'normalize_contract_status': '(self, contract: Any) -> Any',
        'is_structurally_valid_contract': '(contract: Any) -> bool',
        'driver_overall': '(self, driver: managers.driver.Driver) -> float',
        'get_season_history': '(self, name: str, *, include_poles: bool = True) -> List[Dict[str, Any]]',
    },
    'ui': {
        'fit_text': "(text, font, max_width, *, suffix='...')",
    },
}


def preparation_methods(state, screen):
    for name in ("managers.driver", "ui.base_screen"):
        module = sys.modules.get(name)
        if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
            raise HookError("Academy preparation needs completed native imports: " + name)
    return {role: method_group(owner, _PREPARATION_METHODS[role])
            for role, owner in (("academy", state.academy_manager),
                                ("driver", state.driver_manager), ("ui", screen))}
