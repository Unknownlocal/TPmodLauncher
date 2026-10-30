# SPDX-License-Identifier: MPL-2.0
"""Check JSON values and count their size without repeatedly walking cached rules.

Record byte size, value count, nesting depth and large-integer conversion work.
Later checks combine those saved measurements with the action's context to enforce
the full argument limits. clone() makes independent arguments for each validator.
"""

import json
import math

from .policy import _constant, _object, encode
from .frozen import FrozenValue

_MAX_BYTES, _MAX_NODES, _MAX_DEPTH = 4096, 1024, 8


def integer(value, bits=32, *, positive=False):
    # bool subclasses int; exact type prevents True from becoming a slot or ID.
    if type(value) is not int or not int(positive) <= value < 2**bits:
        raise ValueError("Invalid resident integer")
    return value


def text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValueError("Invalid resident text")
    return value


def loads(value):
    # Callers already bound the scalar or complete transfer length. Duplicate
    # keys/nonfinite constants must not become ambiguous resident state.
    return json.loads(value, object_pairs_hook=_object, parse_constant=_constant)


def scalar(value):
    work = 0
    if type(value) is int:
        # 30103/100000 bounds log10(2) from above without a decimal conversion.
        # Large integer conversion needs quadratic work accounting beyond 64 digits.
        digits = max(1, (value.bit_length() * 30103 + 99999) // 100000)
        work = digits * digits if digits > 64 else 0
    return len(encode(value).encode("utf-8")), 1, 0, work


def measure(value, *, encoded=None, limited=False, limits=(_MAX_BYTES, _MAX_NODES, _MAX_DEPTH)):
    """Measure JSON, optionally stopping at the per-invocation size limits.

    Admission uses limited=True to reject excess nodes or nesting before encoding.
    Larger transfer tables use the same measurements without invocation limits.
    Signing uses the default bounds; voting supplies its larger bounds explicitly.
    """
    # Metric order: UTF-8 bytes, value nodes, container depth, integer work.
    # Object keys contribute bytes, while only values/containers contribute nodes.
    nodes, deepest, work = 0, 0, 0

    def visit(item, depth=0):
        nonlocal nodes, deepest, work
        nodes += 1
        kind = type(item)
        # depth counts enclosing containers; another container adds one level.
        if limited and (nodes > limits[1] or depth >= limits[2] and kind in (dict, list)):
            raise ValueError("Resident invocation exceeds byte/node/depth limits")
        if kind is dict:
            depth += 1
            deepest = max(deepest, depth)
            # Check keys during the value walk: avoid a generator and second
            # iteration for every object, including node-dense empty objects.
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("Resident keys must be strings")
                visit(child, depth)
        elif kind is list:
            depth += 1
            deepest = max(deepest, depth)
            for child in item:
                visit(child, depth)
        elif kind is int:
            digits = max(1, (item.bit_length() * 30103 + 99999) // 100000)
            work += digits * digits if digits > 64 else 0
        elif item is not None and kind not in (str, bool):
            if kind is not float or not math.isfinite(item):
                raise ValueError("Expected finite plain resident JSON")

    try:
        visit(value)
    finally:
        # The recursive helper holds a reference to itself. Release it on both
        # success and rejection so repeated measurements do not retain garbage
        # and leave its collection to later validator calls.
        visit = None
    # Trusted host preparation can retain the encoding it just made. Guest
    # verification always omits this argument and measures received values itself.
    metric = len((encode(value) if encoded is None else encoded).encode("utf-8")), nodes, deepest, work
    return bounded(metric, limits=limits) if limited else metric


def array_measure(metrics):
    metrics = tuple(metrics)
    return (2 + max(0, len(metrics) - 1) + sum(metric[0] for metric in metrics),
            1 + sum(metric[1] for metric in metrics),
            1 + max((metric[2] for metric in metrics), default=0),
            sum(metric[3] for metric in metrics))


def object_measure(pairs):
    # Compose cached children with braces, commas and encoded key/colon sizes.
    # This keeps the expanded bound exact without re-encoding resident payloads.
    pairs = tuple(pairs)
    return (2 + max(0, len(pairs) - 1) + sum(KEY_BYTES[key] + metric[0] for key, metric in pairs),
            1 + sum(metric[1] for _, metric in pairs),
            1 + max((metric[2] for _, metric in pairs), default=0),
            sum(metric[3] for _, metric in pairs))


def bounded(metric, *, limits=(_MAX_BYTES, _MAX_NODES, _MAX_DEPTH)):
    # These are per-record/expanded-invocation limits, not a regulations.json or
    # complete multi-call packet size limit.
    if metric[0] > limits[0] or metric[1] > limits[1] or metric[2] > limits[2]:
        raise ValueError("Resident invocation exceeds byte/node/depth limits")
    return metric


def clone(value):
    """Make fresh dicts/lists so a mod cannot change another call's arguments.

    Cached FrozenValue objects are rebuilt here; ordinary context dicts/lists
    are copied recursively. Strings and numbers can safely be shared.
    """
    kind = type(value)
    if kind is FrozenValue:
        return value.copy()
    if kind is dict:
        # Empty containers still need fresh identities, but no iterator or
        # recursive comprehension work. They are common at the node limit.
        return {key: clone(child) for key, child in value.items()} if value else {}
    if kind is list:
        return [clone(child) for child in value] if value else []
    return value


def install_fuel(byte_count, nodes, records, calls, work):
    """Count incoming data and retired payload/view nodes, including career reset."""
    return integer(8_000_000 + 512 * byte_count + 64_000 * nodes + 200_000 * records
                   + 250_000 * calls + 2 * work, 63, positive=True)


def entry_fuel(byte_count, nodes, calls, work):
    return integer(64_000_000 + 512 * byte_count + 64_000 * nodes + 250_000 * calls + 2 * work, 63, positive=True)


# Only fixed envelope keys are cached; arbitrary mod keys cannot grow this map.
KEY_BYTES = {key: scalar(key)[0] + 1 for key in (
    "schema", "key", "revision", "evaluation_season", "selected", "current", "upcoming",
    "effective_season", "parameters", "option_data", "context", "regulation",
    "owner", "contexts", "selections", "calls", "rule")}
