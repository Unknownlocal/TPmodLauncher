# SPDX-License-Identifier: MPL-2.0
"""Check helpers whose lazy initialization must stay private before signing."""

from ...callables import method_group
from ...manager import HookError


# Generated from inspected declarations, excluding source locations. These
# helpers define which fields a getter can fill; changed writes need inspection
# before they can safely run against the small preparation drafts.
_PREPARATION_GROUPS = {
    'personnel_negotiation_manager': (
        ('_normalize_ai_rookie_offer_duration', '_candidate_already_committed', '_prestige_eligible', 'player_contract_decision_accept_block_reason'),
        'cd7dfd5966e1371383bf5c4e69304f76a733cfebdd001ca1cf2a1993bb2f2fdf',
    ),
    'driver_negotiation': (
        # Shared caller methods invoke this only on their driver branches.
        # Keep its compatibility failure independent from staff support.
        ('driver_contract_block_reason',),
        '25334969b67ff51a1554adaa0f8b3cea0f2b1af9e74e1fb3a7985ccd8b87b821',
    ),
    'staff_manager': (
        # These staff queries borrow live data because their checked bodies do
        # not initialize fields or caches. A changed body needs fresh inspection.
        ('staff_team_next_season', 'staff_contracted_next_season', 'required_prestige', 'average_skill', '_effective_staff_skill_map', '_staff_skill_map', '_morale_skill_delta', 'morale', '_clamp_score', '_normalize_skill_profile', 'role_skill_keys'),
        'c014705e33733c8f68991981c37ad7d1aa730f1aff6f40d183c3ac862807d40d',
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


def check_preparation(state, kinds):
    for role, (names, expected) in _PREPARATION_GROUPS.items():
        if (role in ("driver_manager", "driver_negotiation") and "driver" not in kinds
                or role == "staff_manager" and "staff" not in kinds):
            continue
        _, digest = method_group(getattr(state, "personnel_negotiation_manager" if role == "driver_negotiation" else role), names)
        if digest != expected:
            raise HookError("Native personnel negotiation preparation changed: " + role)
