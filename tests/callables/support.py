# SPDX-License-Identifier: MPL-2.0
"""Shared native fixtures and trusted projections for callable tests."""

import inspect

from hook_manager.callables import CallableTarget


def project(arguments, result):
    return {"effective": {"season": 1}, "proposal": {"value": arguments["value"]}}


def approve(engine, module, attribute="method", owners=(), name=None):
    owner = module
    for part in owners:
        owner = getattr(owner, part)
    raw = inspect.getattr_static(owner, attribute)
    function = raw.__func__ if type(raw) in (staticmethod, classmethod) else raw
    engine.approve(CallableTarget(name or attribute, module.__name__, owners, attribute,
                                  str(inspect.signature(function))))
