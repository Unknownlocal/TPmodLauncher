# SPDX-License-Identifier: MPL-2.0
"""Automatic decisions retain native choices, quotas and durable progress."""

from copy import deepcopy
import json
import random
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.automatic_progress import AutomaticProgress, FIELD
from hook_manager.game_adapters.regulations.regulation_category import RegulationCategoryAdapter
from hook_manager.game_adapters.regulations.regulation_change import RegulationChangeAdapter
from hook_manager.manager import HookError


@pytest.fixture
def automatic(monkeypatch, tmp_path):
    native = ModuleType("managers.formula_regulation_governance")
    native.option_neighbours = lambda spec, current_key: [row["key"] for row in spec["options"]]
    class Changes:
        def __init__(self, state, saved_bootstrap=None):
            self.state = state
            self.seed = "fixed-career-seed"
            self.suppressed_script_ids = set()
            self.stages = 0
            self.load_state(saved_bootstrap or {"definition_snapshot": state.regulation_manager.regulation_defs,
                                               "staged_sources": {}, "policy": {"scripted_changes": []}})
        def load_state(self, data):
            self.definitions = deepcopy(data["definition_snapshot"])
            self.staged_sources = deepcopy(data["staged_sources"])
            self.policy = deepcopy(data["policy"])
        def save_state(self):
            return deepcopy({"definition_snapshot": self.definitions, "staged_sources": self.staged_sources,
                             "policy": self.policy})
        def process_week(self):
            return "native week completed"
        def _trigger_candidates(self, snapshot):
            return sorted(self.definitions)[:1]
        def stage_changes(self, changes, *, source=None, decision_id=None, effective_season=None):
            self.stages += 1
            for key, option in changes.items():
                self.state.regulation_manager.regulations[key]["upcoming"] = option
                self.state.regulation_manager.pending_rules[key] = option
                self.staged_sources[key] = {"source": source, "id": decision_id, "effective_season": effective_season}
    native.FormulaRegulationGovernanceManager = Changes
    monkeypatch.setitem(sys.modules, native.__name__, native)
    module = ModuleType("managers.regulation_manager")
    def definition(key):
        return {"key": key, "default": "open", "options": [{"key": key} for key in ("open", "limited", "closed")]}
    class Regulations:
        def _load_regulation_defs(self):
            return {"native": definition("native")}
    Regulations._load_regulation_defs.__annotations__ = {"return": "Dict[str, Dict[str, Any]]"}
    module.RegulationManager = Regulations
    monkeypatch.setitem(sys.modules, module.__name__, module)
    report = []
    owner = RegulationChangeAdapter(report.append)
    categories = RegulationCategoryAdapter({"pack": str(tmp_path),
                                            "categories": [definition("custom.a"), definition("custom.b")]}, owner)
    def build(saved=None, pack=tmp_path, mode="formula"):
        state = SimpleNamespace(data_dir=pack, game_mode=mode, season_no=3, week_index=0)
        regulation = state.regulation_manager = Regulations()
        regulation.state = state
        regulation.regulation_defs = regulation._load_regulation_defs()
        regulation.regulations = {key: {"current": "open", "upcoming": None} for key in regulation.regulation_defs}
        regulation.pending_rules = {}
        state.formula_regulation_governance = Changes(state, saved)
        return state
    policies = {key: {"mode": "automatic", "base_chance": 1, "announcement_week": 5} for key in categories.categories}
    yield SimpleNamespace(owner=owner, categories=categories, build=build, policies=policies, report=report,
                          native=Changes, folder=tmp_path)
    categories.stop()
    owner.stop()


def test_catch_up_draws_are_independent_and_survive_reload(automatic):
    a = automatic
    a.owner.install_automatic(a.policies)
    state = a.build()
    manager = state.formula_regulation_governance
    assert manager.process_week() == "native week completed"
    assert not manager._tpm_automatic_progress.present
    global_random = random.getstate()
    state.week_index = 8
    manager.process_week()
    assert random.getstate() == global_random
    chosen = deepcopy(state.regulation_manager.pending_rules)
    assert set(chosen) == set(a.policies) and all(choice != "open" for choice in chosen.values())
    saved = json.loads(json.dumps(manager.save_state()))
    manager.load_state(saved)
    manager.process_week()
    assert manager.stages == 2 and manager.save_state() == saved
    # Reversing policy order in another career must not change either draw.
    a.categories.automatic.policies = dict(reversed(a.categories.automatic.policies.items()))
    other = a.build()
    other.week_index = 8
    other.formula_regulation_governance.process_week()
    assert other.regulation_manager.pending_rules == chosen
    assert not a.report


@pytest.mark.parametrize("reason", ["zero_chance", "no_alternative", "approved", "scripted"])
def test_unchanged_outcomes_are_checked_once_without_notices(automatic, reason):
    a = automatic
    if reason == "no_alternative":
        a.categories.categories["custom.a"]["options"] = [{"key": "open"}]
    if reason == "zero_chance":
        a.policies["custom.a"]["base_chance"] = 0
    a.owner.install_automatic({"custom.a": a.policies["custom.a"]})
    state = a.build()
    state.week_index = 5
    manager = state.formula_regulation_governance
    if reason == "approved":
        manager.stage_changes({"custom.a": "closed"}, source="voting", decision_id="foreign", effective_season=4)
    if reason == "scripted":
        manager.policy["scripted_changes"] = [{"key": "custom.a", "effective_season": 4, "id": "scripted:a"}]
    manager.process_week()
    assert manager._tpm_automatic_progress.data["categories"]["custom.a"] == {
        "checked_season": 3, "pending_announcements": []}
    manager.process_week()
    assert manager.stages == (1 if reason == "approved" else 0)
    assert not a.report


@pytest.mark.parametrize("completed", [False, True])
def test_interrupted_native_stage_recovers_without_repeating_it(automatic, completed):
    a = automatic
    a.owner.install_automatic({"custom.a": a.policies["custom.a"]})
    state = a.build()
    state.week_index = 5
    manager = state.formula_regulation_governance
    original = manager.stage_changes
    def interrupted(*args, **kwargs):
        if completed:
            original(*args, **kwargs)
        raise RuntimeError("interrupted")
    manager.stage_changes = interrupted
    manager.process_week()
    assert len(a.report) == 1
    assert ("custom.a" in manager._tpm_automatic_progress.data["categories"]) == completed
    manager.stage_changes = original
    manager.process_week()
    record = manager.save_state()[FIELD]["categories"]["custom.a"]
    assert manager.stages == 1 and record["pending_announcements"] == [
        {"decision_season": 3, "option": state.regulation_manager.pending_rules["custom.a"]}]
    # A native stage loaded without its progress record recovers the same notice.
    manager.load_state({key: value for key, value in manager.save_state().items() if key != FIELD})
    manager.process_week()
    assert manager.stages == 1 and manager.save_state()[FIELD]["categories"]["custom.a"] == record


def test_notice_capacity_prevents_staging_without_losing_old_notices(automatic):
    a = automatic
    a.owner.install_automatic({"custom.a": a.policies["custom.a"]})
    state = a.build()
    state.season_no, state.week_index = 34, 5
    manager = state.formula_regulation_governance
    record = {"checked_season": 33, "pending_announcements": [
        {"decision_season": season, "option": "limited"} for season in range(1, 33)]}
    manager._tpm_automatic_progress = AutomaticProgress({FIELD: {"schema": 1, "categories": {"custom.a": record}}})
    manager.process_week()
    assert manager.stages == 0 and manager.save_state()[FIELD]["categories"]["custom.a"] == record
    assert "no room" in a.report[-1]
    a.categories.automatic.policies["custom.a"]["base_chance"] = 0
    manager.process_week()
    assert manager.save_state()[FIELD]["categories"]["custom.a"] == dict(record, checked_season=34)


def test_native_quotas_scope_and_category_cleanup(automatic):
    a = automatic
    original = a.native.process_week
    a.owner.install_automatic(a.policies)
    state = a.build()
    manager = state.formula_regulation_governance
    assert manager._trigger_candidates({}) == ["native"]
    assert set(manager.definitions) == {"native", *a.policies}
    for other in (a.build(pack=a.folder / "other"), a.build(mode="oval")):
        other.week_index = 5
        other.formula_regulation_governance.process_week()
        assert not other.regulation_manager.pending_rules
    state.week_index = 5
    cached = manager.process_week
    cached()
    saved = manager.save_state()
    a.categories.stop()
    assert a.native.process_week is original
    assert cached() == "native week completed" and manager.save_state() == saved


def test_inconsistent_native_stage_does_not_block_other_categories(automatic):
    a = automatic
    a.owner.install_automatic(a.policies)
    state = a.build()
    state.week_index = 5
    manager = state.formula_regulation_governance
    state.regulation_manager.pending_rules["custom.a"] = "closed"
    assert manager.process_week() == "native week completed"
    assert state.regulation_manager.regulations["custom.a"]["upcoming"] is None
    assert "custom.a" not in manager._tpm_automatic_progress.data["categories"]
    assert "custom.b" in manager._tpm_automatic_progress.data["categories"]
    assert len(a.report) == 1 and "disagree" in a.report[0]


def test_incremental_progress_budget_releases_space_without_losing_saved_records():
    record = {"checked_season": 32, "pending_announcements": [
        {"decision_season": season, "option": "\U0001f600" * 128} for season in range(1, 33)]}
    saved = {FIELD: {"schema": 1, "categories": {f"disabled.{index}": deepcopy(record) for index in range(20)}}}
    progress = AutomaticProgress(saved)
    assert progress.error is None
    with pytest.raises(HookError, match="byte or node"):
        progress.store_record("new", record)
    assert progress.snapshot() == saved[FIELD]
    progress.store_record("disabled.0", {"checked_season": 32, "pending_announcements": []})
    progress.store_record("new", record)
    restored = AutomaticProgress(json.loads(json.dumps({FIELD: progress.snapshot()})))
    assert restored.error is None and restored.snapshot() == progress.snapshot()
