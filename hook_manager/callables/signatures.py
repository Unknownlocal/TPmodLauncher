# SPDX-License-Identifier: MPL-2.0
"""Check native call signatures without pinning their implementations."""

import inspect
from types import FunctionType

if __package__:
    from ..manager import HookError


def check_signature(function, expected):
    # Guards execute synchronous Python methods. Body changes, closures and
    # decorator rearrangements are compatible when the exposed signature matches.
    if (type(function) is not FunctionType or inspect.iscoroutinefunction(function)
            or inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function)):
        raise HookError("Native callable must be a synchronous Python function")
    # Defaults and annotations are part of this exposed signature. Instructions,
    # constants and private local names are deliberately not inspected.
    if str(inspect.signature(function)) != expected:
        raise HookError("Native callable signature changed: " + function.__qualname__)


def method_group(owner, signatures):
    """Check named helper signatures and retain functions for private planning."""
    methods = {}
    for name, expected in signatures.items():
        raw = inspect.getattr_static(owner, name)
        static = type(raw) is staticmethod
        function = raw.__func__ if static else raw
        check_signature(function, expected)
        # Use the current descriptor's binding when preparing a scratch owner;
        # do not treat a descriptor change as an implementation fingerprint.
        methods[name] = function, static
    return methods
