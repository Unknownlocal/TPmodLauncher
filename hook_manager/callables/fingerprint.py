# SPDX-License-Identifier: MPL-2.0
"""Stable identity checks for trusted native code catalogues."""

import hashlib
import json
from types import CodeType


def code_digest(code):
    """Pin the exact native code object, including nested code and exception tables.

    Catalogue producers must use the same Python version and original frozen code;
    filenames/line tables are deliberately part of build compatibility, not ignored.
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
        # Include control flow, call layout and source/build metadata. This pins
        # the code object; signature checks remain the target resolver's job.
        return [item.co_code.hex(), item.co_exceptiontable.hex(), item.co_linetable.hex(),
                item.co_filename, item.co_firstlineno, item.co_name, item.co_qualname,
                item.co_argcount, item.co_posonlyargcount, item.co_kwonlyargcount,
                item.co_flags, item.co_stacksize, list(item.co_names), list(item.co_varnames),
                list(item.co_freevars), list(item.co_cellvars),
                [constant(value) for value in item.co_consts]]

    # Marshal's reference/interning flags can differ for equivalent code loaded
    # in separate processes. Hash values, not the serializer's object sharing.
    return hashlib.sha256(json.dumps(record(code), separators=(",", ":")).encode()).hexdigest()
