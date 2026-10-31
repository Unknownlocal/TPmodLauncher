# SPDX-License-Identifier: MPL-2.0
"""Check guest rejection of repeated calls and invalid policy transfers."""

import json
from contextlib import contextmanager

import pytest

from launcher.regulations.regulation_handler_bindings import RegulationHandlerBindings
from launcher.regulations.validation.action import PreparedAction
from launcher.regulations.validation.catalog import CombinedCatalog
from launcher.regulations.validation.install import PolicyInstall
from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import encode
from tests.regulation_support import definitions
from tests.regulation_data import contract_context as context


OWNERS = ("a.rules", "b.rules")


@contextmanager
def shared(make_mods, *, peer_handler=False):
    cleaned = []
    rules = {owner: "def validate(c,r): return {'decision':'allow'}\n" for owner in OWNERS}
    if peer_handler:
        rules[OWNERS[1]] = "import wit_world\nvalidate = wit_world.import_mod('a.rules','rules').validate\n"
    mods = make_mods(rules, cleanup=lambda: cleaned.append(True))
    model = definitions({owner: mod.snapshot for owner, mod in mods.items()})
    with next(iter(mods.values())).runner as group:
        RegulationHandlerBindings.bind(group, model.validators)
        yield group, CombinedCatalog(model), cleaned
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


def evaluate(group, catalog, fault):
    """Send invalid packets directly so host checks cannot hide guest failures."""
    action, ctx = 1, context(points=25)
    selection = {"rule": "native", "evaluation_season": 2026, "selected": "current",
                 "current": [0, 2026], "upcoming": None}
    frame = PreparedAction([ctx], [selection])
    contexts = encode([ctx, ctx] if fault == "extra-context" else [ctx])
    with group.sandbox.phase(fuel=128_000_000, seconds=1):
        assert reply(group, "begin-regulation-contexts", action, contexts) == {
            "action": action, "contexts": 1}
        for owner, view in catalog.views.items():
            calls = [[next(iter(view.validators)), 0, 0]]
            text = frame.for_owner(view, calls, f"{view.revision}:1:1")[0]
            if fault == "repeat-validator":
                # Bypass host preflight to check the guest's once-per-validator fence.
                packet = json.loads(text)
                packet["calls"] *= 2
                text = encode(packet)
            assert reply(group, "begin-regulation-action", owner, action, 1, 1, len(text.encode()), text) == {
                "action": action, "prepared": len(calls)}
            reply(group, "validate-resident-regulation", action, 0, fuel=5_000_000)
        if fault == "repeat-owner":
            # Multiple calls belong in one packet; a second packet cannot
            # bypass that mod's per-action call limit.
            reply(group, "begin-regulation-action", owner, action, 1, 1, len(text.encode()), text)


def test_shared_binding_rejects_a_peer_function_with_the_same_local_filename(make_mods):
    with pytest.raises(SandboxError, match="b.rules.*owned synchronous"):
        with shared(make_mods, peer_handler=True):
            pytest.fail("A peer re-export must not be admitted as this owner's handler")


@pytest.mark.parametrize("failure", ["extra-context", "repeat-validator", "repeat-owner"])
def test_failed_shared_action_cannot_leave_other_residents_available(make_mods, failure):
    with shared(make_mods) as (group, catalog, cleaned):
        publish(group, catalog)
        with pytest.raises(SandboxError):
            evaluate(group, catalog, failure)
        assert group.closed and cleaned == [True]


@pytest.mark.parametrize("replacement", [False, True])
def test_guest_requires_a_nonempty_manifest_and_reset_before_replacement(make_mods, replacement):
    with shared(make_mods) as (group, catalog, cleaned):
        if replacement:
            publish(group, catalog)
        with pytest.raises(SandboxError):
            reply(group, "begin-regulation-policy", 1, 2 if replacement else 1, 1 if replacement else 0)
        assert group.closed and cleaned == [True]
