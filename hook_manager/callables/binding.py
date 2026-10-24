# SPDX-License-Identifier: MPL-2.0
"""Trusted adapter bindings; never accepted from mod declarations."""

# Normal host imports use the package; flat game bootstrap supplies dependencies.
if __package__:
    from ..manager import HookError, identifier
    from .result_contract import ResultContract


class CallBinding:
    """Trusted integration: target ID, adapter projection and native denial contract.

    project(bound_arguments, native_result) runs only trusted launcher code and
    must be read-only. It may normalize arbitrary native areas; it is never mod
    source. Scope-specific adapters later construct the public context/rule data.
    Arguments may include self/cls; the engine copies only the projection result
    for dispatch. native_result is None for a guard, or the query's actual result.
    """

    def __init__(self, id, target, project, result, *, phase="guard"):
        # IDs select approved host integrations; project is a trusted callable,
        # never a handler imported from a mod's source files into the game.
        self.id, self.target = identifier(id), identifier(target)
        if not callable(project) or not isinstance(result, ResultContract):
            raise HookError("Expected a trusted projection and return contract")
        # Guards evaluate before native execution. Query mappings must identify
        # read-only native queries and supply a selector preserving native refusals.
        if phase not in ("guard", "query") or phase == "query" and result.accepted is None:
            raise HookError("Queries require a native success selector")
        self.project, self.result, self.phase = project, result, phase
