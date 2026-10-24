# SPDX-License-Identifier: MPL-2.0
"""Identify native staff signing without gating unrelated executable contents."""

from ...callables import CallableTarget

# Inspection provenance only; runtime compatibility checks the call signature.
STAFF_INSPECTED_SHA256 = 'e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773'


def staff_target():
    return CallableTarget("staff.sign", "managers.staff_manager", ("StaffManager",),
                          "sign_staff", "(self, team_name: 'str', staff: 'Staff', salary_m: 'float', years: 'int', *, bonuses: 'Optional[Dict[str, Any]]' = None, signer_kind: 'str' = 'team', bypass_requirements: 'bool' = False, allow_current_cover: 'bool' = False, contract_start_week: 'Optional[int]' = None) -> 'bool'")


def staff_prestige_target():
    return CallableTarget("staff.prestige-check", "managers.staff_manager", ("StaffManager",),
                          "required_prestige", "(self, staff: 'Staff') -> 'int'")


def staffing_targets():
    """Common Formula entry points used by loading, rollover and role recovery."""
    return (
        CallableTarget("staffing.onboard", "managers.staff_manager", ("StaffManager",),
                       "onboard_joining_team_staff", "(self, team_name: 'str', *, contract_years: 'int' = 1) -> 'List[str]'"),
        CallableTarget("staffing.recover", "managers.staff_manager", ("StaffManager",),
                       "ensure_active_teams_have_staff", "(self, roles: 'Optional[List[str]]' = None) -> 'None'"),
    )
