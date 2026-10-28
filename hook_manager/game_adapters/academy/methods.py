# SPDX-License-Identifier: MPL-2.0
"""Retain read-only academy term helpers after signature-only checks."""

import inspect
import sys

from ...callables import method_group
from ...manager import HookError
from .targets import academy_targets


# These helpers read source data or copy a single contract record.
# Do not use ensure_team_state: it normalizes live academy lists and dictionaries.
_ACADEMY_METHODS = {
    'academy': {
        '_seasons_until_age_cap': '(self, driver) -> int',
        'signing_cost_m': '(self, driver) -> float',
        'contract_for': '(self, driver_name: str) -> Optional[Dict]',
    },
    'state': {
        '_team_record': '(self, name)',
        'current_season_start_week': '(self)',
    },
}


def academy_methods(state):
    targets = academy_targets()
    for target in targets:
        target._check(inspect.getattr_static(state.academy_manager, target.attribute))
    module = sys.modules.get(targets[0].module)
    if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
        raise HookError("Academy planning needs completed native imports")
    return {role: method_group(owner, _ACADEMY_METHODS[role])
            for role, owner in (("academy", state.academy_manager), ("state", state))}
