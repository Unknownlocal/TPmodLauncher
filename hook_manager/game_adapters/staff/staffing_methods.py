# SPDX-License-Identifier: MPL-2.0
"""Check the native helpers used by private team staffing preparation."""

import sys

from ...callables import method_group
from ...manager import HookError
from .methods import _STAFF_GROUPS
from .targets import staffing_targets


# These methods run on a private manager. Ranking and name-pool helpers only read
# borrowed data; generation and signing write newly created or selected people.
# Compatibility checks call shapes, while native conformance checks these writes.
_STAFFING_METHODS = {
    **{target.attribute: target.signature for target in staffing_targets()},
    '_create_free_staff_for_role': "(self, role: 'str', rng: 'random.Random') -> 'Staff'",
    '_generate_skill_profile': "(self, role: 'str', rng: 'random.Random') -> 'Dict[str, int]'",
    '_generate_talent_profile': "(self, skills: 'Dict[str, int]', rng: 'random.Random') -> 'Dict[str, int]'",
    '_adjust_profile_to_total': "(self, profile: 'Dict[str, int]', target_total: 'int', rng: 'random.Random') -> 'Dict[str, int]'",
    '_unique_staff_identity': "(self, rng: 'random.Random') -> 'Tuple[str, str]'",
    '_next_staff_id': "(self, reserved: 'Optional[Set[str]]' = None) -> 'str'",
    '_refresh_cached_skill': "(self, staff: 'Staff') -> 'None'",
    'set_team': "(self, staff: 'Staff', team_name: 'Optional[str]')",
    'role_skill_keys': "(role: 'str') -> 'List[str]'",
    '_normalize_skill_profile': "(self, role: 'str', raw_skills: 'Any', fallback_skill: 'int') -> 'Dict[str, int]'",
    '_normalize_talent_profile': "(self, role: 'str', raw_talent: 'Any', skills: 'Dict[str, int]') -> 'Dict[str, int]'",
    '_staff_skill_map': "(self, staff: 'Staff') -> 'Dict[str, int]'",
    '_staff_talent_map': "(self, staff: 'Staff') -> 'Dict[str, int]'",
    '_effective_staff_skill_map': "(self, staff: 'Staff') -> 'Dict[str, int]'",
    '_morale_skill_delta': "(self, staff: 'Staff') -> 'float'",
    'morale': "(self, staff: 'Staff') -> 'float'",
    '_clamp_score': "(value: 'Any', default: 'float' = 50.0) -> 'float'",
    'average_skill': "(self, staff: 'Staff') -> 'float'",
    'average_skill_int': "(self, staff: 'Staff') -> 'int'",
    'overall_skill': "(self, staff: 'Staff') -> 'int'",
    'raw_average_skill': "(self, staff: 'Staff') -> 'float'",
    'raw_average_skill_int': "(self, staff: 'Staff') -> 'int'",
    'expected_salary': "(self, staff: 'Staff') -> 'float'",
    'staff_team_next_season': "(self, staff: 'Staff') -> 'Optional[str]'",
    'staff_contracted_next_season': "(self, team_name: 'str', role: 'str') -> 'bool'",
    'add_staff_effect': "(self, staff: 'Staff', amount: 'float', weeks: 'int', reason: 'str', category: 'str' = '') -> 'None'",
    '_dedupe_staff_effects': "(self, effects: 'Any', fallback: 'str' = 'staff_morale') -> 'List[Dict[str, Any]]'",
    '_effect_key': "(effect: 'Dict[str, Any]', fallback: 'str' = '') -> 'str'",
}


def staffing_methods(manager):
    # As with the contract planner, incompatibility raises to the consuming
    # coordinator, which owns the CLI/log diagnostic and skipped integration.
    module = sys.modules.get("managers.staff_manager")
    if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
        raise HookError("Staffing preparation needs completed native imports")
    method_group(module, {
        '_clamp_skill': "(value: 'Any', fallback: 'int' = 10) -> 'int'",
        '_clamp_effective_skill': "(value: 'Any', fallback: 'float' = 10.0) -> 'int'",
        '_avg': "(values: 'List[int]', fallback: 'float' = 10.0) -> 'float'",
    })
    return {
        "staff": method_group(manager, {**_STAFF_GROUPS["staff"], **_STAFFING_METHODS}),
        "names": method_group(manager._name_pool, {
            'generate_identity': "(self, rng: 'Any', existing_names: 'Iterable[str]', *, attempts: 'int', fallback_prefix: 'str') -> 'Tuple[str, str]'",
            '_weighted_nationality': "(self, rng: 'Any') -> 'str'",
        }),
    }
