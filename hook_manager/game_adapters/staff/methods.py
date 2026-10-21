# SPDX-License-Identifier: MPL-2.0
"""Check the read-only native helpers used to prepare staff employment terms."""

import inspect
import sys

from ...callables import method_group
from ...manager import HookError
from .targets import staff_target


# Signing chooses dates and employer kind through these helpers. Their bodies
# are checked before any borrowed roster, team or supplier data is read. Source
# line changes alone do not change these checks; relevant code/defaults do.
_STAFF_GROUPS = {
    'staff': (
        ('normalize_role', 'team_can_sign_role', 'supplier_can_sign_role', '_team_engine_plant_level', '_supplier_by_id', 'team_has_active_staff'),
        '1c05f53e03774ac5cde3fdbb06e8c348ef5498faf7671ebe074cd16dcd20ef8b',
    ),
    'state': (
        ('current_season_start_week', 'next_season_start'),
        '126d22c1769b2d17e56e7bd6efaffca841a3e746496383e5e0e1a2ddb1eb2874',
    ),
}


def staff_methods(state):
    target = staff_target()
    function = inspect.getattr_static(state.staff_manager, target.attribute)
    target._check(function)
    if str(inspect.signature(function)) != target.signature:
        raise HookError("Staff signing signature changed")
    module = sys.modules.get(target.module)
    if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
        raise HookError("Staff planning needs completed native imports")
    result = {}
    for role, owner in (("staff", state.staff_manager), ("state", state)):
        names, expected = _STAFF_GROUPS[role]
        methods, digest = method_group(owner, names)
        if digest != expected:
            raise HookError("Native staff planning helpers changed: " + role)
        result[role] = methods
    return result
