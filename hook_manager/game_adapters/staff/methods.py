# SPDX-License-Identifier: MPL-2.0
"""Check the read-only native helpers used to prepare staff employment terms."""

import inspect
import sys

from ...callables import method_group
from ..native_methods import native_signatures, GAME_STATE
from .native_methods import STAFF
from ...manager import HookError
from .targets import staff_target


# These helpers read employer eligibility and season dates without changing
# live records. Check their exposed signatures before preparing terms.
_STAFF_GROUPS = {
    'staff': native_signatures(STAFF, (
        'normalize_role team_can_sign_role supplier_can_sign_role _team_engine_plant_level '
        '_supplier_by_id team_has_active_staff'
    )),
    'state': native_signatures(GAME_STATE, 'current_season_start_week next_season_start'),
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
