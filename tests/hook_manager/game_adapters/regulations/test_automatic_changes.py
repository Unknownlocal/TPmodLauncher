# SPDX-License-Identifier: MPL-2.0
"""Automatic decisions retain native choices, quotas and durable progress."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import random
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.automatic_progress import AutomaticProgress, FIELD
from hook_manager.game_adapters.regulations.regulation_category import RegulationCategoryAdapter
from hook_manager.game_adapters.regulations.regulation_change import RegulationChangeAdapter
from hook_manager.manager import HookError, Manager
from hook_manager.adapter_modules import AdapterModules


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
            self.work = lambda: None
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
            self.work()
            return "native week completed"
        def _trigger_candidates(self, snapshot):
            return sorted(self.definitions)[:1]
        def _send(self, subject, body, actions=None, *, structured_content=None):
            self.state.email_manager.send_email(subject, body, category="regulation", structured_content=structured_content)
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
        inbox = []
        state.email_manager = SimpleNamespace(inbox=inbox, send_email=lambda subject, body, **kwargs:
                                              inbox.append({"subject": subject, "body": body, **kwargs}))
        if categories.automatic is not None and pack == tmp_path and mode == "formula":
            categories.automatic.state, categories.automatic.channel = state, SimpleNamespace(validation_ready=True, closed=False)
        return state
    policies = {key: {"mode": "automatic", "base_chance": 1, "announcement_week": 5} for key in categories.categories}
    yield SimpleNamespace(owner=owner, categories=categories, build=build, policies=policies, report=report,
                          native=Changes, folder=tmp_path)
    categories.stop()
    owner.stop()


def test_failed_weekly_install_restores_native_methods_and_can_retry(automatic, monkeypatch):
    a = automatic
    week, candidates = a.native.process_week, a.native._trigger_candidates
    install = a.owner.install

    def fail_after_install(*args, **kwargs):
        install(*args, **kwargs)
        raise RuntimeError("Owned installation failure")

    monkeypatch.setattr(a.owner, "install", fail_after_install)
    with pytest.raises(RuntimeError, match="Owned installation failure"):
        a.owner.install_automatic(a.policies)
    assert a.native.process_week is week and a.native._trigger_candidates is candidates
    assert a.categories.automatic is None and a.owner.categories is a.categories
    saved = a.build().formula_regulation_governance.save_state()
    saved[FIELD] = {"schema": 1, "categories": {}}
    state = a.build(saved=saved)
    assert state.formula_regulation_governance.save_state() == saved
    monkeypatch.setattr(a.owner, "install", install)
    a.owner.install_automatic(a.policies)
    state = a.build()
    state.week_index = 5
    state.formula_regulation_governance.process_week()
    assert len(state.regulation_manager.pending_rules) == 2


def test_failed_category_construction_retains_busy_owner_until_cleanup(automatic, monkeypatch):
    a = automatic
    state = a.build()
    manager = state.formula_regulation_governance
    saved = manager.save_state()
    saved[FIELD] = {"schema": 1, "categories": {}}
    manager.load_state(saved)
    a.categories.stop()
    entered, release = threading.Event(), threading.Event()

    class HeldPolicy(dict):
        def __deepcopy__(self, memo):
            entered.set()
            assert release.wait(3)
            return dict(self)

    manager.policy = HeldPolicy(manager.policy)
    monkeypatch.setattr(a.native, "process_week", lambda self, changed: None)
    host = ModuleType("_automatic_failed_setup_test")
    host.manager = Manager()
    modules = AdapterModules(host, {})
    modules.regulation_changes = a.owner
    with ThreadPoolExecutor(max_workers=1) as worker:
        task = worker.submit(manager.save_state)
        try:
            assert entered.wait(3)
            with pytest.raises(HookError, match="busy"):
                modules.categories = RegulationCategoryAdapter({"pack": str(a.folder),
                    "categories": list(a.categories.categories.values()), "automatic_changes": a.policies}, a.owner)
            partial = a.owner.categories
            assert not modules.stop_regulations() and modules.categories is partial
        finally:
            release.set()
        assert task.result(timeout=3) == saved
    assert modules.stop_regulations() and partial.closed and a.owner.categories is None
    assert set(state.regulation_manager._load_regulation_defs()) == {"native"}
    assert manager.save_state() == saved


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
    assert manager.stages == 1 and record["pending_announcements"] == []
    assert len(state.email_manager.inbox) == 1
    # A native stage loaded without its progress record recovers the same notice.
    manager.load_state({key: value for key, value in manager.save_state().items() if key != FIELD})
    manager.process_week()
    assert manager.stages == 1 and manager.save_state()[FIELD]["categories"]["custom.a"] == record
    assert len(state.email_manager.inbox) == 1


def test_notice_capacity_prevents_staging_without_losing_old_notices(automatic):
    a = automatic
    a.owner.install_automatic({"custom.a": a.policies["custom.a"]})
    state = a.build()
    state.season_no, state.week_index = 34, 5
    manager = state.formula_regulation_governance
    state.email_manager.send_email = lambda *args, **kwargs: None
    record = {"checked_season": 33, "pending_announcements": [
        {"decision_season": season, "option": "limited"} for season in range(1, 33)]}
    manager._tpm_automatic_progress = AutomaticProgress({FIELD: {"schema": 1, "categories": {"custom.a": record}}})
    manager.process_week()
    assert manager.stages == 0 and manager.save_state()[FIELD]["categories"]["custom.a"] == record
    assert "no room" in a.report[-1]["detail"]
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
    replacement = RegulationCategoryAdapter({"pack": str(a.folder),
        "categories": list(a.categories.categories.values())}, a.owner)
    try:
        a.owner.install_automatic(a.policies)
        state = a.build(saved=saved)
        replacement.check(state)
        assert state.formula_regulation_governance.save_state() == saved
    finally:
        replacement.stop()


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
    assert len(a.report) == 1 and "disagree" in a.report[0]["detail"]


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


@pytest.mark.parametrize("inserted", [False, True])
def test_mail_failure_confirms_insertion_and_never_recreates_completed_mail(automatic, inserted):
    a = automatic
    a.owner.install_automatic({"custom.a": a.policies["custom.a"]})
    state = a.build()
    state.week_index = 5
    manager, mail = state.formula_regulation_governance, state.email_manager
    original = mail.send_email
    def failed(*args, **kwargs):
        if inserted:
            original(*args, **kwargs)
        raise RuntimeError("Mail backend failed")
    mail.send_email = failed
    manager.process_week()
    pending = manager._tpm_automatic_progress.data["categories"]["custom.a"]["pending_announcements"]
    assert bool(pending) is not inserted
    assert len(mail.inbox) == int(inserted) and "Mail backend failed" in a.report[-1]["detail"]
    mail.send_email = original
    manager.load_state(json.loads(json.dumps(manager.save_state())))
    manager.process_week()
    assert manager.stages == 1 and len(mail.inbox) == 1
    assert not manager.save_state()[FIELD]["categories"]["custom.a"]["pending_announcements"]
    mail.inbox.clear()
    manager.process_week()
    assert not mail.inbox


def test_two_failed_notice_seasons_survive_rollover_and_retry_before_due_week(automatic):
    a = automatic
    a.owner.install_automatic({"custom.a": a.policies["custom.a"]})
    state = a.build()
    manager, mail = state.formula_regulation_governance, state.email_manager
    original, attempts = mail.send_email, []
    mail.send_email = lambda *args, **kwargs: attempts.append(kwargs["structured_content"]["decision_season"])
    for season in (3, 4):
        state.season_no, state.week_index = season, 5
        manager.process_week()
        regulation = state.regulation_manager.regulations["custom.a"]
        regulation["current"], regulation["upcoming"] = regulation["upcoming"], None
        state.regulation_manager.pending_rules.clear()
        manager.staged_sources.clear()
    assert attempts == [3, 3, 4]
    saved = json.loads(json.dumps(manager.save_state()))
    state.season_no, state.week_index = 5, 0
    manager.load_state(saved)
    mail.send_email = original
    manager.process_week()
    assert manager.stages == 2 and len(mail.inbox) == 2
    assert {row["structured_content"]["decision_season"] for row in mail.inbox} == {3, 4}
    assert all("Effective from Season " + str(row["structured_content"]["decision_season"] + 1) in row["body"]
               for row in mail.inbox)
    assert not manager.save_state()[FIELD]["categories"]["custom.a"]["pending_announcements"]


def test_weekly_work_waits_for_the_current_careers_activation(automatic):
    a = automatic
    a.owner.install_automatic(a.policies)
    state = a.build()
    state.week_index = 5
    helper = a.categories.automatic
    helper.check(state)
    helper.channel.validation_ready = False
    assert state.formula_regulation_governance.process_week() == "native week completed"
    assert not state.regulation_manager.pending_rules and not state.email_manager.inbox
    helper.channel.validation_ready = True
    state.formula_regulation_governance.process_week()
    assert len(state.regulation_manager.pending_rules) == 2 and len(state.email_manager.inbox) == 2
    # A loaded replacement career must wait for its own installation/activation.
    replacement = a.build()
    replacement.week_index = 5
    helper.state = state
    replacement.formula_regulation_governance.process_week()
    assert not replacement.regulation_manager.pending_rules and not replacement.email_manager.inbox
    # Retirement preserves the pack hooks, but waits for an admitted weekly call.
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(3)
    manager = state.formula_regulation_governance
    manager.work = work
    host = ModuleType("_automatic_retirement_test")
    host.manager = Manager()
    modules = AdapterModules(host, {})
    modules.categories, modules.channel = a.categories, helper.channel
    modules.regulation_changes = a.owner
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(manager.process_week)
        try:
            assert entered.wait(3)
            helper.channel.validation_ready = False
            assert modules.stop_regulations(retain=True) is False
        finally:
            release.set()
        assert future.result(timeout=3) == "native week completed"
    assert modules.stop_regulations(retain=True) is True and not a.categories.closed
