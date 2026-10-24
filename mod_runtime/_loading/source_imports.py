# SPDX-License-Identifier: MPL-2.0
"""Resolve a mod's ordinary absolute imports without global module aliases."""

import builtins
import sys


class SourceImports:
    """Give modules their owner's local names while keeping real Python packages."""

    def __init__(self, namespace, sources):
        self.namespace = namespace
        self.roots = {path.split("/", 1)[0].removesuffix(".py") for path in sources}
        self.builtins = dict(vars(builtins))
        self.builtins["__import__"] = self.import_module

    def import_module(self, name, globals=None, locals=None, fromlist=(), level=0):
        # Relative imports already carry the real, owner-qualified __package__.
        # Standard-library imports also keep Python's normal resolution.
        root = name.split(".", 1)[0]
        if level or root not in self.roots:
            return builtins.__import__(name, globals, locals, fromlist, level)
        module = builtins.__import__(self.namespace + "." + name, globals, locals, fromlist, 0)
        # `import pkg.child` expects the local pkg object; returning the synthetic
        # outer namespace would give Python the wrong object to bind to `pkg`.
        return module if fromlist else sys.modules[self.namespace + "." + root]
