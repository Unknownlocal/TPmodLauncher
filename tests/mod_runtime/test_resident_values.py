# SPDX-License-Identifier: MPL-2.0
"""Check value measurements, copied records and temporary helper cleanup."""

import gc
from types import FunctionType

import pytest

from mod_runtime._regulations import values
from mod_runtime._regulations.frozen import FrozenValue


def test_component_metrics_match_expanded_encoding_with_unicode_depth_and_big_integers():
    for first, second in (({"quoted\"": [None, "é\U0001f600\n", -0.0]}, {"x": 10**3600}),
                          ({"a": {"b": [{}, 0, False]}}, {}), ({}, {"x": [[], {}]})):
        assert values.object_measure((("context", values.measure(first)), ("regulation", values.measure(second)))) == values.measure({"context": first, "regulation": second})
        assert values.array_measure((values.measure(first), values.measure(second))) == values.measure([first, second])
        assert values.measure(first, encoded=values.encode(first)) == values.measure(first)
        for payload in (first, second):
            assert values.measure(payload, limited=True) == values.bounded(values.measure(payload))
            assert values.encode(values.clone(FrozenValue(payload))) == values.encode(payload)
    with pytest.raises(ValueError, match="keys must be strings"):
        values.measure({"valid": [{}], 1: "invalid"})


def test_measure_releases_temporary_helpers_without_waiting_for_gc():
    # Disable automatic cleanup so it cannot conceal a reference cycle. Other
    # tests may have left objects behind; only inspect functions created here.
    enabled = gc.isenabled()
    gc.disable()
    try:
        previous = {id(item) for item in gc.get_objects() if type(item) is FunctionType}
        values.measure({"nested": [{}, 1]})
        retained = [item for item in gc.get_objects() if type(item) is FunctionType
                    and item.__module__ == values.__name__ and id(item) not in previous]
        assert not retained
    finally:
        if enabled:
            gc.enable()


def test_limited_measure_rejects_excess_nodes_and_depth_before_reading_later_values():
    nested = {"next": object()}
    for _ in range(8):
        nested = {"next": nested}
    for payload in ([None] * 1024 + [object()], nested):
        with pytest.raises(ValueError, match="byte/node/depth limits"):
            values.measure(payload, limited=True)
