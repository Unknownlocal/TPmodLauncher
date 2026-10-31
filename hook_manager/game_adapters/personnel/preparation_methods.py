# SPDX-License-Identifier: MPL-2.0
"""Check native helpers before preparing private signing data."""

from ...callables import method_group
from ..native_methods import native_signatures
from .native_methods import PERSONNEL, PRESTIGE
from ..staff.native_methods import STAFF
from ..drivers.native_methods import DRIVER
from .market_preparation import MarketPreparation


_PREPARATION_GROUPS = {
    'personnel_negotiation_manager': native_signatures(PERSONNEL, (
        '_normalize_ai_rookie_offer_duration _candidate_already_committed _prestige_eligible '
        'player_contract_decision_accept_block_reason'
    )),
    'driver_negotiation': native_signatures(PERSONNEL, 'driver_contract_block_reason'),
    'staff_manager': native_signatures(STAFF, (
        'staff_team_next_season staff_contracted_next_season required_prestige average_skill '
        '_effective_staff_skill_map _staff_skill_map _morale_skill_delta morale _clamp_score '
        '_normalize_skill_profile role_skill_keys'
    )),
    'driver_manager': native_signatures(DRIVER, (
        'refresh_driver_career_stage evaluate_career_stage _driver_career_totals _career_requirement_met '
        'get_season_history _main_series_poles_by_season'
    )),
    'prestige_manager': native_signatures(PRESTIGE, 'ensure_team_state _write_team_state'),
}


def check_preparation(state):
    MarketPreparation.check(state)
    for role, signatures in _PREPARATION_GROUPS.items():
        method_group(getattr(state, "personnel_negotiation_manager" if role == "driver_negotiation" else role), signatures)
