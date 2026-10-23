# SPDX-License-Identifier: MPL-2.0
"""Add regulation invocation metadata to independently prepared game proposals."""

from ...manager import HookError


def regulation_context(scope, prepared, *, initiator="unknown", operation="commit"):
    # Activation checks describe a stored agreement taking effect. The private
    # validator envelope already recognizes that operation alongside new commits.
    if initiator not in ("player", "ai", "system", "unknown") or operation not in ("preview", "commit", "activate"):
        raise HookError("Unsupported regulation proposal context")
    # Native planners own the calculations and returned data. Add only invocation
    # metadata here; every subscribing rule receives the same prepared proposal.
    return {
        "schema": 1, "scope": scope, "operation": operation, "initiator": initiator,
        "clock": prepared["clock"], "effective": prepared["effective"], "proposal": prepared["proposal"],
    }
