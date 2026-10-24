# SPDX-License-Identifier: MPL-2.0
"""Checked callers and helpers whose preparation must precede project admission.

Compatibility uses method paths and signatures. The inspected
AI chassis caller selects its package before start; the start itself stores it
on success, so the earlier write can be suppressed within that checked caller.
"""

from ...callables import CallableTarget


_PROJECT_CALLERS = (
    ('aero_ui', 'ui.screens.aerodynamics_screen', 'AerodynamicsScreen', '_execute_action',
     "(self, part: 'str', action: 'str') -> 'None'"),
    ('chassis_ui', 'ui.screens.development_screen', 'DevelopmentScreen', '_handle_button',
     "(self, key: 'str')"),
    ('ai_current', 'managers.ai_development', 'AIDevelopment', '_ensure_current_project',
     "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', pool: 'Dict[str, Dict[str, float]]', week_no: 'int') -> 'bool'"),
    ('ai_future', 'managers.ai_development', 'AIDevelopment', '_ensure_future_project',
     "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', pool: 'Dict[str, Dict[str, float]]') -> 'bool'"),
    ('ai_aero', 'managers.ai_development', 'AIDevelopment', '_ensure_aero_projects',
     "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', finance: 'Dict[str, float]', pool: 'Dict[str, Dict[str, float]]') -> 'bool'"),
    ('aero_ai', 'managers.aero_manager', 'AeroDevelopmentManager', '_ai_try_launch_project',
     "(self, team_name: 'str', team: 'Dict', part_key: 'str', ai_state: 'Dict', bracket: 'str') -> 'Tuple[bool, float]'"),
    ('package', 'managers.development_manager', 'DevelopmentManager', 'set_current_package_choice',
     '(self, team_name: str, package_key: str) -> bool'),
    ('aero_parts', 'managers.aero_manager', 'AeroDevelopmentManager', '_part_record',
     "(self, team: 'Dict', part_key: 'str') -> 'Dict'"),
    ('aero_store', 'managers.aero_manager', 'AeroDevelopmentManager', '_project_store',
     "(self, team: 'Dict') -> 'Dict[str, Optional[Dict]]'"),
    ('chassis_store', 'managers.development_manager', 'DevelopmentManager', '_project_store',
     '(self, team: Dict) -> Dict[str, Optional[Dict]]'),
    ('ai_project', 'managers.ai_development', 'AIDevelopment', '_dev_project',
     "(self, team: 'Dict[str, Any]', key: 'str') -> 'Optional[Dict[str, Any]]'"),
)


def project_caller_targets():
    return tuple(CallableTarget("projects.caller." + role, module, (owner,), method, signature)
                 for role, module, owner, method, signature in _PROJECT_CALLERS)
