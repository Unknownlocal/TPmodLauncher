# SPDX-License-Identifier: MPL-2.0
"""Production dispatch through one shared runner with distinct mod identities."""

from contextlib import ExitStack
import json
from types import SimpleNamespace

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.shared_handler_runner import SharedHandlerRunner
from launcher.regulations.validation import group as group_module
from launcher.regulations.validation.dispatch import ValidationDispatch
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, SourceSnapshot
from regulation_support import context, selection, validator


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
def make_shared_dispatch(runner_bundle):
    with ExitStack() as stack:
        def create(*, count=1):
            calls, cleaned, sources = [], [], {}
            for owner in ("a.rules", "b.rules", "c.rules"):
                manifest = SourceManifest(owner, "1", "0.4.0", "mod", frozenset({"regulations", "diagnostics"}))
                files = (("mod.py", ENTRY), ("rules.py", CODE.format(owner=owner)))
                sources[owner] = SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files))
            providers = {owner: {"report": lambda text, owner=owner: calls.append((owner, text))} for owner in sources}
            group = stack.enter_context(SharedPythonRunner(runner_bundle, tuple(sources.values()), providers,
                                                           cleanup=lambda: cleaned.append(True)))
            for member in group.members.values():
                group.activate(member.manifest.id)
            sections = {owner: {"schema": 1, "validators": [validator(f"v{i:02}") for i in range(count if owner != "b.rules" else 1)],
                               "option_data": {"native": {"open": {"owner": owner}}}} for owner in sources}
            private = "a.rules.private"
            sections["a.rules"]["categories"] = [{"key": private, "label": "Private", "default": "open",
                "options": [{"id": 1, "key": "open", "name": "Open", "parameters": {"custom": [7]}}]}]
            sections["a.rules"]["validators"].append({**validator("private"), "rule": private})
            sections["a.rules"]["option_data"][private] = {"open": {"owner": "a.rules"}}
            data = {"categories": [{"key": "native", "options": [{"key": "open"}]}], "mod_extensions": sections}
            model = RegulationDefinitions.from_bytes(json.dumps(data).encode(), sources, scopes={"personnel.contract"})
            binding = SharedHandlerRunner.bind(group, model.validators)
            session = ValidationDispatch(model, binding)
            return session, group, calls, cleaned, model, binding
        yield create


def proposal(**extra):
    value = context()
    value["proposal"] = {"points": [25], **extra}
    return value


def publish(session):
    return session.publish(native={"native": [{"key": "open", "points": [25]}]}, custom={"a.rules.private": ["open"]})


def test_shared_dispatch_reuses_records_and_contexts_in_owner_order_through_actions_and_career_reset(make_shared_dispatch, monkeypatch):
    session, group, calls, _, model, handler = make_shared_dispatch(count=20)
    traffic = []
    original = group.sandbox.call
    def record(name, *args, **kwargs):
        traffic.append((name, args))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(group.sandbox, "call", record)
    publish(session)
    assert session.ready
    assert session.group.active.operations == []  # Sent JSON fragments must not remain resident.
    with pytest.raises(ValueError, match="already belongs"):
        ValidationDispatch(model, handler)
    records = [value for name, args in traffic if name == "install-regulation-records" for _, value in json.loads(args[0])]
    assert sum("points" in value for value in records) == 1
    manifest = json.loads("".join(args[0] for name, args in traffic if name == "put-regulation-policy"))
    native_slots = {base for view in manifest["owners"] for rule, _, base, _ in view["choices"]
                    if view["rules"][rule] == "native"}
    assert len(native_slots) == 1
    assert len(manifest["records"]) == len({row["id"] for row in manifest["records"]})
    assert max(i for i, (name, _) in enumerate(traffic) if name == "seal-regulation-policy") < min(
        i for i, (name, _) in enumerate(traffic) if name == "commit-regulation-policy")
    traffic.clear()
    requests = {owner: [[f"v{i:02}", 0, 0] for i in range(20 if owner != "b.rules" else 1)] for owner in session.states}
    requests["a.rules"].insert(0, ["private", 0, 1])
    private_selection = {**selection(), "rule": "a.rules.private"}
    result = session.evaluate([proposal(deny="c.rules")], [selection(), private_selection], requests)
    assert len(result["a.rules"]) == 21 and result["b.rules"] == [{"decision": "allow"}]
    assert result["c.rules"] == [{"decision": "deny", "message": "c.rules"}] * 20
    assert calls == [("a.rules", "a.rules.private")] + [("a.rules", "native")] * 20 + [("b.rules", "native")] + [("c.rules", "native")] * 20
    assert sum(name == "begin-regulation-contexts" for name, _ in traffic) == 1
    assert all(json.loads(args[-1])["contexts"] == [0] for name, args in traffic if name == "begin-regulation-action")
    action = session.action
    traffic.clear()
    session.reset(2)
    assert sum(name == "reset-regulation-group" for name, _ in traffic) == 1
    assert not session.ready and not group.closed
    publish(session)
    assert session.evaluate([proposal()], [selection()], {"c.rules": [["v00", 0, 0]]}) == {"c.rules": [{"decision": "allow"}]}
    assert session.action > action and session.ready


def test_failed_shared_publication_fences_all_peers_and_preserves_host_view(make_shared_dispatch, monkeypatch):
    session, group, _, cleaned, _, _ = make_shared_dispatch()
    before = session.catalog.views
    original = group.sandbox.call
    def fail(name, *args, **kwargs):
        raw = original(name, *args, **kwargs)
        if name == "commit-regulation-policy":
            value = json.loads(raw)
            value["generation"] += 1
            return json.dumps(value)
        return raw
    monkeypatch.setattr(group.sandbox, "call", fail)
    with pytest.raises(Exception, match="acknowledgement"):
        publish(session)
    assert session.catalog.views is before and not session.ready and group.closed and cleaned == [True]
    assert set(session.states.values()) == {"unavailable"}
    with pytest.raises(RuntimeError, match="revive"):
        session.reset(2)


@pytest.mark.parametrize("failure", ["handler", "context-reply", "handler-reply", "completion-reply", "multiple-proposals", "duplicate-validator"])
def test_failed_shared_execution_closes_all_members_without_returning_partial_approval(make_shared_dispatch, monkeypatch, failure):
    session, group, calls, cleaned, _, _ = make_shared_dispatch()
    publish(session)
    original = group.sandbox.call
    def fail(name, *args, **kwargs):
        raw = original(name, *args, **kwargs)
        if failure == "context-reply" and name == "begin-regulation-contexts":
            value = json.loads(raw)
            value["action"] += 1
            return json.dumps(value)
        if failure == "handler-reply" and name == "validate-resident-regulation":
            value = json.loads(raw)
            value["action"] += 1
            return json.dumps(value)
        if failure == "completion-reply" and name == "finish-regulation-action":
            return "unexpected completion result"
        return raw
    monkeypatch.setattr(group.sandbox, "call", fail)
    with pytest.raises(Exception):
        ctx = proposal(error="c.rules" if failure == "handler" else "")
        session.evaluate([ctx, ctx] if failure == "multiple-proposals" else [ctx], [selection()],
                         {owner: [["v00", 0, 0]] * (2 if failure == "duplicate-validator" else 1)
                          for owner in session.states})
    assert group.closed and cleaned == [True] and not session.ready
    assert set(session.states.values()) == {"unavailable"}
    if failure in ("multiple-proposals", "duplicate-validator"):
        assert not calls  # Invalid shapes must fail before any mod runs.
    if failure in ("handler", "completion-reply"):
        assert calls[:2] == [("a.rules", "native"), ("b.rules", "native")]
    with pytest.raises(RuntimeError, match="unavailable"):
        session.evaluate([proposal()], [selection()], {"b.rules": [["v00", 0, 0]]})


def test_shared_completion_keeps_original_deadline_and_rejects_late_cleanup(make_shared_dispatch, monkeypatch):
    session, group, _, cleaned, _, _ = make_shared_dispatch()
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


@pytest.mark.parametrize("invalid", ["partial", "unchanged", "repeat"])
def test_only_a_complete_initial_policy_can_be_installed(make_shared_dispatch, invalid):
    session, group, _, cleaned, _, _ = make_shared_dispatch()
    if invalid == "repeat":
        publish(session)
    before = session.catalog.views
    with pytest.raises(ValueError):
        if invalid == "repeat":
            publish(session)
        else:
            session.publish(native={"native": None if invalid == "unchanged" else [{"key": "open"}]},
                            custom={"a.rules.private": ["open"]} if invalid == "unchanged" else {})
    assert session.catalog.views is before and not session.ready
    assert group.closed and cleaned == [True]
