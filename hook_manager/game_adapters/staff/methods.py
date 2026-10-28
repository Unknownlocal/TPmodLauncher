# SPDX-License-Identifier: MPL-2.0
"""Check the read-only native helpers used to prepare staff employment terms."""

import inspect
import sys

from ...callables import method_group
from ...manager import HookError
from .targets import staff_target


# These helpers read employer eligibility and season dates without changing
# live records. Check their exposed signatures before preparing terms.
_STAFF_GROUPS = {
    'staff': {
        'normalize_role': "(role: 'Optional[str]') -> 'str'",
        'team_can_sign_role': "(self, team_name: 'str', role: 'str') -> 'bool'",
        'supplier_can_sign_role': "(self, supplier_id: 'str', role: 'str') -> 'bool'",
        '_team_engine_plant_level': "(self, team_name: 'str') -> 'int'",
        '_supplier_by_id': "(self, supplier_id: 'str')",
        'team_has_active_staff': "(self, team_name: 'str', role: 'str', week: 'Optional[int]' = None) -> 'bool'",
    },
    'state': {
        'current_season_start_week': '(self)',
        'next_season_start': '(self)',
    },
}


def staff_methods(state):
    target = staff_target()
    function = inspect.getattr_static(state.staff_manager, target.attribute)
    target._check(function)
    module = sys.modules.get(target.module)
    if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
        raise HookError("Staff planning needs completed native imports")
    result = {}
    for role, owner in (("staff", state.staff_manager), ("state", state)):
        result[role] = method_group(owner, _STAFF_GROUPS[role])
    return result
