# SPDX-License-Identifier: MPL-2.0
"""Native construction and saved restoration both retain prepared mod scripts."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.game_adapters.regulations.native_categories import NativeCategories
from hook_manager.manager import HookError, Manager


@pytest.fixture
def native(monkeypatch, tmp_path):
    module = ModuleType("managers.formula_regulation_governance")
    def normalize_policy(raw, definitions):
        # Model the one native validation boundary. Real native policy validation
        # and application run separately against archived bodies in the probe.
        rows = raw["scripted_changes"]
        if len({(row["key"], row["effective_season"]) for row in rows}) != len(rows):
            raise ValueError("native category/season conflict")
        return deepcopy(raw)
    module.normalize_policy = normalize_policy
    class Governance:
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
    module.FormulaRegulationGovernanceManager = Governance
    monkeypatch.setitem(sys.modules, module.__name__, module)
    regulation = ModuleType("managers.regulation_manager")
    regulation.RegulationManager = type("RegulationManager", (), {"_load_regulation_defs": lambda self: {}})
    monkeypatch.setitem(sys.modules, regulation.__name__, regulation)
    category = {"key": "owned.rule", "default": "open", "options": [{"id": 1, "key": "open", "name": "Open"}]}
    script = {"id": "owned:change", "key": "owned.rule", "new_regulation": {"key": "open"}, "effective_season": 2}
    request = {"pack": str(tmp_path), "categories": [category], "scripted_changes": [script]}
    hook = NativeCategories(request)
    state = SimpleNamespace(data_dir=tmp_path, regulation_manager=regulation.RegulationManager())
    state.regulation_manager.state = state
    state.regulation_manager.regulation_defs = state.regulation_manager._load_regulation_defs()
    yield hook, state, Governance, request
    hook.stop()


def test_constructor_and_saved_restore_merge_once_and_keep_native_mode_and_progress(native):
    hook, state, Governance, request = native
    manager = state.formula_regulation_governance = Governance(state)
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
    other = Governance(state, again)
    assert other.policy == manager.policy
    state.data_dir = state.data_dir / "other-pack"
    manager.load_state(saved)
    assert manager.policy["scripted_changes"] == []


def test_conflicting_saved_script_retains_native_load_and_prevents_activation(native):
    hook, state, Governance, request = native
    manager = state.formula_regulation_governance = Governance(state)
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
    hook, _, Governance, request = native
    hook.stop()
    constructor = Governance.__init__
    incompatible = lambda self, changed: None
    monkeypatch.setattr(Governance, "load_state", incompatible)
    with pytest.raises(HookError, match="signature"):
        NativeCategories(request)
    assert Governance.__init__ is constructor and Governance.load_state is incompatible
    assert not hasattr(sys.modules["managers.regulation_manager"].RegulationManager._load_regulation_defs, "_tpm_hook_group")


def test_active_saved_restore_defers_pack_cleanup_and_cached_stopped_hooks_fall_through(native):
    hook, state, Governance, _ = native
    manager = state.formula_regulation_governance = Governance(state)
    saved = {"definition_snapshot": manager.definitions, "policy": manager.policy}
    host = ModuleType("_owned_script_loader")
    host.manager = Manager()
    modules = AdapterModules(host, {})
    modules.categories = hook
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(3)
    manager.work = work
    cached = manager.load_state
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(cached, saved)
        try:
            assert entered.wait(3)
            assert modules.close() is False and not hook.scripts.closed
        finally:
            release.set()
        assert future.result(timeout=3) == "native restored"
    assert modules.close() and hook.scripts.closed
    assert cached(saved) == "native restored"
