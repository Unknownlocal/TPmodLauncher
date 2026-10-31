# SPDX-License-Identifier: MPL-2.0
"""Check the call signatures used by private academy AI preparation."""

from ...callables import method_group
from ..native_methods import native_signatures, BASE_SCREEN
from .native_methods import ACADEMY
from ..drivers.native_methods import DRIVER
from ...manager import HookError
import sys


_PREPARATION_METHODS = {
    'academy': native_signatures(ACADEMY, (
        'ensure_team_state max_slots is_eligible _has_main_series_history academy_owner '
        '_ai_candidate_pool _ai_meets_thresholds _team_prestige_value max_renew_years seasons_remaining'
    )),
    'driver': native_signatures(DRIVER, (
        'active_contract normalize_contract_status is_structurally_valid_contract driver_overall '
        'get_season_history'
    )),
    'ui': native_signatures(BASE_SCREEN, 'fit_text'),
}


def preparation_methods(state, screen):
    for name in ("managers.driver", "ui.base_screen"):
        module = sys.modules.get(name)
        if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
            raise HookError("Academy preparation needs completed native imports: " + name)
    return {role: method_group(owner, _PREPARATION_METHODS[role])
            for role, owner in (("academy", state.academy_manager),
                                ("driver", state.driver_manager), ("ui", screen))}
