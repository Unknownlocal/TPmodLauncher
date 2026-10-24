# SPDX-License-Identifier: MPL-2.0
"""Private bridge admission; no game process or guest compilation is needed."""

import json
from unittest.mock import Mock

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.rookie_contract_limit import RookieContractLimit
from launcher.regulations.rule_parameter import RuleParameter
from launcher.regulations.runtime import RegulationRuntime
from launcher.shared_source_mod import SharedSourceMod
from mod_runtime.source import SourceManifest, SourceSnapshot

OWNER = "example.rules"
ROOKIE = "rookie_contract_max_years"
PRESTIGE = "staff_prestige_check"


def source(owner=OWNER):
    # API 0.4.0 remains private. Importing this source on the host would fail.
    text = "raise AssertionError('author code imported during admission')"
    manifest = SourceManifest(owner, "1.0.0", "0.4.0", "rules", frozenset({"regulations"}))
    return SourceSnapshot(manifest, (("rules.py", text),), len(text))


def document(bridges):
    return {"categories": [{"key": "native", "options": [{"key": "open"}]}],
            "mod_extensions": {OWNER: {"schema": 1, "native_rules": bridges}}}


def prepare(data, sources=None):
    return RegulationDefinitions.from_bytes(json.dumps(data).encode(),
        {OWNER: source()} if sources is None else sources, scopes=set())


def test_literal_models_keep_missing_unlimited_and_capped_declarations_distinct():
    absent = prepare(document({}))
    assert absent.native_rules == {} and "native_rules" not in absent.owners[OWNER]
    for value in (None, 3):
        result = prepare(document({ROOKIE: {"value": value}}))
        assert result.native_rules == {OWNER: {ROOKIE: RookieContractLimit(value)}}
        assert result.owners[OWNER]["native_rules"] == {ROOKIE: {"value": value}}
        assert result.document == absent.document  # These are not native JSON fields.
        bindings = result.native_rules
        bindings[OWNER].clear()
        assert result.native_rules[OWNER][ROOKIE].value == value


def test_rule_models_admit_references_without_importing_code():
    data = document({ROOKIE: {"rule": "native", "parameter": "Maximum years"}})
    result = prepare(data)
    assert result.native_rules[OWNER] == {
        ROOKIE: RookieContractLimit(RuleParameter("native", "Maximum years"))}
    # Missing optional parameters are admitted; resolution belongs to publication.
    reference = data["mod_extensions"][OWNER]["native_rules"][ROOKIE]
    reference["source"] = "option_data"
    reference["parameter"] = "max_years"
    extra = prepare(data)
    assert extra.native_rules[OWNER][ROOKIE].value.source == "option_data"
    outward = extra.owners
    outward[OWNER]["native_rules"][ROOKIE]["source"] = "changed"
    assert extra.native_rules[OWNER][ROOKIE].value.source == "option_data"


def test_staff_prestige_switch_keeps_false_and_changes_revision_without_rule_dependencies():
    absent = prepare(document({}))
    revisions = []
    for enabled in (True, False):
        result = prepare(document({PRESTIGE: enabled}))
        assert result.native_rules == {OWNER: {PRESTIGE: enabled}}
        assert result.owners[OWNER]["native_rules"] == {PRESTIGE: enabled}
        assert result.document == absent.document
        assert result._catalog_inputs()[1][OWNER].rule_keys() == set()
        revisions.append(result.revisions)
    assert revisions[0] != revisions[1]


@pytest.mark.parametrize("value", [0, None, {"handler": "rules.prestige"}])
def test_staff_prestige_switch_requires_a_boolean(value):
    with pytest.raises(ValueError, match="must be a boolean"):
        prepare(document({PRESTIGE: value}))


@pytest.mark.parametrize("value", [True, 2.0, "2", 0])
def test_literal_rookie_maximum_requires_a_positive_exact_integer_or_null(value):
    with pytest.raises(ValueError, match="positive integer or null"):
        prepare(document({ROOKIE: {"value": value}}))


@pytest.mark.parametrize("bridges, message", [
    ({"arbitrary.native.target": {"value": 3}}, "declaration fields"),
    ({ROOKIE: {"value": 3, "rule": "native", "parameter": "max_years"}}, "declaration fields"),
    ({ROOKIE: {"rule": "native", "parameter": "max_years", "source": "state"}}, "parameter source"),
    ({"staff_required_prestige": {"handler": "rules.prestige"}}, "declaration fields"),
])
def test_bridge_structure_and_owned_references_are_closed(bridges, message):
    with pytest.raises(ValueError, match=message):
        prepare(document(bridges))


@pytest.mark.parametrize("name, declaration", [(ROOKIE, {"value": None}), (PRESTIGE, False)])
def test_conflicts_are_rejected_per_bridge_and_disabled_owners_do_not_reserve_it(name, declaration):
    data = document({name: declaration})
    data["mod_extensions"]["other.rules"] = {"schema": 1, "native_rules": {name: declaration}}
    with pytest.raises(ValueError, match=f"other.rules.*Conflicting native rule {name}.*{OWNER}"):
        prepare(data, {OWNER: source(), "other.rules": source("other.rules")})
    assert prepare(data).owner_ids == (OWNER,)
    # Different bridges can have different owners; there is no pack-wide winner.
    other = PRESTIGE if name == ROOKIE else ROOKIE
    data["mod_extensions"]["other.rules"]["native_rules"] = {
        other: False if other == PRESTIGE else {"value": 2}}
    assert len(prepare(data, {OWNER: source(), "other.rules": source("other.rules")}).native_rules) == 2


def test_bridge_only_references_participate_in_revisions_and_payload_limits():
    data = document({ROOKIE: {"rule": "native", "parameter": "max_years"}})
    before = prepare(data).revisions
    data["categories"][0]["options"][0]["parameters"] = {"max_years": 3}
    after = prepare(data).revisions
    assert before != after
    data["categories"][0]["options"][0]["parameters"] = {"text": "x" * 4096}
    with pytest.raises(ValueError, match="bytes"):
        prepare(data)


@pytest.mark.parametrize("declaration", [{ROOKIE: {"value": 3}}, {PRESTIGE: False}])
def test_bridge_binding_cannot_be_reported_ready_before_execution_support_exists(declaration):
    definitions = prepare(document(declaration))
    mod = SharedSourceMod.__new__(SharedSourceMod)
    mod.runner = Mock()
    runtime = RegulationRuntime(definitions, {OWNER: mod})
    with pytest.raises(ValueError, match="not available for binding"):
        runtime.bind()
    assert runtime.status == {"ready": False, "owners": {OWNER: "unavailable"}}
    assert runtime._server is None
    mod.runner.close.assert_called_once_with()
