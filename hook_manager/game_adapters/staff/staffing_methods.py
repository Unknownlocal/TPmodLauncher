# SPDX-License-Identifier: MPL-2.0
"""Check the native helpers used by private team staffing preparation."""

import sys

from ...callables import method_group
from ..native_methods import native_signatures
from .native_methods import STAFF
from ...manager import HookError
from .methods import _STAFF_GROUPS


# These methods run on a private manager. Ranking and name-pool helpers only read
# borrowed data; generation and signing write newly created or selected people.
_STAFFING_METHODS = native_signatures(STAFF, (
    'onboard_joining_team_staff ensure_active_teams_have_staff _create_free_staff_for_role '
    '_generate_skill_profile _generate_talent_profile _adjust_profile_to_total '
    '_unique_staff_identity _next_staff_id _refresh_cached_skill set_team role_skill_keys '
    '_normalize_skill_profile _normalize_talent_profile _staff_skill_map _staff_talent_map '
    '_effective_staff_skill_map _morale_skill_delta morale _clamp_score average_skill '
    'average_skill_int overall_skill raw_average_skill raw_average_skill_int expected_salary '
    'staff_team_next_season staff_contracted_next_season add_staff_effect _dedupe_staff_effects '
    '_effect_key'
))


def staffing_methods(manager):
    # The coordinator reports incompatibility and skips integration.
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
