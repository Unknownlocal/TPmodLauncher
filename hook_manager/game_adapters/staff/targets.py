# SPDX-License-Identifier: MPL-2.0
"""Identify native staff signing without gating unrelated executable contents."""

from ...callables import CallableTarget

# Inspection provenance only; runtime checks signature and relevant code.
STAFF_INSPECTED_SHA256 = 'e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773'


def staff_target():
    return CallableTarget("staff.sign", "managers.staff_manager", ("StaffManager",),
                          "sign_staff", "(self, team_name: 'str', staff: 'Staff', salary_m: 'float', years: 'int', *, bonuses: 'Optional[Dict[str, Any]]' = None, signer_kind: 'str' = 'team', bypass_requirements: 'bool' = False, allow_current_cover: 'bool' = False, contract_start_week: 'Optional[int]' = None) -> 'bool'",
                          ('55748b94827d0aef8e5abdecac3cda6cf80cfab96e297e93656faf37e1a79513',))
