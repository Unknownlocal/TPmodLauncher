# SPDX-License-Identifier: MPL-2.0
"""Check and retain the native helpers used to plan a project safely.

A start-method fingerprint alone does not cover the helpers that clamp staff or
choose budgets. Check their bodies, defaults and static-method status once when
constructing the planner. Retain those checked functions for the scratch objects;
mod declarations never supply this list or these implementations.
"""

import hashlib
import inspect
import json
from types import FunctionType

from ...callables import code_digest
from ...manager import HookError
from .targets import project_targets


# Generated from declarations in the same inspected executable as targets.py.
# The group digest includes each method name, descriptor kind, code and defaults.
_GROUPS = {
    'aero': ((
        '_absolute_week',
        'budget_options',
        'default_budget_key',
        '_sanitize_budget_key',
        '_budget_meta',
        '_staff_capacity',
        'staff_in_use',
        'available_staff',
        'technical_staff_capacity',
        'available_technical_staff',
        '_clamp_staff',
        '_clamp_technical_staff',
        '_project_store',
    ), 'ea414a9102b62f6a08d87c4a1a0c5d74ba745028acc312604ca6f1758fe85abf'),
    'chassis': ((
        '_absolute_week',
        'staff_capacity',
        'cfd_capacity',
        'technical_staff_capacity',
        '_upkeep_staff_factor',
        'effective_staff_capacity',
        'effective_cfd_capacity',
        'effective_technical_staff_capacity',
        'technical_staff_in_use',
        'technical_staff_available',
        'chassis_staff_in_use',
        '_project_store',
        '_available_staff',
        '_clamp_staff',
        '_clamp_technical_staff',
        '_sanitize_budget_key',
        '_default_budget_option',
        '_default_budget_options',
        '_budget_amount_for_project_type',
        '_sanitize_current_package_key',
        '_canonical_current_package_key',
        '_current_package_budget_multiplier',
        '_is_spec_b_chassis_package_key',
    ), '377e730e7fc3a63c4e57cb3b0f9be56269986433f634e2f85ea6207e10a030d3'),
    'ers': ((
        'is_open',
        'regulation_mode',
    ), 'c00abc46e12b151ccef593e55b3e19abb425fb6ed11e931fbe49afb3ebc7359f'),
    'state': ((
        'get_hq_upkeep_pct',
    ), 'f29735da2bc03bd7eb21801c91f455ba279ad86c24b96ff8e3beb670bcd26afe'),
}


def method_group(owner, names):
    methods, records = {}, []
    for name in names:
        raw = inspect.getattr_static(owner, name)
        static = type(raw) is staticmethod
        function = raw.__func__ if static else raw
        if type(function) is not FunctionType or function.__closure__:
            raise HookError("Project planning needs ordinary checked native methods")
        methods[name] = function, static
        records.append((name, static, code_digest(function.__code__), repr(function.__defaults__),
                        repr(sorted((function.__kwdefaults__ or {}).items()))))
    digest = hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
    return methods, digest


def project_methods(state):
    targets = project_targets()
    owners = {"aero": state.aero_manager, "chassis": state.development_manager,
              "ers": getattr(state, "ers_development_manager", None), "state": state}
    for target in targets:
        owner = owners["aero" if "aero" in target.name else "chassis"]
        target._check(inspect.getattr_static(owner, target.attribute))
        if str(inspect.signature(inspect.getattr_static(owner, target.attribute))) != target.signature:
            raise HookError("Project admission signature changed")
    result = {}
    for role, owner in owners.items():
        if role == "ers" and owner is None:
            continue  # The native capacity helper treats missing ERS as open.
        names, expected = _GROUPS[role]
        methods, digest = method_group(owner, names)
        if digest != expected:
            raise HookError("Native project planning helpers changed: " + role)
        result[role] = methods
    return result
