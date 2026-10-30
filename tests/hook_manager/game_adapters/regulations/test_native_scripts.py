# SPDX-License-Identifier: MPL-2.0
"""Native construction and saved restoration both retain prepared mod scripts."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.game_adapters.regulations.regulation_category import RegulationCategoryAdapter
from hook_manager.game_adapters.regulations.regulation_change import RegulationChangeAdapter
from hook_manager.game_adapters.regulations.automatic_progress import FIELD
from hook_manager.manager import HookError, Manager


@pytest.fixture
def native(monkeypatch, tmp_path):
    module = ModuleType("managers.formula_regulation_governance")
    def normalize_policy(raw, definitions):
        # Reject duplicate category/season pairs and return independent policy data.
        rows = raw["scripted_changes"]
        if len({(row["key"], row["effective_season"]) for row in rows}) != len(rows):
            raise ValueError("native category/season conflict")
        return deepcopy(raw)
    module.normalize_policy = normalize_policy
    class RegulationChanges:
        def __init__(self, state, saved_bootstrap=None):
            self.state = state
            self.calls = []
            self.work = lambda: None
            self.load_state(saved_bootstrap or {"policy": {"mode": "scripted", "scripted_changes": []},
                                               "definition_snapshot": state.regulation_manager.regulation_defs})
        def load_state(self, data):
            self.work()
            self.calls.append(data)
            if data.get("fail"):
                raise data["fail"]
            self.definitions = deepcopy(data["definition_snapshot"])
            self.policy = deepcopy(data["policy"])
            self.applied_script_ids = set(data.get("applied_script_ids", []))
            return "native restored"
        def save_state(self):
            self.work()
            return {"definition_snapshot": deepcopy(self.definitions), "policy": deepcopy(self.policy),
                    "applied_script_ids": sorted(self.applied_script_ids)}
    module.FormulaRegulationGovernanceManager = RegulationChanges
    monkeypatch.setitem(sys.modules, module.__name__, module)
    regulation = ModuleType("managers.regulation_manager")
    regulation.RegulationManager = type("RegulationManager", (), {"_load_regulation_defs": lambda self: {}})
    regulation.RegulationManager._load_regulation_defs.__annotations__ = {"return": "Dict[str, Dict[str, Any]]"}
    monkeypatch.setitem(sys.modules, regulation.__name__, regulation)
    category = {"key": "owned.rule", "default": "open", "options": [{"id": 1, "key": "open", "name": "Open"}]}
    script = {"id": "owned:change", "key": "owned.rule", "new_regulation": {"key": "open"}, "effective_season": 2}
    request = {"pack": str(tmp_path), "categories": [category], "scripted_changes": [script]}
    notices = []
    regulation_changes = RegulationChangeAdapter(notices.append)
    hook = RegulationCategoryAdapter(request, regulation_changes)
    state = SimpleNamespace(data_dir=tmp_path, regulation_manager=regulation.RegulationManager())
    state.notices = notices
    state.regulation_manager.state = state
    state.regulation_manager.regulation_defs = state.regulation_manager._load_regulation_defs()
    yield hook, state, RegulationChanges, request
    hook.stop()
    regulation_changes.stop()


def test_constructor_and_saved_restore_merge_once_and_keep_native_mode_and_progress(native):
    hook, state, RegulationChanges, request = native
    manager = state.formula_regulation_governance = RegulationChanges(state)
    row = request["scripted_changes"][0]
    assert manager.policy["scripted_changes"] == [row] and not hook.errors
    # A save made before this mod's rule existed lacks both the category and script.
    saved = {"definition_snapshot": {}, "policy": {"mode": "voting", "scripted_changes": []},
             "applied_script_ids": [row["id"]]}
    before = deepcopy(saved)
    assert manager.load_state(saved) == "native restored"
    assert saved == before and manager.policy["mode"] == "voting"
    assert manager.policy["scripted_changes"] == [row] and manager.applied_script_ids == {row["id"]}
    again = {**saved, "definition_snapshot": manager.definitions, "policy": manager.policy}
    manager.load_state(again)
    hook.check(state)
    assert manager.policy["scripted_changes"] == [row] and not hook.errors
    # Constructor bootstrap also has to work before load_state restores the save.
    other = RegulationChanges(state, again)
    assert other.policy == manager.policy
    state.data_dir = state.data_dir / "other-pack"
    manager.load_state(saved)
    assert manager.policy["scripted_changes"] == []


def test_conflicting_saved_script_retains_native_load_and_prevents_activation(native):
    hook, state, RegulationChanges, request = native
    manager = state.formula_regulation_governance = RegulationChanges(state)
    saved = {"definition_snapshot": manager.definitions, "policy": {"mode": "scripted", "scripted_changes": [
        {**request["scripted_changes"][0], "effective_season": 3}]}}
    assert manager.load_state(saved) == "native restored"
    assert "differs" in hook.errors[-1] and manager.policy == saved["policy"]
    with pytest.raises(HookError, match="did not load"):
        hook.check(state)
    failure = ValueError("native restore failed")
    with pytest.raises(ValueError) as raised:
        manager.load_state({"fail": failure})
    assert raised.value is failure and manager.calls[-1] == {"fail": failure}


def test_script_signature_failure_restores_earlier_hooks(native, monkeypatch):
    hook, _, RegulationChanges, request = native
    hook.stop()
    hook.regulation_changes.stop()
    constructor = RegulationChanges.__init__
    incompatible = lambda self, changed: None
    monkeypatch.setattr(RegulationChanges, "load_state", incompatible)
    with pytest.raises(HookError, match="signature"):
        RegulationChangeAdapter(lambda message: None)
    assert RegulationChanges.__init__ is constructor and RegulationChanges.load_state is incompatible
    assert not hasattr(sys.modules["managers.regulation_manager"].RegulationManager._load_regulation_defs, "_tpm_hook_group")


@pytest.mark.parametrize("operation", ["load_state", "save_state"])
def test_active_regulation_changes_defers_cleanup_and_cached_stopped_hooks_fall_through(native, operation):
    hook, state, RegulationChanges, _ = native
    manager = state.formula_regulation_governance = RegulationChanges(state)
    saved = {"definition_snapshot": manager.definitions, "policy": manager.policy}
    host = ModuleType("_owned_script_loader")
    host.manager = Manager()
    modules = AdapterModules(host, {})
    modules.categories = hook
    modules.regulation_changes = hook.regulation_changes
    if operation == "save_state":
        hook.stop()
        modules.categories = None
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(3)
    manager.work = work
    cached = getattr(manager, operation)
    arguments = (saved,) if operation == "load_state" else ()
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(cached, *arguments)
        try:
            assert entered.wait(3)
            assert modules.close() is False and not hook.regulation_changes.closed
        finally:
            release.set()
        result = future.result(timeout=3)
        assert result == ("native restored" if operation == "load_state" else manager.save_state())
    assert modules.close() and hook.regulation_changes.closed
    assert cached(*arguments) == result


def progress():
    return {"schema": 1, "categories": {"disabled.rule": {"checked_season": 3,
        "pending_announcements": [{"decision_season": 2, "option": "first"},
                                  {"decision_season": 3, "option": "second"}]}}}


def test_progress_survives_without_active_mods_and_stays_with_its_career(native):
    hook, state, RegulationChanges, _ = native
    hook.stop()
    manager = RegulationChanges(state)
    old = manager.save_state()
    assert FIELD not in old
    saved = dict(old, **{FIELD: progress()})
    other = RegulationChanges(state, saved)
    assert other.save_state()[FIELD] == progress()
    manager.load_state(saved)
    saved[FIELD]["categories"].clear()
    outgoing = json.loads(json.dumps(manager.save_state()))
    assert outgoing == dict(old, **{FIELD: progress()})
    outgoing[FIELD]["categories"].clear()
    assert manager.save_state()[FIELD] == progress()
    manager.load_state(old)
    assert FIELD not in manager.save_state() and other.save_state()[FIELD] == progress()
    assert not state.notices


@pytest.mark.parametrize("raw", [
    {"schema": 2, "future": {"retain": [1, 2]}},
    {"schema": 1, "categories": {"disabled.rule": {"checked_season": True, "pending_announcements": []}}}])
def test_unrecognised_progress_is_preserved_and_reported_instead_of_reset(native, raw):
    hook, state, RegulationChanges, _ = native
    hook.stop()
    manager = RegulationChanges(state)
    saved = dict(manager.save_state(), **{FIELD: raw})
    assert manager.load_state(saved) == "native restored"
    assert manager._tpm_automatic_progress.error
    assert manager.save_state()[FIELD] == raw
    assert "automatic changes unavailable" in state.notices[-1]


def test_unsafe_progress_cannot_be_silently_dropped_from_a_save(native):
    hook, state, RegulationChanges, _ = native
    hook.stop()
    manager = RegulationChanges(state)
    raw = {"schema": 1, "categories": {}, "excess": [None] * 32769}
    manager.load_state(dict(manager.save_state(), **{FIELD: raw}))
    with pytest.raises(HookError, match="Cannot safely preserve"):
        manager.save_state()
    assert "node limit" in state.notices[-1]
