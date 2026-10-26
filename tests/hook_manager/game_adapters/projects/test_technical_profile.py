# SPDX-License-Identifier: MPL-2.0
"""Native read isolation and the extra future-chassis freshness fence."""

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallBinding, CallableTarget, refuse_boolean
from hook_manager.manager import HookError
from hook_manager.game_adapters.projects import technical_profile as module
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.project_adapter import ProjectAdapter


@pytest.fixture
def native(monkeypatch):
    row = {"current": "old", "upcoming": "new", "current_severity": "minor",
           "upcoming_severity": "drastic", "announcement_sent": False, "unrelated": object()}
    spec = {"matrix": {"concept": {"attribute": 1.2}}}
    manager = SimpleNamespace(regulation_defs={module._RULE: spec, "unrelated": object()},
                              regulations={module._RULE: row, "unrelated": object()})
    state = SimpleNamespace(regulation_manager=manager, season_no=1)
    manager.state = state
    calls = []

    def read(scratch):
        # This stand-in intentionally normalises and returns nested aliases,
        # like the mapped helpers. Actual native calculations run in the probe.
        assert not hasattr(scratch, "state")
        assert set(scratch.regulation_defs) == set(scratch.regulations) == {module._RULE}
        copied = scratch.regulations[module._RULE]
        assert "unrelated" not in copied
        scratch.regulations[module._RULE] = dict(copied)
        calls.append(copied)
        upcoming = bool(copied["upcoming"] and copied["announcement_sent"])
        return {"key": copied["upcoming" if upcoming else "current"],
                "severity": copied["upcoming_severity" if upcoming else "current_severity"],
                "retention": .7, "concept_strength": 1.4, "upcoming": upcoming,
                "concept_attribute_multipliers": scratch.regulation_defs[module._RULE]["matrix"]}

    monkeypatch.setattr(module, "method_group", lambda owner, signatures: {
        "announced_future_technical_regulation_profile": (read, False)})
    return state, module.TechnicalProfile(state), calls


def test_profile_copies_only_technical_inputs_and_detaches_nested_native_output(native):
    state, profile, calls = native
    manager = state.regulation_manager
    row = manager.regulations[module._RULE]
    before = dict(row)
    hidden = profile.prepare()
    assert hidden["key"] == "old" and hidden["severity"] == "minor" and not hidden["upcoming"]
    row["announcement_sent"] = True
    announced = profile.prepare()
    assert announced["key"] == "new" and announced["severity"] == "drastic" and announced["upcoming"]
    announced["concept_attribute_multipliers"]["concept"]["attribute"] = 999
    assert manager.regulation_defs[module._RULE]["matrix"]["concept"]["attribute"] == 1.2
    assert manager.regulations[module._RULE] is row
    assert dict(row, announcement_sent=False) == before and len(calls) == 2


def test_identity_compares_inputs_not_normalised_row_objects_and_rejects_new_policy(native):
    state, profile, _ = native
    before = profile.identity()
    state.regulation_manager.regulations[module._RULE] = dict(state.regulation_manager.regulations[module._RULE])
    assert profile.identity() == before
    state.regulation_manager.regulations[module._RULE]["current_severity"] = "major"
    assert profile.identity() != before
    state.regulation_manager.regulation_defs = dict(state.regulation_manager.regulation_defs)
    with pytest.raises(HookError, match="another native policy"):
        profile.prepare()


def test_project_guard_keeps_foreign_calls_native_and_rejects_changed_owned_inputs(native, monkeypatch):
    state, profile, calculations = native
    owner, writes = "owned.rules", []
    channel = SimpleNamespace(_unavailable=set(), bindings=lambda scope: {owner: ((0, "rule"),)})
    channel.before_reply = lambda: None
    channel.decision = {"decision": "allow"}
    contexts = []

    class Channel:
        def __call__(self, proposals, selections, calls, deadline):
            contexts.extend(proposals)
            channel.before_reply()
            return {owner: [channel.decision]}

    # The transport fixture is callable; the real guard still owns admission,
    # before/after identities, the shared proposal and the native write boundary.
    wire = Channel()
    wire.bindings, wire._unavailable = channel.bindings, channel._unavailable
    engine = object.__new__(ProjectAdapter)
    RegulationGuards.__init__(engine, wire, lambda *args: {"selected": "current", "current": [0, 1], "upcoming": None},
                             "development.project")
    engine.planner = SimpleNamespace(managers=(SimpleNamespace(), state.regulation_manager), technical=profile)
    native_module = ModuleType("owned_technical_project")
    monkeypatch.setitem(sys.modules, native_module.__name__, native_module)

    def start(self, project_type):
        writes.append(project_type)
        return True

    def project(arguments, result):
        proposal = {"technical_regulation": profile.prepare()} if arguments["project_type"] == "future" else {}
        return {"effective": {"season": 2}, "proposal": proposal}

    native_module.start = start
    engine.approve(CallableTarget("start", native_module.__name__, (), "start", str(inspect.signature(start))))
    engine.register(owner, [CallBinding("project", "start", project,
        refuse_boolean)])
    engine.poll()
    try:
        # The class hook also sees other careers. Their calls stay native even
        # while this career's validators refuse both owned manager types.
        channel.decision = {"decision": "deny", "message": "Project refused"}
        assert native_module.start(self=SimpleNamespace(), project_type="future") is True
        assert writes == ["future"] and not contexts and not calculations
        for manager in engine.planner.managers:
            assert native_module.start(manager, "current") is False
            assert engine.last_outcome.message == "Project refused"
        assert writes == ["future"]
        channel.decision = {"decision": "allow"}
        writes.clear()
        row = state.regulation_manager.regulations[module._RULE]
        for name, value in (("current_severity", "major"), ("announcement_sent", True)):
            channel.before_reply = lambda: row.__setitem__(name, value)
            assert native_module.start(state.regulation_manager, "future") is False
            assert engine.last_outcome.status == "unavailable" and engine.owners[owner] == "ready"
        channel.before_reply = lambda: None
        assert native_module.start(state.regulation_manager, "future") is True
        assert contexts[-1]["proposal"]["technical_regulation"]["upcoming"]
        assert len(calculations) == 3 and writes == ["future"]
        channel.before_reply = lambda: row.__setitem__("announcement_sent", False)
        assert native_module.start(state.regulation_manager, "current") is True
        assert len(calculations) == 3 and writes == ["future", "current"]
    finally:
        engine.stop()
