# SPDX-License-Identifier: MPL-2.0
"""Measure the fixed minimum invocation once, then account for owner metadata."""

from dataclasses import dataclass
import json


@dataclass(frozen=True, slots=True)
class PayloadMeasure:
    """Size of JSON values for the static regulation payload limits."""

    nodes: int
    depth: int
    utf8_bytes: int

    def check(self):
        if self.nodes > 1024:
            raise ValueError("Regulation payload exceeds its node limit")
        if self.depth > 8:
            raise ValueError("Regulation payload exceeds its container depth")
        if self.utf8_bytes > 4096:
            raise ValueError("Regulation payload exceeds 4096 UTF-8 bytes")


def measure(value):
    """Measure bounded JSON data; keys count toward bytes, values toward nodes."""
    nodes, deepest = 0, 0

    def visit(item, depth=0):
        nonlocal nodes, deepest
        nodes += 1
        if nodes > 1024:
            raise ValueError("Regulation payload exceeds its node limit")
        if type(item) in (dict, list):
            depth += 1
            if depth > 8:
                raise ValueError("Regulation payload exceeds its container depth")
            deepest = max(deepest, depth)
            for child in item.values() if type(item) is dict else item:
                visit(child, depth)

    visit(value)
    size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    result = PayloadMeasure(nodes, deepest, size)
    result.check()
    return result


def minimum(key, option):
    """Measure a current-option envelope with empty context and owner metadata.

    This is only a static lower bound. Dispatch must still check real context,
    revision/season values and any upcoming policy against the full limits.
    """
    return measure({"context": {}, "regulation": {
        "schema": 1, "key": key, "revision": "x", "evaluation_season": 0,
        "selected": "current", "upcoming": None, "current": {
            "key": option["key"], "effective_season": 0,
            "parameters": option.get("parameters", {}), "option_data": {}}}})


def check_metadata(base, metadata):
    """Replace minimum()'s empty option_data object using exact measurements."""
    if not metadata:
        return  # The cached base already includes and checks an empty object.
    extra = measure(metadata)
    # option_data sits below root/regulation/current: its container depth has an
    # offset of three. Replace the {} placeholder (one node, two UTF-8 bytes).
    PayloadMeasure(base.nodes - 1 + extra.nodes, max(base.depth, 3 + extra.depth),
                   base.utf8_bytes - 2 + extra.utf8_bytes).check()
