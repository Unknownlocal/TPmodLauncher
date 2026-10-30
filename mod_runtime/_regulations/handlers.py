# SPDX-License-Identifier: MPL-2.0
"""Frozen runner-owned handler bindings; never included in the author facade."""

import importlib
from types import FunctionType

from _source_policy import module_part
from .policy import decode, fields


class RegulationHandlers:
    """Resolve admitted source functions, then freeze bindings for resident policy."""

    def __init__(self, sources, *, owner, namespace):
        self._sources, self._handlers = sources, []
        self._sealed = False
        self.owner, self.namespace = owner, namespace

    def bind(self, index, text, *, voting=False):
        if self._sealed or type(index) is not int or index != len(self._handlers) or index >= 32:
            raise ValueError("Invalid or repeated regulation binding")
        data = decode(text)
        fields(data, ("handler", "rule", "kind", "owner") if voting else ("handler", "rule", "scope", "owner"))
        if voting and data["kind"] != "voting":
            raise ValueError("Invalid voting binding kind")
        if any(type(value) is not str or not value for value in data.values()):
            raise ValueError("Invalid regulation binding names")
        if len(data["owner"]) > 128 or self.owner != data["owner"]:
            raise ValueError("Foreign regulation binding owner")
        parts = data["handler"].split(".")
        if len(parts) < 2:
            raise ValueError("Handler needs an owned module and function")
        for part in parts:
            module_part(part)
        module_name, stem = ".".join(parts[:-1]), "/".join(parts[:-1])
        paths = (stem + ".py", stem + "/__init__.py")
        if not any(path in self._sources for path in paths):
            raise ValueError("Handler module is absent from the owned snapshot")
        qualified = self.namespace + "." + module_name
        function = getattr(importlib.import_module(qualified), parts[-1])
        # Only synchronous source functions with exactly two positional arguments
        # are admitted. No callable instances, generators, coroutines or varargs.
        # Re-exports from another owned module remain ordinary owned functions;
        # checking the actual code path excludes imported standard-library code.
        prefix = "<mod>/" + self.namespace + "/"
        if (type(function) is not FunctionType or not function.__code__.co_filename.startswith(prefix)
                or function.__code__.co_filename.removeprefix(prefix) not in self._sources
                or function.__code__.co_argcount != 2 or function.__code__.co_kwonlyargcount
                or function.__code__.co_flags & (4 | 8 | 32 | 128 | 512)):
            raise ValueError("Handler must be an owned synchronous two-argument function")
        self._handlers.append((function, "voting" if voting else data["scope"], data["rule"]))

    def seal(self):
        """Freeze admitted bindings before the first resident policy is installed."""
        self._sealed = True
        return tuple(self._handlers)
