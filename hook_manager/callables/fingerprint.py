# SPDX-License-Identifier: MPL-2.0
"""Stable identity checks for trusted native code catalogues."""

import hashlib
import inspect
import json
from types import CodeType, FunctionType

if __package__:
    from ..manager import HookError


def code_digest(code):
    """Check implementation details without depending on where code was built.

    Ignore filenames and source lines, including those of nested functions. Keep
    instructions, constants and exception handling: matching arguments alone do
    not show that a helper still calculates the same terms or writes at the same
    point. This is a conservative compatibility check, not proof of equivalence;
    a different Python compiler can still require a refreshed catalogue.
    """
    def constant(value):
        # Nested functions are part of native identity; frozenset iteration order
        # must not make otherwise equivalent catalogues process-dependent.
        if type(value) is CodeType:
            return record(value)
        if type(value) in (tuple, frozenset):
            items = [constant(item) for item in value]
            return [type(value).__name__, sorted(items, key=repr) if type(value) is frozenset else items]
        if type(value) is bytes:
            return ["bytes", value.hex()]
        return [type(value).__name__, repr(value)]

    def record(item):
        # Source locations change when unrelated code is inserted earlier in a
        # module. They do not affect the calculation or our admission boundary.
        # Signature checks remain the target resolver's job.
        return [item.co_code.hex(), item.co_exceptiontable.hex(), item.co_name, item.co_qualname,
                item.co_argcount, item.co_posonlyargcount, item.co_kwonlyargcount,
                item.co_flags, item.co_stacksize, list(item.co_names), list(item.co_varnames),
                list(item.co_freevars), list(item.co_cellvars),
                [constant(value) for value in item.co_consts]]

    # Marshal's reference/interning flags can differ for equivalent code loaded
    # in separate processes. Hash values, not the serializer's object sharing.
    return hashlib.sha256(json.dumps(record(code), separators=(",", ":")).encode()).hexdigest()


def method_group(owner, names):
    """Retain a trusted helper group and check code, defaults and descriptor kind."""
    # Project and personnel planners reuse this check. Inspect without calling
    # a descriptor; keep the checked functions for binding to private state.
    methods, records = {}, []
    for name in names:
        raw = inspect.getattr_static(owner, name)
        static = type(raw) is staticmethod
        function = raw.__func__ if static else raw
        if type(function) is not FunctionType or function.__closure__:
            raise HookError("Native helper checks require ordinary functions")
        methods[name] = function, static
        records.append((name, static, code_digest(function.__code__), repr(function.__defaults__),
                        repr(sorted((function.__kwdefaults__ or {}).items()))))
    digest = hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
    return methods, digest
