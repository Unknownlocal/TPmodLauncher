# SPDX-License-Identifier: MPL-2.0
"""Private definition admission with inert owned source and native-style JSON."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from launcher.pack_mods import PackMods
from launcher.regulations import RegulationDefinitions
from launcher.source_mod import SourceMod
from mod_runtime.source import SourceManifest, SourceSnapshot

SCOPES = {"personnel.contract"}


def source(owner="example.rules", text="raise AssertionError('host imported author code')"):
    # A private conformance fixture for the planned contract. Manifest parsing
    # still rejects public API 0.4.0; no capability is published by this step.
    manifest = SourceManifest(owner, "1.0.0", "0.4.0", "rules", frozenset({"regulations"}))
    return SourceSnapshot(manifest, (("rules.py", text),), len(text.encode()))


def category(key="example.rules.policy"):
    return {"key": key, "label": "Policy", "default": "open", "options": [
        {"id": 1, "key": "open", "name": "Open", "parameters": {"target": "data.only"}},
        {"id": 2, "key": "restricted", "name": "Restricted", "parameters": {"arbitrary": ["value"]}}],
        "neighbours": {"open": ["restricted"], "restricted": ["open"]}}


def document():
    return {"categories": [{"key": "native", "default": "open", "options": [
        {"id": 1, "key": "open", "points": [25, 18]}]}],
        "change_control": {"mode": "voting", "scripted_changes": []},
        "native_extra": {"unchanged": True}, "mod_extensions": {"example.rules": {
            "schema": 1, "categories": [category()],
            "option_data": {"native": {"open": {"anything": [1, "x"]}}},
            "validators": [{"id": "check", "rule": "example.rules.policy",
                            "scope": "personnel.contract", "handler": "rules.validate"}],
            "scripted_changes": [{"key": "example.rules.policy", "new_regulation": {"key": "restricted"}, "effective_season": 2028}]}}}


def prepare(data=None, sources=None, **kwargs):
    return RegulationDefinitions.from_bytes(json.dumps(document() if data is None else data).encode(),
                                 {"example.rules": source()} if sources is None else sources,
                                 scopes=SCOPES, **kwargs)


def test_owned_view_preserves_native_data_and_isolates_copies_and_revisions():
    data = document()
    data["mod_extensions"]["disabled.mod"] = {"target": "ignored", "schema": False}
    before = deepcopy(data)
    result = prepare(data)
    assert data == before
    assert result.native_document == {key: value for key, value in data.items() if key != "mod_extensions"}
    merged = result.document
    assert merged["categories"][0] == data["categories"][0]
    assert merged["change_control"]["mode"] == "voting"
    assert merged["change_control"]["scripted_changes"][0]["id"] == "example.rules:0"
    assert merged["change_control"]["scripted_changes"][0]["new_regulation"] == {"key": "restricted"}
    assert result.owners["example.rules"]["categories"][0]["options"][0]["parameters"] == {"target": "data.only"}
    merged["categories"][1]["options"].clear()
    owned = result.owners
    owned["example.rules"]["option_data"]["native"]["open"]["anything"].clear()
    owned.clear()
    assert result.owners["example.rules"]["option_data"]["native"]["open"]["anything"] == [1, "x"]
    assert result.document["categories"][1]["options"] and result.owners
    assert result.revisions == prepare(data).revisions
    assert result.revisions != prepare(data, {"example.rules": source(text="# changed code")}).revisions
    data["categories"][0]["options"][0]["parameters"] = {"limit": 2}
    assert result.revisions != prepare(data).revisions


def test_native_and_disabled_sections_keep_existing_json_semantics(tmp_path):
    content = b'{"categories":[],"native_extra":{"x":1,"x":2},"mod_extensions":{"off":{"schema":false,"schema":[],"nested":{"x":1,"x":2}}}}'
    result = RegulationDefinitions.from_bytes(content, {"example.rules": source()}, scopes=SCOPES)
    assert result.document == {"categories": [], "native_extra": {"x": 2}} and result.owners == {}
    assert RegulationDefinitions.from_bytes(b'{"mod_extensions":null}', {}, scopes=set()).document == {}
    assert RegulationDefinitions.read(tmp_path / "absent", {}, scopes=set()) is None
    with pytest.raises(ValueError, match="declaration fields"):
        RegulationDefinitions.from_bytes(b'{"mod_extensions":{"example.rules":null}}', {"example.rules": source()}, scopes=SCOPES)
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        RegulationDefinitions.from_bytes(b'{"mod_extensions":{"example.rules":{"schema":1,"schema":1}}}',
                              {"example.rules": source()}, scopes=SCOPES)


def test_enabled_owner_failure_does_not_publish_partial_categories():
    data = document()
    data["mod_extensions"]["z.broken"] = {"schema": False}
    with pytest.raises(ValueError, match="z.broken"):
        prepare(data, {"example.rules": source(), "z.broken": source("z.broken")})
    # The caller's input remains usable; no merged category/script was written.
    assert len(data["categories"]) == 1 and data["change_control"]["scripted_changes"] == []


def test_category_cannot_replace_native_fallback_or_foreign_definition():
    data = document()
    fallback = category()
    with pytest.raises(ValueError, match="collides"):
        prepare(data, native_categories=[fallback])
    data["mod_extensions"]["example.rules"]["categories"][0]["key"] = "another.mod.policy"
    with pytest.raises(ValueError, match="namespace"):
        prepare(data)


@pytest.mark.parametrize("fault", ["option_ids", "neighbours"])
def test_custom_option_identity_and_relationship_integrity(fault):
    data = document()
    row = data["mod_extensions"]["example.rules"]["categories"][0]
    if fault == "option_ids":
        row["options"][1]["id"] = 3
    else:
        row["neighbours"]["open"] = ["missing"]
    with pytest.raises(ValueError, match="consecutive|neighbours"):
        prepare(data)


@pytest.mark.parametrize("fault", ["foreign_module", "unmapped_scope", "binding_directive"])
def test_validators_have_owned_source_and_only_semantic_contexts(fault):
    data = document()
    row = data["mod_extensions"]["example.rules"]["validators"][0]
    if fault == "foreign_module":
        row["handler"] = "sys.validate"
    elif fault == "unmapped_scope":
        row["scope"] = "unmapped.game_area"
    else:
        row["target"] = "arbitrary.native.function"
    with pytest.raises(ValueError, match="source snapshot|trusted mapping|declaration fields"):
        prepare(data)


def test_native_option_data_uses_trusted_defaults_without_rewriting_native_fields():
    data = document()
    section = data["mod_extensions"]["example.rules"]
    section["option_data"] = {"fallback": {"open": {"custom": 9}}}
    section["validators"][0]["rule"] = "fallback"
    fallback = {"key": "fallback", "options": [{"key": "open"}]}
    result = prepare(data, native_categories=[fallback])
    assert result.owners["example.rules"]["option_data"]["fallback"]["open"] == {"custom": 9}
    assert result.native_document["categories"] == data["categories"]
    section["option_data"] = {"foreign.policy": {"open": {}}}
    with pytest.raises(ValueError, match="unknown or foreign"):
        prepare(data, native_categories=[fallback])


def test_scripted_changes_cannot_override_native_category_policy():
    data = document()
    data["mod_extensions"]["example.rules"]["scripted_changes"][0]["key"] = "native"
    with pytest.raises(ValueError, match="owned custom category"):
        prepare(data)


@pytest.mark.parametrize("limit", ["bytes", "nodes", "depth"])
def test_statically_oversized_policy_data_is_rejected(limit):
    data = document()
    payload = {"text": "é" * 2048} if limit == "bytes" else {"values": [0] * 1024}
    if limit == "depth":
        payload = {"a": {"b": {"c": {"d": {"e": {}}}}}}
    data["mod_extensions"]["example.rules"]["categories"][0]["options"][0]["parameters"] = payload
    with pytest.raises(ValueError, match="bytes|node limit|container depth"):
        prepare(data)


@pytest.mark.parametrize("resource", ["categories", "validators"])
def test_session_quotas_cannot_be_split_across_owners(resource):
    data, sources = document(), {}
    data["mod_extensions"] = {}
    for index in range(5 if resource == "categories" else 9):
        owner = f"owner.n{index}"
        sources[owner] = source(owner)
        section = {"schema": 1, resource: []}
        if resource == "categories":
            section[resource] = [category(f"{owner}.rule{number}") for number in range(26)]
        else:
            section[resource] = [{"id": f"v{number}", "rule": "native", "scope": "personnel.contract",
                                  "handler": "rules.validate"} for number in range(29)]
        data["mod_extensions"][owner] = section
    with pytest.raises(ValueError, match="session quota"):
        prepare(data, sources)


def test_file_admission_is_bounded_and_requires_matching_source_identity(tmp_path):
    path = tmp_path / "regulations.json"
    path.write_bytes(json.dumps(document()).encode())
    assert RegulationDefinitions.read(tmp_path, {"example.rules": source()}, scopes=SCOPES).owners
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="byte limit"):
        RegulationDefinitions.read(tmp_path, {"example.rules": source()}, scopes=SCOPES)
    with pytest.raises(ValueError, match="admitted source snapshot"):
        prepare(sources={"another.mod": source()})


def test_pack_preparation_uses_only_live_admitted_source_owners_once(tmp_path):
    data = document()
    data["mod_extensions"]["component.mod"] = None
    data["mod_extensions"]["closed.mod"] = None
    (tmp_path / "regulations.json").write_bytes(json.dumps(data).encode())
    scope = PackMods(tmp_path, lambda _: None)
    scope.finished, scope._pack_root = True, tmp_path
    active = SourceMod.__new__(SourceMod)
    active.snapshot, active.manifest = source(), source().manifest
    active._active, active.sandbox = True, SimpleNamespace(closed=False)
    closed = SourceMod.__new__(SourceMod)
    closed.snapshot, closed.manifest = source("closed.mod"), source("closed.mod").manifest
    closed._active, closed.sandbox = True, SimpleNamespace(closed=True)
    scope.mods = {"example.rules": active, "closed.mod": closed,
                  "component.mod": SimpleNamespace(manifest=source("component.mod").manifest)}
    assert set(scope.prepare_regulations(scopes=SCOPES).owners) == {"example.rules"}
    with pytest.raises(ValueError, match="one preparation"):
        scope.prepare_regulations(scopes=SCOPES)
