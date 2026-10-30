# SPDX-License-Identifier: MPL-2.0
"""Admit direct season-end signing without replacing the game's season logic."""

from .native_operation import native_operation


def rollover_wrapper(guards, state, original, signature):
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        if guards.closed or not guards.owners or bound.arguments["self"] is not state:
            return original(*args, **kwargs)
        with native_operation(guards, "Season rollover"):
            return original(*args, **kwargs)
    return wrapped
