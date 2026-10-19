# SPDX-License-Identifier: MPL-2.0
"""Exact component arithmetic and preservation of warm-path canonical records."""

from mod_runtime._regulations import values as values


def test_component_metrics_match_expanded_encoding_with_unicode_depth_and_big_integers():
    for first, second in (({"quoted\"": [None, "é\U0001f600\n", -0.0]}, {"x": 10**3600}),
                          ({"a": {"b": [{}, 0, False]}}, {}), ({}, {"x": [[], {}]})):
        assert values.object_measure((("context", values.measure(first)), ("regulation", values.measure(second)))) == values.measure({"context": first, "regulation": second})
