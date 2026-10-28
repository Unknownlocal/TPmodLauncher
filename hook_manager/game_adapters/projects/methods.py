# SPDX-License-Identifier: MPL-2.0
"""Check helper call signatures and retain native functions for project planning."""

import inspect

from ...callables import method_group
from .targets import project_targets


# Check native arguments, defaults and annotations while allowing changes
# to method implementations.
_GROUPS = {
    'aero': {
        '_absolute_week': "(self) -> 'int'",
        'budget_options': "(self, project_type: 'str' = 'development') -> 'Dict[str, Dict[str, float]]'",
        'default_budget_key': "(self) -> 'str'",
        '_sanitize_budget_key': "(self, raw_key: 'Optional[str]', project_type: 'str' = 'development', legacy_weekly: 'Optional[float]' = None) -> 'str'",
        '_budget_meta': "(self, budget_key: 'Optional[str]', project_type: 'str' = 'development') -> 'Dict[str, float]'",
        '_staff_capacity': "(self, team: 'Dict') -> 'int'",
        'staff_in_use': "(self, team: 'Dict', exclude_part: 'Optional[str]' = None) -> 'int'",
        'available_staff': "(self, team: 'Dict', part_key: 'Optional[str]' = None) -> 'int'",
        'technical_staff_capacity': "(self, team: 'Dict') -> 'int'",
        'available_technical_staff': "(self, team: 'Dict', part_key: 'Optional[str]' = None) -> 'int'",
        '_clamp_staff': "(self, team: 'Dict', staff: 'int', part_key: 'Optional[str]' = None) -> 'int'",
        '_clamp_technical_staff': "(self, team: 'Dict', staff: 'int', part_key: 'Optional[str]' = None) -> 'int'",
        '_project_store': "(self, team: 'Dict') -> 'Dict[str, Optional[Dict]]'",
    },
    'chassis': {
        '_absolute_week': '(self) -> int',
        'staff_capacity': '(self, team: Dict) -> int',
        'cfd_capacity': '(self, team: Dict) -> int',
        'technical_staff_capacity': '(self, team: Dict) -> int',
        '_upkeep_staff_factor': '(self, team: Dict, building: str) -> float',
        'effective_staff_capacity': '(self, team: Dict) -> int',
        'effective_cfd_capacity': '(self, team: Dict) -> int',
        'effective_technical_staff_capacity': '(self, team: Dict) -> int',
        'technical_staff_in_use': '(self, team: Dict, *, exclude_group: Optional[str] = None, exclude_key: Optional[str] = None) -> int',
        'technical_staff_available': '(self, team: Dict, *, exclude_group: Optional[str] = None, exclude_key: Optional[str] = None) -> int',
        'chassis_staff_in_use': '(self, team: Dict, exclude: Optional[str] = None) -> int',
        '_project_store': '(self, team: Dict) -> Dict[str, Optional[Dict]]',
        '_available_staff': '(self, team: Dict, project_type: str) -> int',
        '_clamp_staff': '(self, team: Dict, project_type: str, staff: int) -> int',
        '_clamp_technical_staff': '(self, team: Dict, project_type: str, staff: int) -> int',
        '_sanitize_budget_key': '(self, raw_key) -> str',
        '_default_budget_option': '(self) -> Dict',
        '_default_budget_options': '(self) -> Dict[str, Dict]',
        '_budget_amount_for_project_type': '(meta: Dict, project_type: str) -> float',
        '_sanitize_current_package_key': '(self, raw_key) -> str',
        '_canonical_current_package_key': '(self, raw_key) -> str',
        '_current_package_budget_multiplier': '(self, project: Dict, package_key: Optional[str] = None) -> float',
        '_is_spec_b_chassis_package_key': '(self, package_key: object) -> bool',
    },
    'ers': {
        'is_open': "(self) -> 'bool'",
        'regulation_mode': "(self) -> 'str'",
    },
    'state': {
        'get_hq_upkeep_pct': '(self, team: Dict[str, Any], building: str, default: float = 1.0) -> float',
    },
}


def project_methods(state):
    targets = project_targets()
    owners = {"aero": state.aero_manager, "chassis": state.development_manager,
              "ers": getattr(state, "ers_development_manager", None), "state": state}
    for target in targets:
        owner = owners["aero" if "aero" in target.name else "chassis"]
        target._check(inspect.getattr_static(owner, target.attribute))
    result = {}
    for role, owner in owners.items():
        if role == "ers" and owner is None:
            continue  # The native capacity helper treats missing ERS as open.
        result[role] = method_group(owner, _GROUPS[role])
    return result
