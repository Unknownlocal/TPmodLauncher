# SPDX-License-Identifier: MPL-2.0
"""Fixed native method definitions used by projects integrations."""

from ..native_methods import define_methods


AERO = define_methods('managers.aero_manager', 'AeroDevelopmentManager', {
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
    'start_project': "(self, team_name: 'str', part_key: 'str', weekly_spend: 'float' = 0.0, staff: 'int' = 0, technical_staff: 'int' = 0, budget_key: 'Optional[str]' = None) -> 'bool'",
    'start_spec_upgrade': "(self, team_name: 'str', part_key: 'str', weekly_spend: 'float' = 0.0, staff: 'int' = 0, technical_staff: 'int' = 0, budget_key: 'Optional[str]' = None) -> 'bool'",
    '_ai_try_launch_project': "(self, team_name: 'str', team: 'Dict', part_key: 'str', ai_state: 'Dict', bracket: 'str') -> 'Tuple[bool, float]'",
    '_part_record': "(self, team: 'Dict', part_key: 'str') -> 'Dict'",
})


CHASSIS = define_methods('managers.development_manager', 'DevelopmentManager', {
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
    'start_project': '(self, team_name: str, project_type: str, weekly_spend: Optional[float] = None, budget_key: Optional[str] = None, staff: Optional[int] = None, technical_staff: Optional[int] = None, concept: Optional[str] = None, car_concept: Optional[str] = None, car_focus: Optional[str] = None, package_key: Optional[str] = None, allow_ai: bool = False) -> bool',
    'set_current_package_choice': '(self, team_name: str, package_key: str) -> bool',
})


ERS = define_methods('managers.ers_development_manager', 'ErsDevelopmentManager', {
    'is_open': "(self) -> 'bool'",
    'regulation_mode': "(self) -> 'str'",
})


AI_DEVELOPMENT = define_methods('managers.ai_development', 'AIDevelopment', {
    '_ensure_current_project': "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', pool: 'Dict[str, Dict[str, float]]', week_no: 'int') -> 'bool'",
    '_ensure_future_project': "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', pool: 'Dict[str, Dict[str, float]]') -> 'bool'",
    '_ensure_aero_projects': "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', finance: 'Dict[str, float]', pool: 'Dict[str, Dict[str, float]]') -> 'bool'",
    '_dev_project': "(self, team: 'Dict[str, Any]', key: 'str') -> 'Optional[Dict[str, Any]]'",
})


AERO_SCREEN = define_methods('ui.screens.aerodynamics_screen', 'AerodynamicsScreen', {
    '_execute_action': "(self, part: 'str', action: 'str') -> 'None'",
})


CHASSIS_SCREEN = define_methods('ui.screens.development_screen', 'DevelopmentScreen', {
    '_handle_button': "(self, key: 'str')",
})
