# SPDX-License-Identifier: MPL-2.0
"""Exact resident-component accounting; stable payloads are measured only once."""

import json
import math

from .policy import _constant, _object, encode


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


def measure(value, *, encoded=None):
    # Metric order: UTF-8 bytes, value nodes, container depth, integer work.
    # Object keys contribute bytes, while only values/containers contribute nodes.
    nodes, deepest, work = 0, 0, 0

    def visit(item, depth=0):
        nonlocal nodes, deepest, work
        nodes += 1
        if type(item) in (dict, list):
            depth += 1
            deepest = max(deepest, depth)
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError("Resident keys must be strings")
                children = item.values()
            else:
                children = item
            for child in children:
                visit(child, depth)
        elif type(item) is int:
            digits = max(1, (item.bit_length() * 30103 + 99999) // 100000)
            work += digits * digits if digits > 64 else 0
        elif item is not None and type(item) not in (str, bool):
            if type(item) is not float or not math.isfinite(item):
                raise ValueError("Expected finite plain resident JSON")

    visit(value)
    # Trusted host preparation can retain the encoding it just made. Guest
    # verification always omits this argument and measures received values itself.
    return len((encode(value) if encoded is None else encoded).encode("utf-8")), nodes, deepest, work


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


def bounded(metric):
    # These are per-record/expanded-invocation limits, not a regulations.json or
    # complete multi-call packet size limit.
    if metric[0] > 4096 or metric[1] > 1024 or metric[2] > 8:
        raise ValueError("Resident invocation exceeds byte/node/depth limits")
    return metric


def clone(value):
    """Fresh containers for authors; immutable scalars can remain shared."""
    if type(value) is dict:
        return {key: clone(child) for key, child in value.items()}
    if type(value) is list:
        return [clone(child) for child in value]
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
