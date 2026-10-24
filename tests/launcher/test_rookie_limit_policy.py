# SPDX-License-Identifier: MPL-2.0
"""Rookie values share catalogue transactions and dispatcher policy identities."""

import json

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.rookie_contract_limit import RookieContractLimit, apply_rookie_maximum
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


def publish(catalog, native=None, custom=None):
    catalog.stage(native or {}, custom or {})
    catalog.commit()


def test_absent_and_literal_values_preserve_native_applicability():
    absent = CombinedCatalog(definitions(None))
    publish(absent)
    assert absent.rookie_limit() is None
    assert apply_rookie_maximum(1, absent.rookie_limit()) == 1
    for maximum in (None, 3):
        catalog = CombinedCatalog(definitions({"value": maximum}))
        with pytest.raises(RuntimeError, match="not published"):
            catalog.rookie_limit()
        publish(catalog)
        limit = catalog.rookie_limit()
        assert limit == RookieContractLimit(maximum)
        assert apply_rookie_maximum(1, limit) == maximum
        assert apply_rookie_maximum(None, limit) is None


def test_native_choices_keep_saved_payloads_missing_values_and_explicit_null_distinct():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum"}))
    publish(catalog, {"native": [
        {"key": "open", "maximum": 3},
        {"key": "open", "parameters": {"maximum": None}},
        {"key": "missing", "name": "Only presentation data"}]})
    assert [apply_rookie_maximum(2, catalog.rookie_limit(i)) for i in range(3)] == [3, None, 2]
    assert apply_rookie_maximum(None, catalog.rookie_limit(0)) is None
    for choice in (None, True, -1, 3):
        with pytest.raises(ValueError, match="published rule choice"):
            catalog.rookie_limit(choice)


def test_option_data_is_the_owners_overlay_without_fallback_to_native_parameters():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum", "source": "option_data"},
                                         overlays={"native": {"open": {"maximum": 5}}}))
    publish(catalog, {"native": [{"key": "open", "parameters": {"maximum": "unused"}},
                                {"key": "saved", "parameters": {"maximum": 9}}]})
    assert apply_rookie_maximum(2, catalog.rookie_limit(0)) == 5
    assert apply_rookie_maximum(2, catalog.rookie_limit(1)) == 2


def test_custom_bridge_only_choices_reuse_records_and_never_create_guest_handlers(monkeypatch):
    rule = OWNER + ".policy"
    catalog = CombinedCatalog(definitions({"rule": rule, "parameter": "maximum"}, custom=True))
    publish(catalog, custom={rule: ["capped", "open"]})
    assert catalog.owners == {} and catalog.subscribers == {rule: set()}
    assert apply_rookie_maximum(1, catalog.rookie_limit(0)) == 4
    assert apply_rookie_maximum(1, catalog.rookie_limit(1)) is None
    records = tuple(catalog._records.values())
    monkeypatch.setattr(catalog_module, "encode", lambda _: pytest.fail("Unchanged data was encoded"))
    publish(catalog, custom={rule: None})
    assert tuple(catalog._records.values()) == records
    assert apply_rookie_maximum(1, catalog.rookie_limit(0)) == 4


def test_commit_abort_and_reset_control_value_visibility():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum"}))
    publish(catalog, {"native": [{"key": "open", "maximum": 3}]})
    first = catalog.rookie_limit(0)
    catalog.stage({"native": [{"key": "open", "maximum": 7}]}, {})
    assert not catalog.native_values_ready and catalog.rookie_limit(0) is first
    catalog.abort()
    assert catalog.native_values_ready and catalog.rookie_limit(0) is first
    publish(catalog, {"native": [{"key": "open", "maximum": None}]})
    assert apply_rookie_maximum(1, catalog.rookie_limit(0)) is None
    catalog.reset()
    with pytest.raises(RuntimeError, match="not published"):
        catalog.rookie_limit(0)
    publish(catalog, {"native": [{"key": "open"}]})
    assert apply_rookie_maximum(2, catalog.rookie_limit(0)) == 2


def test_invalid_published_maximum_never_becomes_a_native_or_unlimited_value():
    catalog = CombinedCatalog(definitions({"rule": "native", "parameter": "maximum"}))
    with pytest.raises(ValueError, match="positive integer or null"):
        catalog.stage({"native": [{"key": "open", "maximum": -1}]}, {})
    catalog.abort()
    with pytest.raises(RuntimeError, match="not published"):
        catalog.rookie_limit(0)


def test_dispatch_uses_its_existing_epoch_and_generation_for_scalar_preparation():
    # Runtime installation remains gated. This private seam prepares values for
    # native setup; it is not a transport request on each game query.
    dispatch = ValidationDispatch(definitions({"value": 4}), {}, epoch=7)
    assert not dispatch.ready
    dispatch.publish()
    assert dispatch.rookie_limit(epoch=7, generation=1).value == 4
    dispatch.publish()
    for epoch, generation in ((7, 1), (8, 2)):
        with pytest.raises(ValueError, match="Stale"):
            dispatch.rookie_limit(epoch=epoch, generation=generation)
    dispatch.reset(8)
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.rookie_limit(epoch=8, generation=2)
    dispatch.publish()
    assert dispatch.rookie_limit(epoch=8, generation=3).value == 4


def test_late_host_commit_makes_even_a_literal_only_value_unavailable(monkeypatch):
    dispatch = ValidationDispatch(definitions({"value": 4}), {}, epoch=7)
    clock = iter((0, 0, 2))
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="Late resident publication"):
        dispatch.publish()
    assert not dispatch.ready
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.rookie_limit(epoch=7, generation=1)
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: 0)
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.publish()
    with pytest.raises(RuntimeError, match="cannot revive"):
        dispatch.reset(8)
