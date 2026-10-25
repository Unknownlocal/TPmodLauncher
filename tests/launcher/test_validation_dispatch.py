# SPDX-License-Identifier: MPL-2.0
"""Production host catalog/dispatch; existing guest tests own raw protocol cases."""

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


def dispatch(mods, *, count=1):
    sections = {owner: {"schema": 1,
                       "validators": [validator("check" if i == 0 else f"check{i:02}") for i in range(count)],
                       "option_data": {"native": {"open": {"owner": owner}}}} for owner in mods}
    model = definitions({owner: mod.snapshot for owner, mod in mods.items()}, sections=sections)
    handler = SharedHandlerRunner.bind(next(iter(mods.values())).runner, model.validators)
    return ValidationDispatch(model, handler)


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
    encoded = []
    original = catalog_module.encode
    monkeypatch.setattr(catalog_module, "encode", lambda value: (encoded.append(value), original(value))[1])
    native = [{"key": "open", "id": 9, "name": "Display", "points": [25], "parameters": {"opaque": {"x": True}}},
              {"key": "open", "points": [30]}, {"key": "synthesized", "points": [35]}]
    views = catalog.stage({"native": native, "fallback": [{"key": "open", "weight": 7}]}, {"a.rules.private": ["open"]})
    first = [row for row in views[owners[0]].choices if row[0] == "native"]
    second = [row for row in views[owners[1]].choices if row[0] == "native"]
    assert len(first) == 3 and all(a[2] is b[2] for a, b in zip(first, second))
    assert json.loads(first[0][2].text) == {"points": [25], "opaque": {"x": True}}
    assert first[0][3] is first[1][3] and first[0][3] is not second[0][3]
    assert sum("points" in value for value in encoded) == 3
    private = next(row[2] for row in views[owners[0]].choices if row[0] == "a.rules.private")
    assert json.loads(private.text) == {"custom": [1]}
    native[0]["points"].append(999)
    assert json.loads(first[0][2].text)["points"] == [25]
    catalog.commit()
    catalog.reset()
    with pytest.raises(ValueError, match="collide"):
        catalog.stage({"native": [{"key": "open", "points": 1, "parameters": {"points": 1}}],
                       "fallback": [{"key": "open"}]}, {"a.rules.private": ["open"]})
    with pytest.raises(ValueError, match="custom"):
        catalog.stage({"native": [{"key": "open"}], "fallback": [{"key": "open"}],
                       "a.rules.private": [{"key": "open"}]}, {})


def test_validation_dispatch_warm_actions_use_references_and_independent_arguments(make_mods, monkeypatch):
    mods = make_mods({owner: f"""retained=None
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
""" for owner in ("a.rules", "b.rules")})
    session = dispatch(mods, count=32)
    traffic = []
    sandbox = next(iter(mods.values())).sandbox
    original = sandbox.call
    def call(name, *args, **kwargs):
        traffic.append(name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(sandbox, "call", call)
    native = [{"key": "open", "points": [value]} for value in (25, 30, 35)]
    session.publish(native={"native": native})
    assert session.ready
    assert max(i for i, name in enumerate(traffic) if name == "seal-regulation-policy") < min(
        i for i, name in enumerate(traffic) if name == "commit-regulation-policy")
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
    ctx = context()
    ctx["proposal"] = {"expect": 35, "empty": []}
    requests = {owner: [["check" if i == 0 else f"check{i:02}", 0, 0] for i in range(32)] for owner in mods}
    assert session.evaluate([ctx], [selection(2)], requests) == {owner: [{"decision": "allow"}] * 32 for owner in mods}
    ctx["proposal"]["expect"] = 25
    assert session.evaluate([ctx], [selection()], {owner: [["check", 0, 0]] for owner in mods}) == {
        owner: [{"decision": "allow"}] for owner in mods}
    assert not any("policy" in name or "record" in name for name in traffic)


def test_host_composed_limits_apply_before_author_execution(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    session = dispatch({mod.manifest.id: mod})
    session.publish(native={"native": [{"key": "open"}]})
    revision = session.catalog.views[mod.manifest.id].revision + ":1:1"
    rule = {"schema": 1, "key": "native", "revision": revision, "evaluation_season": 2026, "selected": "current",
            "current": {"key": "open", "effective_season": 2026, "parameters": {}, "option_data": {"owner": mod.manifest.id}}, "upcoming": None}
    ctx = context()
    ctx["proposal"]["padding"] = ""
    ctx["proposal"]["padding"] = "x" * (4096 - values.measure({"context": ctx, "regulation": rule})[0])
    assert session.evaluate([ctx], [selection()], {mod.manifest.id: [["check", 0, 0]]})[mod.manifest.id] == [{"decision": "allow"}]
    ctx = context()
    ctx["proposal"]["values"] = []
    ctx["proposal"]["values"] = [{} for _ in range(1024 - values.measure({"context": ctx, "regulation": rule})[1])]
    assert session.evaluate([ctx], [selection()], {mod.manifest.id: [["check", 0, 0]]})[mod.manifest.id] == [{"decision": "allow"}]
    ctx["proposal"]["values"].append({})
    with pytest.raises(ValueError, match="limits"):
        session.evaluate([ctx], [selection()], {mod.manifest.id: [["check", 0, 0]]})
    assert mod.sandbox.closed and not session.ready


def test_career_reset_releases_dense_policy_and_preserves_bindings(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    session = dispatch({mod.manifest.id: mod}, count=32)
    # Cover dense cached data and a record whose page wrapper needs chunking.
    choices = [{"key": "open", "values": [{} for _ in range(950)], "marker": index} for index in range(320)]
    session.publish(native={"native": choices + [{"key": "open", "p": "x" * 4088}]})
    assert any(record.metric[0] == 4096 for record in session.catalog.views[mod.manifest.id].records)
    # Repeated actions must not spend their bounded preparation/handler fuel
    # collecting the large, unchanged resident payload graph.
    for _ in range(4):
        assert session.evaluate([context()], [selection()], {mod.manifest.id: [["check" if i == 0 else f"check{i:02}", 0, 0] for i in range(32)]})[mod.manifest.id] == [{"decision": "allow"}] * 32
    previous_ids = {record.identity for record in session.catalog.views[mod.manifest.id].records}
    action = session.action
    session.reset(2)
    assert not session.ready and not mod.sandbox.closed
    session.publish(native={"native": [{"key": "open"}]})
    assert session.evaluate([context()], [selection()], {mod.manifest.id: [["check", 0, 0]]})[mod.manifest.id] == [{"decision": "allow"}]
    assert session.action > action and not previous_ids & {record.identity for record in session.catalog.views[mod.manifest.id].records}



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
