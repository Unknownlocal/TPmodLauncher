# SPDX-License-Identifier: MPL-2.0
"""Frozen runner-owned handler bindings; never included in the author facade."""

import importlib
from types import FunctionType

from _source_policy import module_part
from .policy import MAX_CALLS, decode, decision, fields, invocation


class RegulationHandlers:
    """Resolve only admitted source functions and evaluate copied snapshots."""

    def __init__(self, sources):
        self._sources, self._handlers = sources, []
        self._prepared, self._next = None, 0
        self._sealed = False
        self.owner = None

    def bind(self, index, text):
        if self._sealed or type(index) is not int or index != len(self._handlers) or index >= 32:
            raise ValueError("Invalid or repeated regulation binding")
        data = decode(text)
        fields(data, ("handler", "rule", "scope", "owner"))
        if any(type(value) is not str or not value for value in data.values()):
            raise ValueError("Invalid regulation binding names")
        if len(data["owner"]) > 128 or self.owner not in (None, data["owner"]):
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
        function = getattr(importlib.import_module(module_name), parts[-1])
        # Only synchronous source functions with exactly two positional arguments
        # are admitted. No callable instances, generators, coroutines or varargs.
        # Re-exports from another owned module remain ordinary owned functions;
        # checking the actual code path excludes imported standard-library code.
        if (type(function) is not FunctionType or not function.__code__.co_filename.startswith("<mod>/")
                or function.__code__.co_filename.removeprefix("<mod>/") not in self._sources
                or function.__code__.co_argcount != 2 or function.__code__.co_kwonlyargcount
                or function.__code__.co_flags & (4 | 8 | 32 | 128 | 512)):
            raise ValueError("Handler must be an owned synchronous two-argument function")
        self._handlers.append((function, data["scope"], data["rule"]))
        self.owner = data["owner"]

    def seal(self):
        """Switch this Store to resident dispatch without changing admitted code."""
        # Do not switch protocols while legacy argument slots are outstanding.
        # Once sealed, neither bindings nor the legacy staging path may change.
        if self._prepared is not None:
            raise ValueError("Previous regulation preparation has not been consumed")
        self._sealed = True
        return tuple(self._handlers)

    def _stage(self, calls):
        if self._sealed or self._prepared is not None:
            raise ValueError("Previous regulation preparation has not been consumed")
        prepared = []
        for index, context, regulation in calls:
            if type(index) is not int or not 0 <= index < len(self._handlers):
                raise ValueError("Unknown regulation binding")
            function, scope, rule = self._handlers[index]
            context, regulation = invocation(context, regulation)
            if context["scope"] != scope or regulation["key"] != rule:
                raise ValueError("Foreign regulation invocation")
            prepared.append((function, context, regulation))
        # Preparation calls no handler. Publish only a complete set of independent
        # copies, then consume each once under its own ordinary execution budget.
        self._prepared, self._next = prepared, 0

    def prepare(self, index, context, regulation):
        self._stage([(index, context, regulation)])

    def evaluate(self, index):
        """Consume a batch-local slot; this index is not the validator binding ID."""
        if type(index) is not int or self._prepared is None or index != self._next:
            raise ValueError("Unknown, repeated or out-of-order prepared invocation")
        function, context, regulation = self._prepared[index]
        self._prepared[index] = None
        self._next += 1
        if self._next == len(self._prepared):
            self._prepared = None
        return decision(function(context, regulation))

    def prepare_batch(self, text):
        packet = decode(text)
        fields(packet, ("contexts", "rules", "calls"))
        if any(type(value) is not list or not 1 <= len(value) <= MAX_CALLS for value in packet.values()):
            raise ValueError("Invalid regulation batch dimensions")
        calls = []
        for call in packet["calls"]:
            if (type(call) is not list or len(call) != 3
                    or any(type(index) is not int or index < 0 for index in call)
                    or call[1] >= len(packet["contexts"]) or call[2] >= len(packet["rules"])):
                raise ValueError("Invalid regulation batch reference")
            calls.append((call[0], packet["contexts"][call[1]], packet["rules"][call[2]]))
        self._stage(calls)
