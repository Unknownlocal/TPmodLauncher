# SPDX-License-Identifier: MPL-2.0
"""Prepare native rule data once, then select through an owned game interface."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.native_policy import NativeRegulationPolicy
from hook_manager.manager import HookError


@pytest.fixture
def native(monkeypatch):
    module = ModuleType("managers.regulation_manager")
    exec('''class RegulationManager:
 def _option_key_for_season(self, category_key: 'str', season_no: 'int') -> 'Optional[str]':
  self.calls += 1
  row = self.regulations[category_key]
  use_upcoming = (season_no >= row.get('upcoming_effective_season', self.state.season_no + 1)
                  if category_key == 'entry' else season_no == self.state.season_no + 1)
  return row['upcoming'] if use_upcoming and row['upcoming'] else row['current']
''', vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    state = SimpleNamespace(season_no=2027, regulation_manager=module.RegulationManager())
    manager = state.regulation_manager
    manager.state, manager.calls = state, 0
    manager.regulation_defs = {rule: {"options": [
        {"key": "open", "name": "Open", "parameters": {"limit": {"years": 5}}},
        {"key": "limited", "limit": 2},
        {"key": "open", "limit": 999},
    ]} for rule in ("entry", "ordinary", "mine.custom")}
    manager.regulation_defs["unused"] = object()  # Unsubscribed data is never consumed.
    manager.regulations = {rule: {"current": "open", "upcoming": "limited"}
                           for rule in ("entry", "ordinary", "mine.custom")}
    manager.regulations["entry"]["upcoming_effective_season"] = 2029
    publications = []
    channel = SimpleNamespace(_rules={"entry", "ordinary", "mine.custom"}, _custom={"mine.custom"},
                              closed=False, publish=lambda **body: publications.append(body))
    return state, channel, publications


def test_prepared_choices_use_native_selection_and_send_small_references(native):
    state, channel, publications = native
    policy = NativeRegulationPolicy(state, channel)
    with pytest.raises(HookError, match="unavailable"):
        policy.select("entry", 2029)
    assert state.regulation_manager.calls == 0
    policy.publish()
    assert len(publications) == 1
    assert publications[0]["native"]["entry"] == [
        {"key": "open", "parameters": {"limit": {"years": 5}}},
        {"key": "limited", "parameters": {"limit": 2}},
    ]
    assert publications[0]["custom"] == {"mine.custom": ["open", "limited"]}
    assert policy.select("entry", 2028) == {"selected": "current", "current": [0, 2027], "upcoming": [1, 2029]}
    assert policy.select("entry", 2030)["selected"] == "upcoming"
    # Ordinary native rules choose upcoming only for the next season, unlike
    # entry rules. Do not replace either behaviour with our own year comparison.
    assert policy.select("ordinary", 2028)["selected"] == "upcoming"
    assert policy.select("ordinary", 2030)["selected"] == "current"
    assert policy.select("mine.custom", 2028)["selected"] == "upcoming"
    assert state.regulation_manager.calls == 5 and len(publications) == 1
    with pytest.raises(HookError, match="already published"):
        policy.publish()
    # The prepared operational data has no live aliases into the game's JSON.
    state.regulation_manager.regulation_defs["entry"]["options"][0]["parameters"]["limit"]["years"] = 99
    assert publications[0]["native"]["entry"][0]["parameters"]["limit"]["years"] == 5


def test_live_adoption_and_rollover_reuse_acknowledged_choices(native):
    state, channel, publications = native
    policy = NativeRegulationPolicy(state, channel)
    state.regulation_manager.regulations["entry"]["upcoming"] = None
    with pytest.raises(HookError, match="before publication"):
        policy.publish()
    state.regulation_manager.regulations["entry"]["upcoming"] = "limited"
    policy.publish()
    previous = policy.identity()
    state.regulation_manager.regulations["entry"]["upcoming_effective_season"] = 2030
    assert policy.select("entry", 2029)["selected"] == "current"
    assert policy.select("entry", 2030)["upcoming"] == [1, 2030]
    assert policy.identity() != previous
    state.regulation_manager.regulations["entry"]["current"] = "limited"
    state.regulation_manager.regulations["entry"]["upcoming"] = None
    assert policy.select("entry", 2027) == {"selected": "current", "current": [1, 2027], "upcoming": None}
    state.season_no += 1
    assert policy.select("entry", 2028)["current"] == [1, 2028]
    assert policy.select("ordinary", 2029)["upcoming"] == [1, 2029]
    assert len(publications) == 1
    state.regulation_manager.regulations["entry"]["current"] = "unknown"
    with pytest.raises(HookError, match="missing from prepared data"):
        policy.select("entry", 2028)
    state.regulation_manager = object()
    with pytest.raises(HookError, match="no longer current"):
        policy.select("entry", 2028)


def test_failed_publication_does_not_enable_selection(native):
    state, channel, _ = native
    def fail(**body):
        raise RuntimeError("Policy was not acknowledged")
    channel.publish = fail
    policy = NativeRegulationPolicy(state, channel)
    with pytest.raises(RuntimeError, match="not acknowledged"):
        policy.publish()
    with pytest.raises(HookError, match="unavailable"):
        policy.select("entry", 2027)

    def changed_during_wait(**body):
        state.regulation_manager.regulations["entry"]["current"] = "limited"
    channel.publish = changed_during_wait
    with pytest.raises(HookError, match="during publication"):
        policy.publish()
    with pytest.raises(HookError, match="unavailable"):
        policy.select("entry", 2027)


def test_saved_replacement_with_an_existing_key_is_not_treated_as_base_data(native):
    state, channel, _ = native
    row = state.regulation_manager.regulations["entry"]
    row["upcoming_option"] = {"key": "limited", "limit": 99}
    with pytest.raises(HookError, match="Saved regulation replacement"):
        NativeRegulationPolicy(state, channel)
    row.pop("upcoming_option")
    policy = NativeRegulationPolicy(state, channel)
    policy.publish()
    row["upcoming_option"] = {"key": "limited", "limit": 99}
    with pytest.raises(HookError, match="Saved regulation replacement"):
        policy.select("entry", 2029)


@pytest.mark.parametrize("key,limit,index", [("limited", 99, 2), ("saved_action", 99, 2), ("limited", 2, 1)])
def test_loaded_calendar_action_uses_its_own_prepared_reference(native, monkeypatch, key, limit, index):
    state, channel, publications = native
    monkeypatch.setattr(sys.modules["managers.regulation_manager"], "CALENDAR_KEY", "ordinary", raising=False)
    row = state.regulation_manager.regulations["ordinary"]
    action = {"key": key, "name": "Saved action", "limit": limit}
    row.update(upcoming=key, upcoming_option=action)
    policy = NativeRegulationPolicy(state, channel)
    policy.publish()
    options = publications[0]["native"]["ordinary"]
    assert options[index] == {"key": key, "parameters": {"limit": limit}}
    assert options[1]["parameters"] == {"limit": 2}  # Base data is unchanged.
    assert policy.select("ordinary", 2027) == {"selected": "current", "current": [0, 2027], "upcoming": [index, 2028]}
    assert policy.select("ordinary", 2028)["selected"] == "upcoming"
    before = policy.identity()
    row.pop("upcoming_option")
    row.update(current="open", upcoming=None)
    state.season_no += 1
    assert policy.identity() != before
    assert policy.select("ordinary", 2028) == {"selected": "current", "current": [0, 2028], "upcoming": None}
    assert len(publications) == 1
    row.update(upcoming=key, upcoming_option=dict(action))
    with pytest.raises(HookError, match="needs a new preparation"):
        policy.select("ordinary", 2029)


def test_saved_calendar_action_rejects_mismatched_or_ambiguous_keys(native, monkeypatch):
    state, channel, _ = native
    monkeypatch.setattr(sys.modules["managers.regulation_manager"], "CALENDAR_KEY", "ordinary", raising=False)
    row = state.regulation_manager.regulations["ordinary"]
    row["upcoming_option"] = {"key": "wrong", "limit": 99}
    with pytest.raises(HookError, match="does not match"):
        NativeRegulationPolicy(state, channel)
    row["upcoming_option"]["key"] = "limited"
    row["current"] = "limited"
    with pytest.raises(HookError, match="ambiguous"):
        NativeRegulationPolicy(state, channel)


def test_boolean_only_setup_skips_native_inspection_and_incompatible_selector_is_rejected(native):
    _, channel, publications = native
    channel._rules = set()
    NativeRegulationPolicy(object(), channel).publish()
    assert publications == [{"native": {}, "custom": {}}]
    channel._rules = {"entry"}
    module = sys.modules["managers.regulation_manager"]
    module.RegulationManager._option_key_for_season = lambda self, changed: None
    state = SimpleNamespace(regulation_manager=module.RegulationManager())
    with pytest.raises(HookError, match="signature changed"):
        NativeRegulationPolicy(state, channel)
