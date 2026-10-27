# SPDX-License-Identifier: MPL-2.0
"""Private native switches, admission and publication fences; no game needed."""

import json

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.validation.dispatch import ValidationDispatch
from launcher.regulations.validation import dispatch as dispatch_module
from mod_runtime.source import SourceManifest, SourceSnapshot

OWNER = "example.rules"
ROOKIE = "rookie_contract_length_check"
PRESTIGE = "staff_prestige_check"


def source(owner=OWNER):
    # Admission must not import author Python on the host.
    text = "raise AssertionError('author code imported during admission')"
    manifest = SourceManifest(owner, "1.0.0", "0.4.0", "rules", frozenset({"regulations"}))
    return SourceSnapshot(manifest, (("rules.py", text),), len(text))


def document(bridges):
    return {"categories": [{"key": "native", "options": [{"key": "open"}]}],
            "mod_extensions": {OWNER: {"schema": 1, "native_rules": bridges}}}


def prepare(data, sources=None):
    return RegulationDefinitions.from_bytes(json.dumps(data).encode(),
        {OWNER: source()} if sources is None else sources, scopes=set())


@pytest.mark.parametrize("name", [ROOKIE, PRESTIGE])
def test_switches_keep_false_and_change_revisions_without_rule_dependencies(name):
    absent = prepare(document({}))
    assert absent.native_rules == {}
    revisions = []
    for enabled in (True, False):
        result = prepare(document({name: enabled}))
        assert result.native_rules == {OWNER: {name: enabled}}
        assert result._catalog_inputs()[1][OWNER].rule_keys() == set()
        outward = result.native_rules
        outward[OWNER].clear()
        assert result.native_rules[OWNER][name] is enabled
        revisions.append(result.revisions)
    assert revisions[0] != revisions[1]


@pytest.mark.parametrize("name,value", [(ROOKIE, None), (PRESTIGE, 0), (ROOKIE, {"value": 3})])
def test_switches_require_exact_booleans(name, value):
    with pytest.raises(ValueError, match="must be a boolean"):
        prepare(document({name: value}))


@pytest.mark.parametrize("name", ["rookie_contract_max_years", "arbitrary.native.target"])
def test_scalar_replacements_and_arbitrary_targets_are_not_declarations(name):
    with pytest.raises(ValueError, match="declaration fields"):
        prepare(document({name: {"value": 3}}))


@pytest.mark.parametrize("name", [ROOKIE, PRESTIGE])
def test_conflicts_are_per_switch_and_disabled_owners_do_not_reserve_it(name):
    data = document({name: False})
    data["mod_extensions"]["other.rules"] = {"schema": 1, "native_rules": {name: False}}
    sources = {OWNER: source(), "other.rules": source("other.rules")}
    with pytest.raises(ValueError, match=f"other.rules.*Conflicting native rule {name}.*{OWNER}"):
        prepare(data, sources)
    assert prepare(data).owner_ids == (OWNER,)
    other = PRESTIGE if name == ROOKIE else ROOKIE
    data["mod_extensions"]["other.rules"]["native_rules"] = {other: False}
    assert len(prepare(data, sources).native_rules) == 2


def test_switch_preparation_uses_existing_policy_identity_and_requires_republication():
    expected = {ROOKIE: False, PRESTIGE: False}
    dispatch = ValidationDispatch(prepare(document(expected)), epoch=7)
    assert not dispatch.ready
    dispatch.publish()
    values = dispatch.native_rules(epoch=7, generation=1)
    assert values == expected
    values.clear()
    assert dispatch.native_rules(epoch=7, generation=1) == expected
    dispatch.reset(8)
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.native_rules(epoch=8, generation=1)
    dispatch.publish()
    for epoch, generation in ((7, 1), (8, 1)):
        with pytest.raises(ValueError, match="Stale"):
            dispatch.native_rules(epoch=epoch, generation=generation)
    assert dispatch.native_rules(epoch=8, generation=2) == expected


def test_late_publication_fences_switch_only_owners_without_guest_handlers(monkeypatch):
    dispatch = ValidationDispatch(prepare(document({ROOKIE: False})), epoch=7)
    clock = iter((0, 0, 2))
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="Late resident publication"):
        dispatch.publish()
    assert not dispatch.ready
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.native_rules(epoch=7, generation=1)
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: 0)
    with pytest.raises(RuntimeError, match="unavailable"):
        dispatch.publish()
    with pytest.raises(RuntimeError, match="cannot revive"):
        dispatch.reset(8)


def test_native_switch_reference_is_owned_boolean_data_and_keeps_nested_views_private():
    category = OWNER + ".length"
    binding = {"rule": category, "parameter": "native_check"}
    data = document({ROOKIE: binding})
    section = data["mod_extensions"][OWNER]
    section["categories"] = [{"key": category, "label": "Rookie length", "default": "native", "options": [
        {"id": 1, "key": "native", "name": "Native", "parameters": {"native_check": True}},
        {"id": 2, "key": "off", "name": "Off", "parameters": {"native_check": False}}]}]
    model = prepare(data)
    outward = model.native_rules
    outward[OWNER][ROOKIE]["parameter"] = "changed"
    assert model.native_rules[OWNER][ROOKIE] == binding
    dispatch = ValidationDispatch(model)
    dispatch.publish()
    values = dispatch.native_rules(epoch=1, generation=1)
    values[ROOKIE]["rule"] = "changed"
    assert dispatch.native_rules(epoch=1, generation=1)[ROOKIE] == binding
    binding["rule"] = "other.mod.length"
    with pytest.raises(ValueError, match="owned category"):
        prepare(data)
    binding["rule"] = category
    section["categories"][0]["options"][1]["parameters"]["native_check"] = 0
    with pytest.raises(ValueError, match="boolean parameter"):
        prepare(data)
