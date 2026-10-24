# SPDX-License-Identifier: MPL-2.0
"""Shared guest regulation storage; launcher dispatch owns end-to-end acceptance."""

import gc
import json
from contextlib import contextmanager

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.shared_handler_runner import SharedHandlerRunner
from launcher.regulations.validation.action import PreparedAction
from launcher.regulations.validation.catalog import CombinedCatalog
from launcher.regulations.validation.install import PolicyInstall
from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.contexts import RegulationContexts
from mod_runtime.runtime import _chunks
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, SourceSnapshot


OWNERS = ("a.rules", "b.rules")
ENTRY = "class WitWorld:\n def activate(self): pass\n def on_event(self,n,p): raise AssertionError('not lifecycle dispatch')\n"
RULES = '''import wit_world
OWNER = {owner!r}
count = 0
def validate(c,r):
 global count
 assert r['current']['option_data']['owner'] == OWNER
 assert r['current']['parameters']['points'] == c['proposal']['points']
 assert 'changed' not in c['proposal'] and 'changed' not in r['current']['parameters']
 c['proposal']['changed'] = r['current']['parameters']['changed'] = True
 count += 1
 wit_world.report(str(count))
 if c['proposal'].get('raise') == OWNER: raise ValueError('deliberate handler failure')
 if c['proposal'].get('deny') == OWNER: return {{'decision':'deny','message':OWNER}}
 return {{'decision':'allow'}}
'''


@contextmanager
def shared(runner_bundle, *, count=1, peer_handler=False):
    sources, calls, cleaned = {}, [], []
    for owner in OWNERS:
        manifest = SourceManifest(owner, "1", "0.4.0", "mod", frozenset({"regulations", "diagnostics"}))
        code = RULES.format(owner=owner)
        if peer_handler and owner == OWNERS[1]:
            code = "import wit_world\nvalidate = wit_world.import_mod('a.rules','rules').validate\n"
        files = (("mod.py", ENTRY), ("rules.py", code))
        sources[owner] = SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files))
    providers = {owner: {"report": lambda text, owner=owner: calls.append((owner, text))} for owner in OWNERS}
    data = {"categories": [{"key": "native", "options": [{"key": "open"}]}],
            "mod_extensions": {owner: {"schema": 1,
                "validators": [{"id": f"v{index:02}", "rule": "native", "scope": "personnel.contract",
                                "handler": "rules.validate"} for index in range(count)],
                "option_data": {"native": {"open": {"owner": owner}}}} for owner in OWNERS}}
    definitions = RegulationDefinitions.from_bytes(encode(data).encode(), sources, scopes={"personnel.contract"})
    with SharedPythonRunner(runner_bundle, tuple(sources.values()), providers,
                            cleanup=lambda: cleaned.append(True)) as group:
        for member in group.members.values():
            member.activate()
        binding = SharedHandlerRunner.bind(group, definitions.validators)
        yield group, binding, CombinedCatalog(definitions), calls, cleaned
    assert cleaned == [True]


def reply(group, name, *args, **kwargs):
    result = group.sandbox.call(name, *args, **kwargs)
    value = json.loads(result) if result is not None else None
    if type(value) is dict and "error" in value:
        raise SandboxError(value["error"])
    return value


def publish(group, catalog, *, points=25, epoch=1, generation=1, unchanged=False):
    previous = dict(catalog.views)
    known = {record.identity for view in previous.values() for record in view.records}
    views = catalog.stage({"native": None if unchanged else [{"key": "open", "points": points}]}, {})
    plans, incoming = {}, 0
    for owner, view in views.items():
        plan = plans[owner] = PolicyInstall(view, previous.get(owner), epoch, generation, known=known)
        incoming += len({record.identity for record in view.records} - known)
        with group.sandbox.phase(fuel=plan.fuel, seconds=5):
            reply(group, "select-regulation-owner", owner)
            for name, args, state in plan.operations:
                value = reply(group, name, *args)
                if state is not None:
                    assert value == {"epoch": epoch, "generation": generation, "state": state}
        known.update(record.identity for record in view.records)
    for owner, plan in plans.items():
        with group.sandbox.phase(fuel=plan.commit_fuel, seconds=5):
            reply(group, "select-regulation-owner", owner)
            assert reply(group, "commit-regulation-policy", epoch, generation)["state"] == "active"
    with group.sandbox.phase(fuel=64_000_000, seconds=5):
        reply(group, "finish-regulation-publication")
    catalog.commit()
    return incoming


def context(**proposal):
    return {"schema": 1, "scope": "personnel.contract", "operation": "commit", "initiator": "player",
            "clock": {"season": 2026, "absolute_week": 4},
            "effective": {"season": 2026, "start_week": 4, "end_week": None}, "proposal": proposal}


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


def evaluate(group, catalog, action, ctx, *, epoch=1, generation=1, count=1, extra_context=False):
    """Exercise private pages; production dispatch separately owns its 100 ms limit."""
    selection = {"rule": "native", "evaluation_season": 2026, "selected": "current",
                 "current": [0, 2026], "upcoming": None}
    frame = PreparedAction([ctx], [selection])
    contexts = encode([ctx, ctx] if extra_context else [ctx])
    # Split even this small table to exercise partial transfer and explicit seal.
    split = len(contexts) // 2
    results = {}
    with group.sandbox.phase(fuel=128_000_000, seconds=1):
        assert reply(group, "begin-regulation-contexts", action, len(contexts.encode()), contexts[:split]) == {
            "action": action, "contexts": 0}
        reply(group, "put-regulation-contexts", contexts[split:])
        assert reply(group, "seal-regulation-contexts")["contexts"] == (2 if extra_context else 1)
        for owner, view in catalog.views.items():
            calls = [[validator, 0, 0] for validator in list(view.validators)[:count]]
            packet = json.loads(frame.for_owner(view, calls, f"{view.revision}:{epoch}:{generation}")[0])
            packet["contexts"] = [0]  # The table itself crossed the boundary once.
            chunks = list(_chunks(encode(packet).encode()))
            reply(group, "select-regulation-owner", owner)
            prepared = reply(group, "begin-regulation-action", action, epoch, generation,
                             len(encode(packet).encode()), chunks[0])
            for chunk in chunks[1:]:
                reply(group, "put-regulation-action", chunk)
            if len(chunks) > 1:
                prepared = reply(group, "seal-regulation-action")
            rows, remaining = [], len(calls)
            while remaining:
                size = min(4, remaining)
                assert prepared == {"action": action, "prepared": size}
                for slot in range(size):
                    rows.append(reply(group, "validate-resident-regulation", action, slot, fuel=5_000_000)["result"])
                remaining -= size
                if remaining:
                    prepared = reply(group, "prepare-regulation-slots", action, fuel=64_000_000)
            results[owner] = rows
        frame.seal()
        # No result leaves this harness before the group checks completion.
        reply(group, "finish-regulation-action", action, fuel=64_000_000)
    return results


def test_shared_records_contexts_and_more_than_32_bindings_survive_updates_and_reset(runner_bundle):
    with shared(runner_bundle, count=20) as (group, binding, catalog, calls, _):
        assert sum(map(len, binding.validators.values())) == 40
        # One common native payload plus two different owner overlays, not four.
        assert publish(group, catalog) == 3
        assert evaluate(group, catalog, 1, context(points=25), count=20) == {
            owner: [{"decision": "allow"}] * 20 for owner in OWNERS}
        assert calls == [(owner, str(index)) for owner in OWNERS for index in range(1, 21)]
        assert publish(group, catalog, points=30, generation=2) == 1
        result = evaluate(group, catalog, 2, context(points=30, deny=OWNERS[1]), generation=2)
        assert result == {OWNERS[0]: [{"decision": "allow"}], OWNERS[1]: [{"decision": "deny", "message": OWNERS[1]}]}
        assert publish(group, catalog, generation=3, unchanged=True) == 0
        with group.sandbox.phase(fuel=64_000_000, seconds=5):
            assert reply(group, "reset-regulation-group", 2) == {"epoch": 2, "generation": 0, "state": "reset"}
        catalog.reset()
        assert publish(group, catalog, points=35, epoch=2) == 3
        assert all(rows == [{"decision": "allow"}] for rows in evaluate(group, catalog, 3, context(points=35), epoch=2).values())
        assert calls[-2:] == [(owner, "22") for owner in OWNERS]


def test_shared_binding_rejects_a_peer_function_with_the_same_local_filename(runner_bundle):
    with pytest.raises(SandboxError, match="b.rules.*owned synchronous"):
        with shared(runner_bundle, peer_handler=True):
            pytest.fail("A peer re-export must not be admitted as this owner's handler")


@pytest.mark.parametrize("failure", ["handler", "unused-context"])
def test_failed_shared_action_cannot_leave_other_residents_available(runner_bundle, failure):
    with shared(runner_bundle) as (group, _, catalog, _, cleaned):
        publish(group, catalog)
        ctx = context(points=25, **({"raise": OWNERS[1]} if failure == "handler" else {}))
        with pytest.raises(SandboxError):
            evaluate(group, catalog, 1, ctx, extra_context=failure == "unused-context")
        # Ordinary author exceptions first return a resident error. Even if a
        # caller ignores it, selecting the other owner traps the poisoned group.
        if not group.closed:
            with pytest.raises(SandboxError):
                reply(group, "select-regulation-owner", OWNERS[0])
        assert all(member.closed for member in group.members.values()) and cleaned == [True]
