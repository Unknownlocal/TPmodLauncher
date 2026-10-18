# SPDX-License-Identifier: MPL-2.0
"""Index JSON fields once and defer duplicate/value policy to selected subtrees."""

import math

_NO_DUPLICATES = frozenset()


def _strict(duplicates):
    if duplicates not in ("reject", "last"):
        raise ValueError("JSON duplicate policy must be 'reject' or 'last'")
    return duplicates == "reject"


class JsonObject:
    """A raw object with indexed last values and separate duplicate-key markers.

    Construction records structure only. Looking up one field never inspects its
    siblings or recursively validates its value. These nodes belong to the caller;
    materialize() creates independent dictionaries/lists for downstream models.
    """

    __slots__ = ("_values", "_duplicates")

    def __init__(self, pairs=()):
        values, duplicates = {}, []
        for key, value in pairs:
            if key in values:
                duplicates.append(key)
            values[key] = value
        # Most objects have no duplicates; share the immutable empty marker.
        self._values = values
        self._duplicates = frozenset(duplicates) if duplicates else _NO_DUPLICATES

    def member(self, name, default=None, *, duplicates="reject"):
        if _strict(duplicates) and name in self._duplicates:
            raise ValueError(f"Duplicate JSON field: {name}")
        return self._values.get(name, default)

    def materialize(self, *, exclude=(), duplicates="reject"):
        """Copy selected fields recursively, applying one explicit duplicate policy."""
        return self._plain(_strict(duplicates), frozenset(exclude))

    def _plain(self, strict, exclude=()):
        result = {}
        for key, value in self._values.items():
            if key in exclude:
                continue
            if strict and key in self._duplicates:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = _materialize(value, strict)
        return result


def _materialize(value, strict):
    if type(value) is JsonObject:
        return value._plain(strict)
    if type(value) is list:
        return [_materialize(item, strict) for item in value]
    # A syntactically valid number can overflow the decoder's float range. Check
    # it when consumed, so an excluded subtree does not impose value semantics.
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        return value
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError("Expected a value returned by JSON decode")


def materialize(value, *, duplicates="reject"):
    """Copy a decoded root/subtree, including arrays and scalars."""
    return _materialize(value, _strict(duplicates))
