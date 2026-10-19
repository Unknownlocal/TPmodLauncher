# SPDX-License-Identifier: MPL-2.0
"""Real resident guest protocol; each test owns a distinct lifecycle/resource proof."""

from copy import deepcopy
import json

import pytest

from launcher.regulations.handler_runner import HandlerRunner
from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.values import measure
from mod_runtime._regulations import values as values
from mod_runtime._regulations.action import RegulationAction
from mod_runtime._regulations.catalog import RegulationCatalog
from regulation_support import context, make_mod, validator
from resident_support import evaluate, install, packet, policy, reply


def bind(mod):
    HandlerRunner.bind(mod.runner, [validator()])


def test_resident_choices_reuse_and_retained_mutations_are_isolated_between_stores(make_mod):
    code = """retained=None
def validate(c,r):
 global retained
 assert r['current']['key']=='open'
 assert r['current']['option_data']['owner']==c['proposal']['owner']
 assert r['current']['parameters']['points'][0]==c['proposal']['points']
 if retained:
  retained[0]['proposal']['leak']=True
  retained[1]['current']['parameters']['leak']=True
 assert 'leak' not in c['proposal'] and 'leak' not in r['current']['parameters']
 retained=(c,r)
 return {'decision':'allow'}
"""
    for index in range(2):
        mod = make_mod(code, owner=f"owned.o{index}")
        bind(mod)
        manifest, records = policy(mod.manifest.id)
        install(mod, manifest, records)
        ctx = context()
        ctx["proposal"] = {"owner": mod.manifest.id, "points": 35}
        # Three adopted choices share one option key; choice slots disambiguate
        # their payloads. Exercise eight groups, then reuse payloads without puts.
        request = packet(manifest, 32, ctx, selected=2)
        assert evaluate(mod, request, 1) == [{"decision": "allow"}] * 32
        install(mod, manifest, records, generation=2, reuse=True)
        ctx["proposal"]["points"] = 25
        assert evaluate(mod, packet(manifest, 4, ctx), 2, generation=2) == [{"decision": "allow"}] * 4
        install(mod, manifest, records, generation=3, metadata_only=True)
        ctx["proposal"]["points"] = 30
        assert evaluate(mod, packet(manifest, ctx=ctx, selected=1), 3, generation=3) == [{"decision": "allow"}]


def test_career_reset_and_explicit_abort_discard_partial_actions_but_preserve_bindings(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    bind(mod)
    manifest, records = policy(mod.manifest.id)
    install(mod, manifest, records)
    text = encode(packet(manifest, 8))
    with mod.sandbox.phase(fuel=64_000_000, seconds=.1):
        assert reply(mod, "begin-regulation-action", 1, 1, 1, len(text.encode()), text)["prepared"] == 4
        reply(mod, "discard-regulation-action", 1)
    assert evaluate(mod, packet(manifest), 2) == [{"decision": "allow"}]
    reply(mod, "reset-regulation-career", 2)
    install(mod, manifest, records, epoch=2)
    assert evaluate(mod, packet(manifest), 3, epoch=2) == [{"decision": "allow"}]
    # Old epoch references poison the owner rather than revive retained policy.
    result = json.loads(mod.sandbox.call("begin-regulation-action", 4, 1, 1, len(text.encode()), text))
    assert "error" in result
    assert "error" in json.loads(mod.sandbox.call("reset-regulation-career", 3))


def test_exact_expanded_limits_survive_chunking_and_fail_before_author_execution(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    bind(mod)
    manifest, records = policy(mod.manifest.id, [{}])
    install(mod, manifest, records)
    rule = {"schema": 1, "key": "native", "revision": manifest["revision"] + ":1:1", "evaluation_season": 2026,
            "selected": "current", "current": {"key": "open", "effective_season": 2026,
            "parameters": {}, "option_data": records[-1]}, "upcoming": None}
    ctx = context()
    ctx["proposal"]["padding"] = ""
    ctx["proposal"]["padding"] = "x" * (4096 - measure({"context": ctx, "regulation": rule})[0])
    assert evaluate(mod, packet(manifest, 4, ctx), 1) == [{"decision": "allow"}] * 4
    ctx = context()
    ctx["proposal"]["values"] = []
    missing = 1024 - measure({"context": ctx, "regulation": rule})[1]
    ctx["proposal"]["values"] = [{} for _ in range(missing)]
    distinct = packet(manifest, 16, ctx)
    distinct["contexts"] = [deepcopy(ctx) for _ in range(16)]
    for index, item in enumerate(distinct["contexts"]):
        item["clock"]["absolute_week"] = index
    distinct["calls"] = [[0, index, 0] for index in range(16)]
    assert evaluate(mod, distinct, 2) == [{"decision": "allow"}] * 16
    oversized = deepcopy(ctx)
    oversized["proposal"]["values"].append({})
    ctx = context()
    nested = {}
    for _ in range(4):
        nested = {"x": nested}
    ctx["proposal"]["nested"] = nested
    assert measure({"context": ctx, "regulation": rule})[2] == 8
    assert evaluate(mod, packet(manifest, ctx=ctx), 3) == [{"decision": "allow"}]
    text = encode(packet(manifest, ctx=oversized))
    with mod.sandbox.phase(fuel=64_000_000, seconds=.1):
        assert "error" in json.loads(mod.sandbox.call("begin-regulation-action", 4, 1, 1, len(text.encode()), text))


def test_uncommitted_policy_fences_old_generation_and_failed_owner_cannot_be_revived(make_mod):
    mod = make_mod("def validate(c,r): raise AssertionError('must not run')\n")
    bind(mod)
    manifest, records = policy(mod.manifest.id)
    install(mod, manifest, records)
    install(mod, manifest, records, generation=2, reuse=True, commit=False)
    text = encode(packet(manifest))
    assert "error" in json.loads(mod.sandbox.call("begin-regulation-action", 1, 1, 1, len(text.encode()), text))
    assert "error" in json.loads(mod.sandbox.call("commit-regulation-policy", 1, 2))


def test_bad_transfer_traps_and_ordinary_handler_fuel_still_closes_runaway_owner(make_mod):
    bad = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    bind(bad)
    manifest, records = policy(bad.manifest.id, [{"p": "x" * 4088}])
    assert measure(records[0])[0] == 4096
    install(bad, manifest, records)  # Page wrapper needs the individual record export.
    reply(bad, "begin-regulation-policy", 1, 2, 1)
    with pytest.raises(SandboxError):
        bad.sandbox.call("put-regulation-policy", "too long")
    assert bad.sandbox.closed
    runaway = make_mod("def validate(c,r):\n while True: pass\n")
    bind(runaway)
    manifest, records = policy(runaway.manifest.id)
    install(runaway, manifest, records)
    with pytest.raises(SandboxError, match="fuel"):
        evaluate(runaway, packet(manifest), 1)
    assert runaway.sandbox.closed


def test_reused_components_are_neither_remeasured_nor_encoded_during_action_preparation(monkeypatch):
    # Trusted fixture functions stand in for admitted bindings in this accounting
    # test. Actual author source and private dispatch are tested above in Wasm.
    manifest, records = policy("owned.rules")
    handlers = [(lambda c, r: {"decision": "allow"}, "personnel.contract", "native")]
    catalog = RegulationCatalog(manifest, handlers, None)
    for slot, value in enumerate(records):
        catalog.install(slot, value)
    catalog.seal()
    original = values.encode

    def encoding(value):
        assert type(value) is not dict or "points" not in value
        return original(value)

    monkeypatch.setattr(values, "encode", encoding)
    reused = RegulationCatalog(manifest, handlers, catalog)
    reused.seal()
    assert reused.records[0] is catalog.records[0]
    action = RegulationAction(packet(manifest, 8), reused, "policy")
    for _ in range(2):
        for slot in range(4):
            function, ctx, rule = action.take(slot)
            assert function(ctx, rule) == {"decision": "allow"}
        if not action.finished:
            action.prepare()
    with pytest.raises(ValueError, match="slot"):
        action.take(0)
