# SPDX-License-Identifier: MPL-2.0
"""Voting executes through real Wasm without changing signing or publishing partial batches."""

from copy import deepcopy
import time

import pytest

from launcher.regulations.regulation_handler_bindings import RegulationHandlerBindings
from launcher.regulations.validation.dispatch import ValidationDispatch
from launcher.regulations.voting_runner import VotingRunner
from mod_runtime._regulations.policy import encode
from tests.regulation_support import context, definitions, selection, validator
from tests.regulation_data import voting_inputs as inputs


def bound(mods, *, validators=None, voting=None):
    runner = next(iter(mods.values())).runner
    return RegulationHandlerBindings.bind(runner, validators or {}, voting=voting or {
        owner: [{"rule": "native", "handler": "rules.vote"}] for owner in mods})


def test_voting_only_and_mixed_owners_share_snapshot_and_coexist_with_signing_and_reset(make_mods, monkeypatch):
    code = '''import wit_world
retained = None
def vote(c,r):
 global retained
 if retained: retained['snapshot']['teams']['A']['cash'] = -1
 assert c['snapshot']['teams']['A']['cash'] == 10
 assert 'leak' not in c['snapshot']['field'] and 'leak' not in r['proposed']
 c['snapshot']['field']['leak'] = True
 r['proposed']['leak'] = True
 retained = c
 wit_world.report(c['team'])
 return {'choice':'proposed' if c['team']=='A' else 'current','reason':c['native']['reason']}
def validate(c,r): return {'decision':'allow'}
'''
    reports = []
    mods = make_mods({"votes.only": code, "mixed.rules": code}, submit_log=lambda owner, text: reports.append((owner, text)))
    handler = bound(mods, validators={"mixed.rules": [validator()]}, voting={
        "votes.only": [{"rule": "custom.vote", "handler": "rules.vote"}],
        "mixed.rules": [{"rule": "native", "handler": "rules.vote"}]})
    votes = VotingRunner(handler)
    snapshot, regulation, contexts = inputs("custom.vote", padding=6000)
    original_data = deepcopy((snapshot, regulation, contexts))
    traffic, original = [], handler.runner.sandbox.call
    def record(name, *args, **kwargs):
        traffic.append((name, args))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(handler.runner.sandbox, "call", record)
    result = votes.evaluate("votes.only", snapshot, regulation, contexts, deadline=time.monotonic() + 5)
    assert result == {team: {"choice": "proposed" if team == "A" else "current", "reason": "Native evaluation"}
                      for team in ("A", "B")}
    assert (snapshot, regulation, contexts) == original_data
    sent = "".join(args[-1] for name, args in traffic if name in ("begin-regulation-vote", "put-regulation-vote"))
    assert sent.count('"voting_model_version"') == 1 and len(sent) > 4096
    model = definitions({"mixed.rules": mods["mixed.rules"].snapshot})
    session = ValidationDispatch(model, handler)
    session.publish(native={"native": [{"key": "open"}]})
    assert session.evaluate([context()], [selection()], {"mixed.rules": [["check", 0, 0]]}) == {
        "mixed.rules": [{"decision": "allow"}]}
    snapshot, regulation, contexts = inputs()
    assert votes.evaluate("mixed.rules", snapshot, regulation, contexts, deadline=time.monotonic() + 5) == result
    session.reset(2)
    session.publish(native={"native": [{"key": "open"}]})
    assert votes.evaluate("votes.only", *inputs("custom.vote"), deadline=time.monotonic() + 5) == result
    assert reports == [(owner, team) for owner in ("votes.only", "mixed.rules", "votes.only") for team in ("A", "B")]
    assert not handler.runner.closed and session.ready


@pytest.mark.parametrize("failure", [
    "return {'choice':'other','reason':'Invalid'}",
    "return {'choice':'current','reason':'é'*1025}",
    "return {'decision':'allow'}",
    "raise ValueError('broken callback')",
    "while True: pass",
])
def test_a_late_bad_vote_or_exhausted_handler_never_returns_an_earlier_result(make_mods, failure):
    reports = []
    code = ("import wit_world\ndef vote(c,r):\n wit_world.report(c['team'])\n"
            " if c['team']=='A': return {'choice':'current','reason':'First valid vote'}\n " + failure)
    mods = make_mods({"votes.only": code}, submit_log=lambda owner, text: reports.append(text))
    handler = bound(mods)
    with pytest.raises(Exception):
        VotingRunner(handler).evaluate("votes.only", *inputs(), deadline=time.monotonic() + 5)
    assert reports == ["A", "B"] and handler.runner.closed


@pytest.mark.parametrize("fault", ["bytes", "nodes", "identity"])
def test_invalid_voting_inputs_are_rejected_before_author_execution(make_mods, fault):
    reports = []
    mods = make_mods({"votes.only": "import wit_world\ndef vote(c,r):\n wit_world.report('called')\n return c['native']"},
                     submit_log=lambda owner, text: reports.append(text))
    handler = bound(mods)
    snapshot, rule, contexts = inputs()
    if fault == "identity":
        contexts[1]["proposal"] = "another-proposal"
    else:
        snapshot["field"] = {"extra": "x" * 65536 if fault == "bytes" else [None] * 8192}
    with pytest.raises(ValueError, match="identities|limits"):
        VotingRunner(handler).evaluate("votes.only", snapshot, rule, contexts, deadline=time.monotonic() + 5)
    assert reports == [] and handler.runner.closed


def test_guest_refuses_to_complete_a_batch_with_unconsumed_votes(make_mods):
    mods = make_mods({"votes.only": "def vote(c,r): return {'choice':'current','reason':'Keep'}"})
    handler = bound(mods)
    snapshot, rule, contexts = inputs()
    packet = encode({"owner": "votes.only", "binding": 0, "snapshot": snapshot, "regulation": rule, "contexts": contexts})
    with pytest.raises(Exception):
        with handler.runner.sandbox.phase(fuel=128_000_000, seconds=5):
            handler.runner.sandbox.call("begin-regulation-vote", 1, len(packet.encode()), packet)
            handler.runner.sandbox.call("finish-regulation-vote", 1)
    assert handler.runner.closed
