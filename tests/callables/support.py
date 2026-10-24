# SPDX-License-Identifier: MPL-2.0
"""Shared native fixtures and trusted contract builders for callable tests."""

import inspect

from hook_manager.callables import CallableTarget, ResultContract


def boolean():
    return ResultContract({"shape": "bool", "denied": {"literal": False},
                           "accepted": {"path": [], "one_of": [True]}})



def pair():
    return ResultContract({"shape": {"tuple": ["bool", "string"]},
                           "denied": {"tuple": [{"literal": False}, {"message": True}]},
                           "accepted": {"path": [0], "one_of": [True]}})



def project(arguments, result):
    return {"value": arguments["value"]}



def approve(engine, module, attribute="method", owners=(), name=None):
    owner = module
    for part in owners:
        owner = getattr(owner, part)
    raw = inspect.getattr_static(owner, attribute)
    function = raw.__func__ if type(raw) in (staticmethod, classmethod) else raw
    engine.approve(CallableTarget(name or attribute, module.__name__, owners, attribute,
                                  str(inspect.signature(function))))
