# SPDX-License-Identifier: MPL-2.0
"""Production host catalog/dispatch; existing guest tests own raw protocol cases."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.shared_handler_runner import SharedHandlerRunner
from launcher.regulations.publication import PolicyPublication
from hook_manager.game_adapters.regulations.protocol import regulation_policy_value
from launcher.regulations.validation import catalog as catalog_module
from launcher.regulations.validation import action as action_module
from launcher.regulations.validation import dispatch as dispatch_module
from launcher.regulations.validation.catalog import CombinedCatalog
from launcher.regulations.validation.dispatch import ValidationDispatch
from mod_runtime._regulations import values
from mod_runtime.source import SourceManifest, SourceSnapshot
from regulation_support import context, make_mod, validator


def definitions(sources, *, sections=None, fallback=()):
    data = {"categories": [{"key": "native", "options": [{"key": "open", "parameters": {}}]}],
            "mod_extensions": sections or {owner: {"schema": 1, "validators": [validator()],
                                                   "option_data": {"native": {"open": {"owner": owner}}}}
                                            for owner in sources}}
    return RegulationDefinitions.from_bytes(json.dumps(data).encode(), sources, scopes={"personnel.contract"},
                                            native_categories=fallback)


def dispatch(mods):
    model = definitions({owner: mod.snapshot for owner, mod in mods.items()})
    handlers = {owner: SharedHandlerRunner.bind(mods[owner].runner, {owner: rows}) for owner, rows in model.validators.items()}
    return ValidationDispatch(model, handlers)


def selection(index=0):
    return {"rule": "native", "evaluation_season": 2026, "selected": "current",
            "current": [index, 2026], "upcoming": None}


def test_policy_pages_require_the_closed_membership_plan_before_publication():
    owner = "owned.rules"
    source = SourceSnapshot(SourceManifest(owner, "1", "0.4.0", "rules", frozenset({"regulations"})),
                            (("rules.py", "# inert"),), 7)
    catalog = CombinedCatalog(definitions({owner: source}))
    published = []
    dispatch = SimpleNamespace(catalog=catalog, generation=0, publish=lambda **data: published.append(data))
    options = [{"key": "open", "points": [25]}, {"key": "open", "points": [30]}]
    metrics = [regulation_policy_value("native", option)[1:] for option in options]
    message = {"id": 1, "epoch": 1, "deadline": 123.0, "generations": {owner: 1},
               "plan": [["native", "native", 2, sum(row[0] for row in metrics), sum(row[1] for row in metrics)]]}
    policy = PolicyPublication(dispatch, message)
    policy.append({**message, "page": 0, "rows": [["native", 0, options[0]]]})
    with pytest.raises(ValueError, match="Incomplete"):
        policy.seal(dispatch, message)
    assert not published
    policy.append({**message, "page": 1, "rows": [["native", 1, options[1]]]})
    policy.seal(dispatch, message)
    assert published[0]["native"]["native"] == [regulation_policy_value("native", option)[0] for option in options]
    assert published[0]["deadline"] == message["deadline"]
    policy = PolicyPublication(dispatch, message)
    with pytest.raises(ValueError, match="reordered"):
        policy.append({**message, "page": 0, "rows": [["native", 1, options[0]]]})
    short = {**message, "plan": [["native", "native", 2, 2, 2]]}
    with pytest.raises(ValueError, match="declared"):
        PolicyPublication(dispatch, short).append({**short, "page": 0, "rows": [["native", 0, options[0]]]})


def test_catalog_borrows_admission_and_projects_shared_effective_native_data_once(monkeypatch):
    owners = ("a.rules", "b.rules")
    sources = {owner: SourceSnapshot(SourceManifest(owner, "1", "0.4.0", "rules", frozenset({"regulations"})),
                                     (("rules.py", "# inert"),), 7) for owner in owners}
    sections = {owner: {"schema": 1, "validators": [validator(), {**validator("fallback"), "rule": "fallback"}],
                        "option_data": {"native": {"open": {"owner": owner}}}} for owner in owners}
    sections[owners[0]]["categories"] = [{"key": "a.rules.private", "label": "Private", "default": "open",
                                         "options": [{"id": 1, "key": "open", "name": "Open", "parameters": {"custom": [1]}}]}]
    sections[owners[0]]["validators"].append({**validator("private"), "rule": "a.rules.private"})
    model = definitions(sources, sections=sections, fallback=[{"key": "fallback", "options": [{"key": "open"}]}])
    catalog = CombinedCatalog(model)
    assert "fallback" not in {row["key"] for row in model.native_document["categories"]}
    encoded = []
    original = catalog_module.encode
    monkeypatch.setattr(catalog_module, "encode", lambda value: (encoded.append(value), original(value))[1])
    native = [{"key": "open", "id": 9, "name": "Display", "points": [25], "parameters": {"opaque": {"x": True}}},
              {"key": "open", "points": [30]}, {"key": "synthesized", "points": [35]}]
    views = catalog.stage({"native": native, "fallback": [{"key": "open", "weight": 7}]}, {"a.rules.private": ["open"]})
    first = [row for row in views[owners[0]].choices if row[0] == "native"]
    second = [row for row in views[owners[1]].choices if row[0] == "native"]
    assert len(first) == 3 and all(a[2] is b[2] for a, b in zip(first, second))
    assert first[0][2].value == {"points": [25], "opaque": {"x": True}}
    assert first[0][3] is first[1][3] and first[0][3] is not second[0][3]
    assert sum("points" in value for value in encoded) == 3
    private = next(row[2] for row in views[owners[0]].choices if row[0] == "a.rules.private")
    assert private.value is model._owners[owners[0]].categories[0]["options"][0]["parameters"]
    native[0]["points"].append(999)
    assert first[0][2].value["points"] == [25]
    catalog.commit()
    with pytest.raises(ValueError, match="collide"):
        catalog.stage({"native": [{"key": "open", "points": 1, "parameters": {"points": 1}}]}, {})
    with pytest.raises(ValueError, match="custom"):
        catalog.stage({"a.rules.private": [{"key": "open", "parameters": {"custom": "replacement"}}]}, {})


def test_validation_dispatch_warm_actions_use_references_and_independent_arguments(make_mod, monkeypatch):
    mods = {}
    for owner in ("a.rules", "b.rules"):
        mods[owner] = make_mod(f"""retained=None
def validate(c,r):
 global retained
 assert r['current']['parameters']['points']==[c['proposal']['expect']]
 assert r['current']['option_data']['owner']=={owner!r}
 if retained:
  retained[0]['proposal']['leak']=True
  retained[0]['proposal']['empty'].append(999)
  retained[1]['current']['parameters']['points'].append(999)
 assert 'leak' not in c['proposal']
 assert c['proposal']['empty']==[]
 retained=(c,r)
 return {{'decision':'allow'}}
""", owner=owner)
    session = dispatch(mods)
    traffic = []
    for owner, mod in mods.items():
        original = mod.sandbox.call
        def call(name, *args, _owner=owner, _original=original, **kwargs):
            traffic.append((_owner, name))
            return _original(name, *args, **kwargs)
        monkeypatch.setattr(mod.sandbox, "call", call)
    native = [{"key": "open", "points": [value]} for value in (25, 30, 35)]
    session.publish(native={"native": native})
    assert session.ready
    assert max(i for i, (_, name) in enumerate(traffic) if name == "seal-regulation-policy") < min(
        i for i, (_, name) in enumerate(traffic) if name == "commit-regulation-policy")
    encode = values.encode
    def no_payload_encode(value):
        def check(item):
            if type(item) is dict:
                assert "points" not in item
                for child in item.values():
                    check(child)
            elif type(item) is list:
                for child in item:
                    check(child)
        check(value)
        return encode(value)
    monkeypatch.setattr(values, "encode", no_payload_encode)
    monkeypatch.setattr(catalog_module, "encode", no_payload_encode)
    monkeypatch.setattr(action_module, "encode", no_payload_encode)
    traffic.clear()
    session.publish(native={"native": None})
    assert sum(name == "begin-regulation-policy" for _, name in traffic) == 2
    assert not any("record" in name for _, name in traffic)
    traffic.clear()
    ctx = context()
    ctx["proposal"] = {"expect": 35, "empty": []}
    requests = {owner: [["check", 0, 0]] * 32 for owner in mods}
    assert session.evaluate([ctx], [selection(2)], requests) == {owner: [{"decision": "allow"}] * 32 for owner in mods}
    ctx["proposal"]["expect"] = 25
    assert session.evaluate([ctx], [selection()], {owner: [["check", 0, 0]] for owner in mods}) == {
        owner: [{"decision": "allow"}] for owner in mods}
    assert not any("policy" in name or "record" in name for _, name in traffic)


def test_host_composed_limits_cover_chunked_actions_before_author_execution(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    session = dispatch({mod.manifest.id: mod})
    session.publish(native={"native": [{"key": "open"}]})
    revision = session.catalog.views[mod.manifest.id].revision + ":1:1"
    rule = {"schema": 1, "key": "native", "revision": revision, "evaluation_season": 2026, "selected": "current",
            "current": {"key": "open", "effective_season": 2026, "parameters": {}, "option_data": {"owner": mod.manifest.id}}, "upcoming": None}
    ctx = context()
    ctx["proposal"]["padding"] = ""
    ctx["proposal"]["padding"] = "x" * (4096 - values.measure({"context": ctx, "regulation": rule})[0])
    assert session.evaluate([ctx], [selection()], {mod.manifest.id: [["check", 0, 0]] * 8})[mod.manifest.id] == [{"decision": "allow"}] * 8
    ctx = context()
    ctx["proposal"]["values"] = []
    ctx["proposal"]["values"] = [{} for _ in range(1024 - values.measure({"context": ctx, "regulation": rule})[1])]
    contexts = [deepcopy(ctx) for _ in range(16)]
    assert len(session.evaluate(contexts, [selection()], {mod.manifest.id: [["check", i, 0] for i in range(16)]})[mod.manifest.id]) == 16
    ctx["proposal"]["values"].append({})
    with pytest.raises(ValueError, match="limits"):
        session.evaluate([ctx], [selection()], {mod.manifest.id: [["check", 0, 0]]})
    assert mod.sandbox.closed and not session.ready


def test_control_replacement_counts_retirement_and_reset_preserves_bindings(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    session = dispatch({mod.manifest.id: mod})
    # Many adopted payloads share a key, then a tiny publication retires them.
    session.publish(native={"native": [{"key": "open", "values": [{} for _ in range(950)], "marker": index} for index in range(320)]})
    # Repeated actions must not spend their bounded preparation/handler fuel
    # collecting the large, unchanged resident payload graph.
    for _ in range(4):
        assert session.evaluate([context()], [selection()], {mod.manifest.id: [["check", 0, 0]] * 32})[mod.manifest.id] == [{"decision": "allow"}] * 32
    previous_ids = {record.identity for record in session.catalog.views[mod.manifest.id].records}
    session.publish(native={"native": [{"key": "open", "p": "x" * 4088}]})
    assert any(record.metric[0] == 4096 for record in session.catalog.views[mod.manifest.id].records)
    session.publish(native={"native": [{"key": "open"}]})
    assert session.evaluate([context()], [selection()], {mod.manifest.id: [["check", 0, 0]]})[mod.manifest.id] == [{"decision": "allow"}]
    action = session.action
    session.reset(2)
    assert not session.ready and not mod.sandbox.closed
    session.publish(native={"native": [{"key": "open"}]})
    assert session.evaluate([context()], [selection()], {mod.manifest.id: [["check", 0, 0]]})[mod.manifest.id] == [{"decision": "allow"}]
    assert session.action > action and not previous_ids & {record.identity for record in session.catalog.views[mod.manifest.id].records}


def test_partial_multi_owner_commit_cannot_publish_or_revive_old_policy(make_mod, monkeypatch):
    mods = {owner: make_mod("def validate(c,r): return {'decision':'allow'}\n", owner=owner) for owner in ("a.rules", "b.rules")}
    model = definitions({owner: mod.snapshot for owner, mod in mods.items()})
    handlers = {owner: SharedHandlerRunner.bind(mods[owner].runner, {owner: rows}) for owner, rows in model.validators.items()}
    with pytest.raises(ValueError, match="integer"):
        ValidationDispatch(model, handlers, epoch=0)
    session = ValidationDispatch(model, dict(reversed(list(handlers.items()))))
    assert list(session.runners) == list(model.owner_ids)
    session.publish(native={"native": [{"key": "open"}]})
    before = session.catalog.views
    def failed_commit(*args):
        raise RuntimeError("injected commit failure")
    monkeypatch.setattr(session.runners["b.rules"], "commit", failed_commit)
    with pytest.raises(RuntimeError, match="injected"):
        session.publish(native={"native": [{"key": "open", "changed": True}]})
    assert session.catalog.views is before and not session.ready
    assert session.states == {owner: "unavailable" for owner in mods}
    assert all(mod.sandbox.closed for mod in mods.values())
    with pytest.raises(RuntimeError, match="revive"):
        session.reset(2)


@pytest.mark.parametrize("operation", ["publication", "reset"])
def test_late_host_cleanup_cannot_acknowledge_control_operation(make_mod, monkeypatch, operation):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    session = dispatch({mod.manifest.id: mod})
    if operation == "reset":
        session.publish(native={"native": [{"key": "open"}]})
    original = session.catalog.commit if operation == "publication" else session.catalog.reset
    late = dispatch_module.time.monotonic() + 1000
    def commit_after_deadline():
        original()
        # Advance only the dispatch clock after actual guest work has finished.
        # Host index retirement is part of the original publication deadline too.
        monkeypatch.setattr(dispatch_module, "time", SimpleNamespace(monotonic=lambda: late))
    monkeypatch.setattr(session.catalog, "commit" if operation == "publication" else "reset", commit_after_deadline)
    with pytest.raises(RuntimeError, match="Late"):
        if operation == "publication":
            session.publish(native={"native": [{"key": "open"}]})
        else:
            session.reset(2)
    assert not session.ready and mod.sandbox.closed
    assert session.states[mod.manifest.id] == "unavailable"


def test_execution_failure_keeps_other_owners_available_and_replies_are_correlated(make_mod, monkeypatch):
    mods = {"a.rules": make_mod("def validate(c,r):\n while True: pass\n", owner="a.rules"),
            "b.rules": make_mod("def validate(c,r): return {'decision':'allow'}\n", owner="b.rules")}
    session = dispatch(mods)
    session.publish(native={"native": [{"key": "open"}]})
    with pytest.raises(Exception, match="fuel"):
        session.evaluate([context()], [selection()], {owner: [["check", 0, 0]] for owner in mods})
    assert session.states == {"a.rules": "unavailable", "b.rules": "active"}
    assert session.evaluate([context()], [selection()], {"b.rules": [["check", 0, 0]]})["b.rules"] == [{"decision": "allow"}]
    original = mods["b.rules"].sandbox.call
    def foreign(name, *args, **kwargs):
        raw = original(name, *args, **kwargs)
        if name == "validate-resident-regulation":
            value = json.loads(raw)
            value["action"] += 1
            return json.dumps(value)
        return raw
    monkeypatch.setattr(mods["b.rules"].sandbox, "call", foreign)
    with pytest.raises(Exception, match="Foreign"):
        session.evaluate([context()], [selection()], {"b.rules": [["check", 0, 0]]})
    assert session.states["b.rules"] == "unavailable" and mods["b.rules"].sandbox.closed
