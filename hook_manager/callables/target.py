# SPDX-License-Identifier: MPL-2.0
"""Host-approved synchronous native targets checked by call signature."""

if __package__:
    from ..manager import HookError, Target
    from .signatures import check_signature


class CallableTarget(Target):
    """A trusted native path/signature; implementation changes do not disable it."""

    def __init__(self, name, module, owners, attribute, signature):
        # Keep the private five-argument contract; legacy Target's optional
        # replacement mode is not available through callable adapters.
        super().__init__(name, module, owners, attribute, signature)

    def _check(self, original):
        # Keep the outer callable so native decorators still execute. Existing
        # hook ownership is distinct from game-version compatibility.
        if hasattr(original, "_tpm_hook_group"):
            raise HookError("Native callable already belongs to a hook group: " + self.name)
        return check_signature(original, self.signature)
