# SPDX-License-Identifier: MPL-2.0
"""Host-approved synchronous native targets and their decorator chains."""

import inspect
from types import FunctionType

# Normal host imports use the package; flat game bootstrap supplies dependencies.
if __package__:
    from ..manager import HookError, Target
    from .fingerprint import code_digest


class CallableTarget(Target):
    """Host-approved native target, including the complete expected decorator chain."""

    def __init__(self, name, module, owners, attribute, signature, fingerprints):
        # owners is the class path beneath module, distinct from validator-owner
        # IDs. Parent resolve checks the signature when the loaded target is bound.
        super().__init__(name, module, owners, attribute, signature)
        if (type(fingerprints) is not tuple or not 1 <= len(fingerprints) <= 8
                or any(type(item) is not str or len(item) != 64
                       or any(char not in "0123456789abcdef" for char in item)
                       for item in fingerprints)):
            raise HookError("Expected one to eight trusted code fingerprints")
        self.fingerprints = fingerprints

    def _check(self, original):
        """Verify outer-to-inner code identities without replacing the outer callable."""
        # Verify, but never unwrap away, the native decorator's business/cleanup code.
        function = original
        for fingerprint in self.fingerprints:
            if (type(function) is not FunctionType
                    or inspect.iscoroutinefunction(function)
                    or inspect.isgeneratorfunction(function)
                    or inspect.isasyncgenfunction(function)
                    or code_digest(function.__code__) != fingerprint):
                raise HookError(f"Native callable identity changed: {self.name}")
            function = vars(function).get("__wrapped__")
        # A matching prefix is insufficient: undeclared additional decorators
        # could change validation, business effects or cleanup.
        if function is not None:
            raise HookError("Native decorator chain differs from its catalogue")
