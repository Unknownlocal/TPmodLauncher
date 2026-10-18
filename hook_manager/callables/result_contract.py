# SPDX-License-Identifier: MPL-2.0
"""Structural native return checks and guaranteed refusal rendering."""

import math

# The game bundle supplies these names before executing this file without a package.
if __package__:
    from ..manager import HookError, data_copy


def _return_shape(spec, depth=0):
    """Compile a trusted shape into a checker; no native object access is evaluated.

    Checks describe the native function's return format, not a regulation's
    conditions. Exact types prevent bool/int coercion from hiding a changed API.
    """
    if depth > 6:
        raise HookError("Return shape is too deep")
    if type(spec) is str:
        kinds = {"bool": (bool,), "string": (str,), "int": (int,),
                 "number": (int, float), "null": (type(None),)}
        if spec not in kinds:
            raise HookError("Unknown return primitive")
        return lambda value: type(value) in kinds[spec] and (
            type(value) is not float or math.isfinite(value))
    if type(spec) is not dict or len(spec) != 1:
        raise HookError("Invalid return shape")
    kind, value = next(iter(spec.items()))
    if kind == "optional":
        # Nullable is a shape property; ResultContract separately decides success.
        child = _return_shape(value, depth + 1)
        return lambda result: result is None or child(result)
    if kind == "tuple" and type(value) is list and len(value) <= 16:
        children = [_return_shape(item, depth + 1) for item in value]
        return lambda result: type(result) is tuple and len(result) == len(children) and all(
            check(item) for check, item in zip(children, result))
    if (kind == "list" and type(value) is dict and set(value) == {"item", "max_items"}
            and type(value["max_items"]) is int and 0 <= value["max_items"] <= 128):
        child = _return_shape(value["item"], depth + 1)
        maximum = value["max_items"]
        # Reject excess entries instead of truncating away a native refusal.
        return lambda result: type(result) is list and len(result) <= maximum and all(child(item) for item in result)
    if (kind == "record" and type(value) is dict and len(value) <= 16
            and all(type(key) is str for key in value)):
        children = {key: _return_shape(item, depth + 1) for key, item in value.items()}
        return lambda result: type(result) is dict and set(result) == set(children) and all(
            check(result[key]) for key, check in children.items())
    raise HookError("Unsupported return shape")



def _denial_template(spec, depth=0):
    """Compile literals/message slots into fresh native refusal values.

    Lists and records are rebuilt for each render so a caller cannot mutate a
    shared template or the construction-verified fallback used by a later call.
    """
    if depth > 6 or type(spec) is not dict or len(spec) != 1:
        raise HookError("Invalid denial template")
    kind, value = next(iter(spec.items()))
    if kind == "literal" and (value is None or type(value) in (str, bool, int, float)):
        if type(value) is float and not math.isfinite(value):
            raise HookError("Non-finite denial literal")
        return lambda message: value
    if kind == "message" and value is True:
        return lambda message: message
    if kind in ("tuple", "list") and type(value) is list and len(value) <= 16:
        children = [_denial_template(item, depth + 1) for item in value]
        return lambda message: (tuple(child(message) for child in children) if kind == "tuple"
                                else [child(message) for child in children])
    if (kind == "record" and type(value) is dict and len(value) <= 16
            and all(type(key) is str for key in value)):
        children = {key: _denial_template(item, depth + 1) for key, item in value.items()}
        return lambda message: {key: child(message) for key, child in children.items()}
    raise HookError("Unsupported denial template")



class ResultContract:
    """Translate validator denial into a trusted native return convention.

    `shape` checks the return structure; `denied` builds its refusal value.
    Optional `accepted` selects a native field/index and its success sentinels;
    CallBinding requires it for query mode. These are host plumbing declarations,
    supplied by trusted mappings rather than interpreted from mod rule data.
    """

    def __init__(self, spec):
        # Retain an independent declaration for comparison and registration copies.
        spec = data_copy(spec)
        if (type(spec) is not dict or not {"shape", "denied"} <= set(spec)
                or set(spec) - {"shape", "denied", "accepted"}):
            raise HookError("Invalid native result contract")
        self.spec = spec
        self.check = _return_shape(spec["shape"])
        self.render = _denial_template(spec["denied"])
        self.path, self.accepted = (), None
        if "accepted" in spec:
            # Scalars cover flag/reason APIs; [] also represents success for
            # native validators that return a bounded list of error messages.
            selector = spec["accepted"]
            if (type(selector) is not dict or set(selector) != {"path", "one_of"}
                    or type(selector["path"]) is not list or len(selector["path"]) > 6
                    or any(type(key) not in (str, int) for key in selector["path"])
                    or type(selector["one_of"]) is not list
                    or not 1 <= len(selector["one_of"]) <= 8
                    or any(item is not None and type(item) not in (str, bool, int, float)
                           and not (type(item) is list and not item)
                           for item in selector["one_of"])):
                raise HookError("Invalid native success selector")
            self.path, self.accepted = tuple(selector["path"]), selector["one_of"]
        # Every admitted contract must have a usable, non-success failure fallback
        # before it can guard any native operation.
        denial = self.render("Validation unavailable")
        if not self.check(denial):
            raise HookError("Denial template violates native return shape")
        if self.accepted is not None and self.allowed(denial):
            raise HookError("Denial template encodes native success")

    def denied(self, message):
        """Render a native refusal, substituting the safe fallback on sentinel collision."""
        result = self.render(message)
        if not self.check(result):
            raise HookError("Denial template violates native return shape")
        # A printable refusal may equal a native success sentinel. The fallback
        # was verified against this exact selector at construction; never encode
        # such a message as success (including a nested tuple/record selector).
        if self.accepted is not None and self.allowed(result):
            return self.render("Validation unavailable")
        return result

    def allowed(self, result):
        """Read success through plain containers only, preserving exact scalar types."""
        if self.accepted is None or not self.check(result):
            raise HookError("Native query violates its result contract")
        value = result
        for key in self.path:
            if (type(value) is dict and type(key) is str
                    or type(value) in (tuple, list) and type(key) is int and key >= 0):
                value = value[key]
            else:
                raise HookError("Invalid native success path")
        # Equality alone would let 1 match True; that changes native API semantics.
        return any(type(value) is type(item) and value == item for item in self.accepted)
