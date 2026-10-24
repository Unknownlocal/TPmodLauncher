# SPDX-License-Identifier: MPL-2.0
"""Shared production dispatch, including interleaved individual Store execution."""

from contextlib import ExitStack
import json
from types import SimpleNamespace

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.handler_runner import HandlerRunner
from launcher.regulations.shared_handler_runner import SharedHandlerRunner
from launcher.regulations.validation import group as group_module
from launcher.regulations.validation.dispatch import ValidationDispatch
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, SourceSnapshot
from regulation_support import context, make_mod, selection, validator


CODE = '''import wit_world
OWNER = {owner!r}
retained = None
def validate(c,r):
 global retained
 assert r['current']['option_data']['owner'] == OWNER
 if r['key'] == 'native': assert r['current']['parameters']['points'] == c['proposal']['points']
 else: assert r['current']['parameters']['custom'] == [7]
 if retained: retained['proposal']['leak'] = True
 assert 'leak' not in c['proposal']
 retained = c
 wit_world.report(r['key'])
 if c['proposal'].get('error') == OWNER: raise ValueError('deliberate handler error')
 if c['proposal'].get('deny') == OWNER: return {{'decision':'deny','message':OWNER}}
 return {{'decision':'allow'}}
'''
ENTRY = "class WitWorld:\n def activate(self): pass\n def on_event(self,n,p): raise AssertionError('private dispatch only')\n"


@pytest.fixture
def make_shared_dispatch(runner_bundle, make_mod):
    with ExitStack() as stack:
        def create(*, count=1):
            calls, cleaned, sources = [], [], {}
            for owner in ("a.rules", "c.rules"):
                manifest = SourceManifest(owner, "1", "0.4.0", "mod", frozenset({"regulations", "diagnostics"}))
                files = (("mod.py", ENTRY), ("rules.py", CODE.format(owner=owner)))
                sources[owner] = SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files))
            providers = {owner: {"report": lambda text, owner=owner: calls.append((owner, text))} for owner in sources}
            group = stack.enter_context(SharedPythonRunner(runner_bundle, tuple(sources.values()), providers,
                                                           cleanup=lambda: cleaned.append(True)))
            for member in group.members.values():
                member.activate()
            individual = make_mod(CODE.format(owner="b.rules"), owner="b.rules", report=lambda owner, text: calls.append((owner, text)))
            sources["b.rules"] = individual.snapshot
            sections = {owner: {"schema": 1, "validators": [validator(f"v{i:02}") for i in range(count if owner != "b.rules" else 1)],
                               "option_data": {"native": {"open": {"owner": owner}}}} for owner in sources}
            private = "a.rules.private"
            sections["a.rules"]["categories"] = [{"key": private, "label": "Private", "default": "open",
                "options": [{"id": 1, "key": "open", "name": "Open", "parameters": {"custom": [7]}}]}]
            sections["a.rules"]["validators"].append({**validator("private"), "rule": private})
            sections["a.rules"]["option_data"][private] = {"open": {"owner": "a.rules"}}
            data = {"categories": [{"key": "native", "options": [{"key": "open"}]}], "mod_extensions": sections}
            model = RegulationDefinitions.from_bytes(json.dumps(data).encode(), sources, scopes={"personnel.contract"})
            binding = SharedHandlerRunner.bind(group, {owner: model.validators[owner] for owner in group.members})
            handlers = {owner: binding for owner in group.members}
            handlers["b.rules"] = HandlerRunner.bind(individual.runner, model.validators["b.rules"])
            session = ValidationDispatch(model, handlers)
            return session, group, individual, calls, cleaned, model, handlers
        yield create


def proposal(**extra):
    value = context()
    value["proposal"] = {"points": [25], **extra}
    return value


def publish(session):
    return session.publish(native={"native": [{"key": "open", "points": [25]}]}, custom={"a.rules.private": ["open"]})


def test_shared_dispatch_reuses_records_and_contexts_in_owner_order_through_updates_and_reset(make_shared_dispatch, monkeypatch):
    session, group, individual, calls, _, model, handlers = make_shared_dispatch(count=20)
    traffic = []
    original = group.sandbox.call
    def record(name, *args, **kwargs):
        traffic.append((name, args))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(group.sandbox, "call", record)
    publish(session)
    assert session.ready
    with pytest.raises(ValueError, match="already belongs"):
        ValidationDispatch(model, handlers)
    records = [value for name, args in traffic if name == "install-regulation-records" for _, value in json.loads(args[0])]
    assert sum("points" in value for value in records) == 1
    assert max(i for i, (name, _) in enumerate(traffic) if name == "seal-regulation-policy") < min(
        i for i, (name, _) in enumerate(traffic) if name == "commit-regulation-policy")
    traffic.clear()
    requests = {owner: [[f"v{i:02}", 0, 0] for i in range(20 if owner != "b.rules" else 1)] for owner in session.runners}
    requests["a.rules"].insert(0, ["private", 0, 1])
    private_selection = {**selection(), "rule": "a.rules.private"}
    result = session.evaluate([proposal(deny="c.rules")], [selection(), private_selection], requests)
    assert len(result["a.rules"]) == 21 and result["b.rules"] == [{"decision": "allow"}]
    assert result["c.rules"] == [{"decision": "deny", "message": "c.rules"}] * 20
    assert calls == [("a.rules", "a.rules.private")] + [("a.rules", "native")] * 20 + [("b.rules", "native")] + [("c.rules", "native")] * 20
    assert sum(name == "begin-regulation-contexts" for name, _ in traffic) == 1
    assert all(json.loads(args[-1])["contexts"] == [0] for name, args in traffic if name == "begin-regulation-action")
    traffic.clear()
    assert session.publish(custom={"a.rules.private": None}) == {"a.rules": 2}
    assert [session.runners[owner].generation for owner in session.runners] == [2, 1, 1]
    assert not any("record" in name for name, _ in traffic)
    first, second = proposal(), proposal()
    second["clock"]["absolute_week"] = 9
    traffic.clear()
    assert session.evaluate([first, second], [selection()], {"b.rules": [["v00", 0, 0]], "c.rules": [["v00", 1, 0]]}) == {
        owner: [{"decision": "allow"}] for owner in ("b.rules", "c.rules")}
    table = next(json.loads(args[-1]) for name, args in traffic if name == "begin-regulation-contexts")
    assert len(table) == 1 and table[0]["clock"]["absolute_week"] == 9
    traffic.clear()
    # Exercise group-cache retirement and reset with enough cached nodes that
    # their cleanup needs the counted control allowance, rather than handler fuel.
    session.publish(native={"native": [{"key": "open", "points": [30], "values": [{} for _ in range(600)], "marker": i}
                                       for i in range(24)]})
    traffic.clear()
    session.publish(native={"native": [{"key": "open", "points": [30]}]})
    assert sum("points" in value for name, args in traffic if name == "install-regulation-records" for _, value in json.loads(args[0])) == 1
    action = session.action
    traffic.clear()
    session.reset(2)
    assert sum(name == "reset-regulation-group" for name, _ in traffic) == 1
    assert not session.ready and not group.closed and not individual.sandbox.closed
    publish(session)
    assert session.evaluate([proposal()], [selection()], {"c.rules": [["v00", 0, 0]]}) == {"c.rules": [{"decision": "allow"}]}
    assert session.action > action and session.ready


def test_failed_shared_publication_fences_unaffected_peers_and_preserves_host_view(make_shared_dispatch, monkeypatch):
    session, group, individual, _, cleaned, _, _ = make_shared_dispatch()
    publish(session)
    before = session.catalog.views
    original = group.sandbox.call
    def fail(name, *args, **kwargs):
        raw = original(name, *args, **kwargs)
        return "invalid acknowledgement" if name == "finish-regulation-publication" else raw
    monkeypatch.setattr(group.sandbox, "call", fail)
    with pytest.raises(Exception, match="void acknowledgement"):
        session.publish(custom={"a.rules.private": None})
    assert session.catalog.views is before and not session.ready and group.closed and cleaned == [True]
    assert session.states == {"a.rules": "unavailable", "b.rules": "active", "c.rules": "unavailable"}
    assert not individual.sandbox.closed
    with pytest.raises(RuntimeError, match="revive"):
        session.reset(2)


@pytest.mark.parametrize("failure", ["handler", "context-reply", "completion-reply"])
def test_failed_shared_execution_closes_all_members_without_returning_partial_approval(make_shared_dispatch, monkeypatch, failure):
    session, group, individual, calls, cleaned, _, _ = make_shared_dispatch()
    publish(session)
    original = group.sandbox.call
    def fail(name, *args, **kwargs):
        raw = original(name, *args, **kwargs)
        if failure == "context-reply" and name == "begin-regulation-contexts":
            value = json.loads(raw)
            value["action"] += 1
            return json.dumps(value)
        if failure == "completion-reply" and name == "finish-regulation-action":
            return "unexpected completion result"
        return raw
    monkeypatch.setattr(group.sandbox, "call", fail)
    with pytest.raises(Exception):
        session.evaluate([proposal(error="c.rules" if failure == "handler" else "")], [selection()],
                         {owner: [["v00", 0, 0]] for owner in session.runners})
    assert group.closed and cleaned == [True] and not session.ready and not individual.sandbox.closed
    assert session.states == {"a.rules": "unavailable", "b.rules": "active", "c.rules": "unavailable"}
    if failure != "context-reply":
        assert calls[:2] == [("a.rules", "native"), ("b.rules", "native")]
    assert session.evaluate([proposal()], [selection()], {"b.rules": [["v00", 0, 0]]}) == {"b.rules": [{"decision": "allow"}]}


def test_shared_completion_keeps_original_deadline_and_rejects_late_cleanup(make_shared_dispatch, monkeypatch):
    session, group, _, _, cleaned, _, _ = make_shared_dispatch()
    publish(session)
    deadlines = []
    phase = group.sandbox.phase
    def bounded_phase(**kwargs):
        deadlines.append(kwargs["deadline"])
        return phase(**kwargs)
    monkeypatch.setattr(group.sandbox, "phase", bounded_phase)
    original = group.sandbox.call
    def late(name, *args, **kwargs):
        raw = original(name, *args, **kwargs)
        if name == "finish-regulation-action":
            # Delay only the coordinator's post-phase clock. Guest execution and
            # watchdog teardown stay real, and the test needs no scheduling sleep.
            monkeypatch.setattr(group_module, "time", SimpleNamespace(monotonic=lambda: deadlines[0] + 1))
        return raw
    monkeypatch.setattr(group.sandbox, "call", late)
    with pytest.raises(Exception, match="Late shared"):
        session.evaluate([proposal()], [selection()], {owner: [["v00", 0, 0]] for owner in ("a.rules", "c.rules")})
    assert len(deadlines) == 4 and len(set(deadlines)) == 1
    assert group.closed and cleaned == [True]
    assert session.states["a.rules"] == session.states["c.rules"] == "unavailable"
