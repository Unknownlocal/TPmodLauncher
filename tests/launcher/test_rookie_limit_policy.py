# SPDX-License-Identifier: MPL-2.0
"""Retained cap preparation uses admitted records, without a guest or game."""

import json

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.validation import catalog as catalog_module
from launcher.regulations.validation import dispatch as dispatch_module
from launcher.regulations.validation.catalog import CombinedCatalog
from launcher.regulations.validation.dispatch import ValidationDispatch
from mod_runtime.source import SourceManifest, SourceSnapshot

OWNER = "cap.rules"


def definitions(declaration, *, overlays=None, custom=False):
    section = {"schema": 1}
    if declaration is not None:
        section["native_rules"] = {"rookie_contract_max_years": declaration}
    if overlays:
        section["option_data"] = overlays
    if custom:
        section["categories"] = [{"key": OWNER + ".policy", "label": "Cap", "default": "open",
            "options": [{"id": 1, "key": "open", "name": "Open", "parameters": {"maximum": None}},
                        {"id": 2, "key": "capped", "name": "Capped", "parameters": {"maximum": 4}}]}]
    manifest = SourceManifest(OWNER, "1", "0.4.0", "rules", frozenset({"regulations"}))
    source = SourceSnapshot(manifest, (("rules.py", ""),), 0)
    data = {"categories": [{"key": "native", "options": [{"key": "open"}]}],
            "mod_extensions": {OWNER: section}}
    return RegulationDefinitions.from_bytes(json.dumps(data).encode(), {OWNER: source}, scopes=set())


def publish(catalog, native=None, custom=None, *, epoch=1, generation=1):
    catalog.stage(native or {}, custom or {}, epoch=epoch, generation=generation)
    catalog.commit()
    return catalog.rookie.binding


def test_absent_and_literal_bindings_preserve_native_applicability():
    absent = CombinedCatalog(definitions(None))
    assert publish(absent) is None
    for maximum in (None, 3):
        model = definitions({"value": maximum})
        catalog = CombinedCatalog(model)
        with pytest.raises(RuntimeError, match="not published"):
            catalog.rookie.binding
        binding = publish(catalog)
        assert (binding.owner, binding.revision) == (OWNER, model.revisions[OWNER])
        assert binding.apply(1, epoch=1, generation=1) == maximum
        assert binding.apply(None, epoch=1, generation=1) is None


def test_native_choices_keep_saved_payloads_missing_values_and_explicit_null_distinct():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum"}))
    binding = publish(catalog, {"native": [
        {"key": "open", "maximum": 3},
        {"key": "open", "parameters": {"maximum": None}},
        {"key": "missing", "name": "Only presentation data"}]})
    assert [binding.apply(2, epoch=1, generation=1, choice=i) for i in range(3)] == [3, None, 2]
    assert binding.apply(None, epoch=1, generation=1, choice=0) is None
    for choice in (None, True, -1, 3):
        with pytest.raises(ValueError, match="published rule choice"):
            binding.apply(2, epoch=1, generation=1, choice=choice)


def test_option_data_is_the_owners_overlay_without_fallback_to_native_parameters():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum", "source": "option_data"},
                                         overlays={"native": {"open": {"maximum": 5}}}))
    binding = publish(catalog, {"native": [{"key": "open", "parameters": {"maximum": "unused"}},
                                         {"key": "saved", "parameters": {"maximum": 9}}]})
    assert binding.apply(2, epoch=1, generation=1, choice=0) == 5
    assert binding.apply(2, epoch=1, generation=1, choice=1) == 2


def test_custom_bridge_only_choices_reuse_records_and_never_create_guest_handlers(monkeypatch):
    rule = OWNER + ".policy"
    catalog = CombinedCatalog(definitions({"rule": rule, "parameter": "maximum"}, custom=True))
    binding = publish(catalog, custom={rule: ["capped", "open"]})
    assert catalog.owners == {} and catalog.subscribers == {rule: set()}
    assert binding.apply(1, epoch=1, generation=1, choice=0) == 4
    assert binding.apply(1, epoch=1, generation=1, choice=1) is None
    records = tuple(catalog._records.values())
    monkeypatch.setattr(catalog_module, "encode", lambda _: pytest.fail("Unchanged data was encoded"))
    newer = publish(catalog, custom={rule: None}, generation=2)
    assert tuple(catalog._records.values()) == records
    assert newer.apply(1, epoch=1, generation=2, choice=0) == 4


def test_commit_abort_and_reset_control_binding_visibility_and_freshness():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum"}))
    first = publish(catalog, {"native": [{"key": "open", "maximum": 3}]})
    catalog.stage({"native": [{"key": "open", "maximum": 7}]}, {}, epoch=1, generation=2)
    assert catalog.rookie.binding is first
    catalog.abort()
    assert catalog.rookie.binding is first
    second = publish(catalog, {"native": [{"key": "open", "maximum": None}]}, generation=3)
    assert second.apply(1, epoch=1, generation=3, choice=0) is None
    for epoch, generation in ((2, 1), (1, 3)):
        with pytest.raises(ValueError, match="Stale"):
            first.apply(1, epoch=epoch, generation=generation, choice=0)
    with pytest.raises(ValueError, match="newer policy"):
        catalog.stage({"native": None}, {}, epoch=1, generation=3)
    catalog.reset()
    with pytest.raises(RuntimeError, match="not published"):
        catalog.rookie.binding
    assert publish(catalog, {"native": [{"key": "open"}]}, epoch=2, generation=4).apply(
        2, epoch=2, generation=4, choice=0) == 2


def test_invalid_published_maximum_never_becomes_a_native_or_unlimited_binding():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum"}))
    with pytest.raises(ValueError, match="positive integer or null"):
        catalog.stage({"native": [{"key": "open", "maximum": -1}]}, {}, epoch=1, generation=1)
    catalog.abort()
    with pytest.raises(RuntimeError, match="not published"):
        catalog.rookie.binding


def test_dispatch_supplies_real_publication_epoch_and_generation():
    # Exercise the private preparation seam only. RegulationRuntime still
    # refuses bridge-bearing packs until native installation is implemented.
    dispatch = ValidationDispatch(definitions({"value": 4}), {}, epoch=7)
    assert not dispatch.ready
    dispatch.publish()
    first = dispatch.catalog.rookie.binding
    assert (first.epoch, first.generation) == (7, 1)
    dispatch.reset(8)
    with pytest.raises(RuntimeError, match="not published"):
        dispatch.catalog.rookie.binding
    dispatch.publish()
    assert dispatch.catalog.rookie.binding.apply(1, epoch=8, generation=2) == 4


def test_late_host_commit_makes_even_a_literal_only_binding_unavailable(monkeypatch):
    dispatch = ValidationDispatch(definitions({"value": 4}), {}, epoch=7)
    clock = iter((0, 0, 2))
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="Late resident publication"):
        dispatch.publish()
    assert not dispatch.ready
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.catalog.rookie.binding
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: 0)
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.publish()
    with pytest.raises(RuntimeError, match="cannot revive"):
        dispatch.reset(8)
