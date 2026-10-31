# SPDX-License-Identifier: MPL-2.0
"""Check value measurements, copied records and temporary helper cleanup."""

import gc
from copy import deepcopy
from types import FunctionType

import pytest

from mod_runtime._regulations import values
from mod_runtime._regulations.contexts import RegulationContexts
from tests.regulation_data import contract_context as context
from mod_runtime._regulations.frozen import FrozenValue
from mod_runtime._regulations.voting import voting_packet


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


def test_template_copies_keep_cached_and_nested_mutable_values_independent():
    shared = {"values": [1, {"number": 10**100}], "empty": []}
    payload = {"first": shared, "second": shared, "text": "é😀"}
    expected = deepcopy(payload)
    cached = FrozenValue(payload, templates=True)
    first = cached.copy()
    first["first"]["values"][1]["number"] = -1
    first["first"]["empty"].append(1)
    shared["values"].append(2)
    assert first["second"] == expected["second"]
    assert cached.copy() == expected


def test_voting_part_measurements_match_complete_encoding():
    packet = {"owner": "sample", "binding": 0,
              "snapshot": {"voting_model_version": 1, "season": 1, "teams": {}, "results": [],
                           "field": {"quoted\"": ["é😀\n", 10**100, {}, []]}, "projected_races": 0},
              "regulation": {"key": "rule", "current": {"key": "a"}, "proposed": {"key": "b"}},
              "contexts": [{"schema": 1, "review": "r", "proposal": "p", "decision_season": 1,
                            "effective_season": 2, "team": "team", "native": {"choice": "current", "reason": "Keep"}, "seed": "s"}]}
    assert voting_packet(packet) == values.measure(packet)


def test_shared_context_cache_leaves_gc_tracking_before_handlers_run():
    # Disable automatic GC so it cannot conceal where the first cache scan
    # happens. This protects handler budgets, rather than timing a busy machine.
    enabled = gc.isenabled()
    gc.disable()
    try:
        contexts = RegulationContexts(1, [context(points=25)])
        cached, _, _, _ = contexts.get(0)
        assert not gc.is_tracked(cached.tokens)
        assert cached.copy() == context(points=25)
    finally:
        if enabled:
            gc.enable()
