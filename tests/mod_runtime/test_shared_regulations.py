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
            group.activate(member.manifest.id)
        binding = SharedHandlerRunner.bind(group, definitions.validators)
        yield group, binding, CombinedCatalog(definitions), calls, cleaned
    assert cleaned == [True]


def reply(group, name, *args, **kwargs):
    result = group.sandbox.call(name, *args, **kwargs)
    value = json.loads(result) if result is not None else None
    if type(value) is dict and "error" in value:
        raise SandboxError(value["error"])
    return value


def publish(group, catalog):
    views = catalog.stage({"native": [{"key": "open", "points": 25}]}, {})
    plan = PolicyInstall(views, 1, 1)
    with group.sandbox.phase(fuel=plan.fuel, seconds=5 * len(views)):
        for name, args, state in plan.operations:
            value = reply(group, name, *args)
            if state is not None:
                assert value == {"epoch": 1, "generation": 1, "state": state}
        assert reply(group, "commit-regulation-policy", 1, 1)["state"] == "active"
    catalog.commit()


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


def evaluate(group, catalog, action, ctx, *, count=1, extra_context=False, repeat_owner=False):
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
            packet = json.loads(frame.for_owner(view, calls, f"{view.revision}:1:1", shared={})[0])
            text = encode(packet)
            assert reply(group, "begin-regulation-action", owner, action, 1, 1, len(text.encode()), text) == {
                "action": action, "prepared": len(calls)}
            results[owner] = [reply(group, "validate-resident-regulation", action, slot, fuel=5_000_000)["result"]
                              for slot in range(len(calls))]
        if repeat_owner:
            # Multiple calls belong in one packet; a second packet cannot
            # bypass that mod's per-action call limit.
            reply(group, "begin-regulation-action", owner, action, 1, 1, len(text.encode()), text)
        frame.seal()
        # No result leaves this harness before the group checks completion.
        reply(group, "finish-regulation-action", action, fuel=64_000_000)
    return results


def test_split_context_transfer_keeps_fresh_arguments_across_owner_calls(runner_bundle):
    with shared(runner_bundle, count=2) as (group, _, catalog, calls, _):
        publish(group, catalog)
        for action in (1, 2):
            assert evaluate(group, catalog, action, context(points=25), count=2) == {
                owner: [{"decision": "allow"}] * 2 for owner in OWNERS}
        assert calls[-4:] == [(owner, str(index)) for owner in OWNERS for index in (3, 4)]


def test_shared_binding_rejects_a_peer_function_with_the_same_local_filename(runner_bundle):
    with pytest.raises(SandboxError, match="b.rules.*owned synchronous"):
        with shared(runner_bundle, peer_handler=True):
            pytest.fail("A peer re-export must not be admitted as this owner's handler")


@pytest.mark.parametrize("failure", ["handler", "unused-context", "repeat-owner"])
def test_failed_shared_action_cannot_leave_other_residents_available(runner_bundle, failure):
    with shared(runner_bundle) as (group, _, catalog, _, cleaned):
        publish(group, catalog)
        ctx = context(points=25, **({"raise": OWNERS[1]} if failure == "handler" else {}))
        with pytest.raises(SandboxError):
            evaluate(group, catalog, 1, ctx, extra_context=failure == "unused-context", repeat_owner=failure == "repeat-owner")
        assert all(member.closed for member in group.members.values()) and cleaned == [True]


@pytest.mark.parametrize("replacement", [False, True])
def test_guest_requires_a_nonempty_manifest_and_reset_before_replacement(runner_bundle, replacement):
    with shared(runner_bundle) as (group, _, catalog, _, cleaned):
        if replacement:
            publish(group, catalog)
        with pytest.raises(SandboxError):
            reply(group, "begin-regulation-policy", 1, 2 if replacement else 1, 1 if replacement else 0)
        assert group.closed and cleaned == [True]
