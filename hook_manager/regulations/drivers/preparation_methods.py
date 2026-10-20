# SPDX-License-Identifier: MPL-2.0
"""Check helpers whose lazy initialization must stay private before signing."""

from ...callables import method_group
from ...manager import HookError


# Generated from inspected declarations, excluding source locations. These
# helpers define which fields a getter can fill; changed writes need inspection
# before they can safely run against the small preparation drafts.
_PREPARATION_GROUPS = {
    'personnel_negotiation_manager': (
        ('_normalize_ai_rookie_offer_duration', 'driver_contract_block_reason', '_candidate_already_committed', '_prestige_eligible', 'player_contract_decision_accept_block_reason'),
        'd84d9cbc3921b0bbcb9da0466a5c5f93a91e7cdb0bba1c0ba89ad90389d97aca',
    ),
    'driver_manager': (
        ('refresh_driver_career_stage', 'evaluate_career_stage', '_driver_career_totals', '_career_requirement_met', 'get_season_history', '_main_series_poles_by_season'),
        '46d5cef059c530b08bdcdf62aa28927b3472008a76444ba759a66cdd1a779d2b',
    ),
    'prestige_manager': (
        ('ensure_team_state', '_write_team_state'),
        '61bc8f9e2f416310eb87051939923491cd82b65b2740625b0fe8e53a144794db',
    ),
}


def check_preparation(state):
    for role, (names, expected) in _PREPARATION_GROUPS.items():
        _, digest = method_group(getattr(state, role), names)
        if digest != expected:
            raise HookError("Native driver negotiation preparation changed: " + role)
