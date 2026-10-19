# SPDX-License-Identifier: MPL-2.0
"""Exact component arithmetic and preservation of warm-path canonical records."""

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
            assert values.encode(values.clone(FrozenValue(payload))) == values.encode(payload)
    with pytest.raises(ValueError, match="keys must be strings"):
        values.measure({"valid": [{}], 1: "invalid"})
