# SPDX-License-Identifier: MPL-2.0
"""Checked callers and helpers whose preparation must precede project admission.

Source locations are excluded from these implementation fingerprints. The
AI chassis caller selects its package before start; the start itself stores it
on success, so the earlier write can be suppressed within that checked caller.
"""

from ...callables import CallableTarget


_PROJECT_CALLERS = (
    ('aero_ui', 'ui.screens.aerodynamics_screen', 'AerodynamicsScreen', '_execute_action',
     "(self, part: 'str', action: 'str') -> 'None'",
     '0bd73421d2ed4dc2ac2d65c1e68fe7e2505bd4b8da4ff84f78633f1b0cea381b'),
    ('chassis_ui', 'ui.screens.development_screen', 'DevelopmentScreen', '_handle_button',
     "(self, key: 'str')",
     '2ac5479794c8c3abe7b8e5475b47a37f51dae271d541cbbb91d4572ad8b67ae0'),
    ('ai_current', 'managers.ai_development', 'AIDevelopment', '_ensure_current_project',
     "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', pool: 'Dict[str, Dict[str, float]]', week_no: 'int') -> 'bool'",
     'fc068421abdae7824c4bf921d3413461994c441059642575d8081d22b0c7235e'),
    ('ai_future', 'managers.ai_development', 'AIDevelopment', '_ensure_future_project',
     "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', pool: 'Dict[str, Dict[str, float]]') -> 'bool'",
     'e7344ef0c553a33d5eac806dadb3bc1df7c5214cbcabb86689b333ac605fabd7'),
    ('ai_aero', 'managers.ai_development', 'AIDevelopment', '_ensure_aero_projects',
     "(self, team_name: 'str', team: 'Dict[str, Any]', fiscal_tier: 'str', finance: 'Dict[str, float]', pool: 'Dict[str, Dict[str, float]]') -> 'bool'",
     '967eb930a60370fffdfa49544259a2867fd4c962433cb1f95ebe6b75b9ff0975'),
    ('aero_ai', 'managers.aero_manager', 'AeroDevelopmentManager', '_ai_try_launch_project',
     "(self, team_name: 'str', team: 'Dict', part_key: 'str', ai_state: 'Dict', bracket: 'str') -> 'Tuple[bool, float]'",
     '43d8698be93e09aba594deb7d71d9268918236dcb7ab4b77957f471dc9b51f23'),
    ('package', 'managers.development_manager', 'DevelopmentManager', 'set_current_package_choice',
     '(self, team_name: str, package_key: str) -> bool',
     'e4610984adba428864b29cf5449c8368b746c76692c315c61205ae6607cc0ff4'),
    ('aero_parts', 'managers.aero_manager', 'AeroDevelopmentManager', '_part_record',
     "(self, team: 'Dict', part_key: 'str') -> 'Dict'",
     '77e1a0ab2ef4c51a2eef04b1cd7718f0623d40465c69864c87e4bdca35e99df7'),
    ('aero_store', 'managers.aero_manager', 'AeroDevelopmentManager', '_project_store',
     "(self, team: 'Dict') -> 'Dict[str, Optional[Dict]]'",
     '3451337f0d5fd64c62a411d7c9da88d2188413275c33902603955ee046e45c1b'),
    ('chassis_store', 'managers.development_manager', 'DevelopmentManager', '_project_store',
     '(self, team: Dict) -> Dict[str, Optional[Dict]]',
     '8345f66f3fd58563a8ce1db7a47d1aa8671b552e0dc9f773538680ad008f763b'),
    ('ai_project', 'managers.ai_development', 'AIDevelopment', '_dev_project',
     "(self, team: 'Dict[str, Any]', key: 'str') -> 'Optional[Dict[str, Any]]'",
     'f732b1d6da6768c70471d05b217a1de2cd091fe6009a736028e5ff6078b2c82c'),
)


def project_caller_targets():
    return tuple(CallableTarget("projects.caller." + role, module, (owner,), method, signature, (digest,))
                 for role, module, owner, method, signature, digest in _PROJECT_CALLERS)
