# SPDX-License-Identifier: MPL-2.0
"""Trusted adapter bindings; never accepted from mod declarations."""

# Normal host imports use the package; flat game bootstrap supplies dependencies.
if __package__:
    from ..manager import HookError, identifier


class CallBinding:
    """Trusted integration: target ID, read-only projection and native refusal function.

    project(bound_arguments, native_result) runs only trusted launcher code and
    must be read-only. It may normalise arbitrary native areas; it is never mod
    source. Scope-specific adapters later construct the public context/rule data.
    Arguments may include self/cls; the adapter copies only the projection result
    for dispatch. The second argument is None because checks precede native code.
    """

    def __init__(self, id, target, project, refuse):
        # Only launcher code supplies these functions. Mod declarations cannot
        # select native targets, run projections or choose a success-like refusal.
        self.id, self.target = identifier(id), identifier(target)
        if not callable(project) or not callable(refuse):
            raise HookError("Expected trusted projection and refusal functions")
        self.project, self.refuse = project, refuse
