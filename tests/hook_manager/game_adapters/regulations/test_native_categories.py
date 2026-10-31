# SPDX-License-Identifier: MPL-2.0
"""Prepared category loading preserves native work and pack ownership."""

from concurrent.futures import ThreadPoolExecutor
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.regulation_category import RegulationCategoryAdapter
from hook_manager.adapter_modules import AdapterModules
from hook_manager.manager import HookError, Manager


@pytest.fixture
def native(monkeypatch, tmp_path):
    module = ModuleType("managers.regulation_manager")
    class RegulationManager:
        def _load_regulation_defs(self) -> "Dict[str, Dict[str, Any]]":
            self.calls += 1
            self.work()
            if self.failure:
                raise self.failure
            return self.base
    module.RegulationManager = RegulationManager
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manager = RegulationManager()
    manager.state = SimpleNamespace(data_dir=tmp_path)
    manager.calls, manager.failure, manager.base = 0, None, {"native": {"options": ["native fallback"]}}
    manager.work = lambda: None
    category = {"key": "owned.rule", "default": "open", "options": [{"id": 1, "key": "open", "name": "Open"}]}
    regulation_changes = SimpleNamespace(categories=None, check=lambda: None, detach_categories=lambda categories: None)
    hook = RegulationCategoryAdapter({"pack": str(tmp_path), "categories": [category]}, regulation_changes)
    yield hook, manager, category
    hook.stop()


def test_load_is_pack_specific_copies_categories_and_preserves_native_results(native, tmp_path):
    hook, manager, category = native
    cached = manager._load_regulation_defs
    first = cached()
    assert manager.calls == 1 and first["native"] is manager.base["native"]
    assert "owned.rule" not in manager.base and first["owned.rule"] == category
    first["owned.rule"]["options"].clear()
    category["options"].clear()
    second = cached()
    assert second["owned.rule"]["options"] and not hook.errors
    manager.regulation_defs = second
    manager.state.regulation_manager = manager
    hook.check(manager.state)
    manager.state.data_dir = tmp_path / "other-pack"
    assert cached() is manager.base
    with pytest.raises(HookError, match="did not load"):
        hook.check(manager.state)
    manager.state.data_dir = tmp_path
    manager.state.game_mode = "oval"
    assert cached() is manager.base
    manager.state.game_mode = "formula"
    hook.stop()
    assert cached() is manager.base


def test_conflict_reports_mapping_failure_without_changing_native_load(native):
    hook, manager, _ = native
    manager.base["owned.rule"] = {"native": True}
    assert manager._load_regulation_defs() is manager.base
    assert "conflict" in hook.errors[-1]
    manager.state.regulation_manager = manager
    manager.regulation_defs = manager.base
    with pytest.raises(HookError, match="did not load"):
        hook.check(manager.state)
    # Native exceptions retain their original identity; our wrapper never retries.
    manager.failure = ValueError("native failed")
    with pytest.raises(ValueError) as raised:
        manager._load_regulation_defs()
    assert raised.value is manager.failure and manager.calls == 2


def test_incompatible_signature_leaves_native_function_intact(monkeypatch, tmp_path):
    module = ModuleType("managers.regulation_manager")
    original = lambda self, changed: None
    module.RegulationManager = type("RegulationManager", (), {"_load_regulation_defs": original})
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(HookError, match="signature"):
        RegulationCategoryAdapter({"pack": str(tmp_path), "categories": [{"key": "owned.rule"}]},
                         SimpleNamespace(categories=None, check=lambda: None, detach_categories=lambda categories: None))
    assert module.RegulationManager._load_regulation_defs is original


def test_pack_loader_survives_retirement_and_busy_loading_defers_cleanup(native):
    hook, manager, _ = native
    host = ModuleType("_owned_category_loader")
    host.manager = Manager()
    modules = AdapterModules(host, {})
    modules.categories = hook
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(3)
    manager.work = work
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(manager._load_regulation_defs)
        try:
            assert entered.wait(3)
            assert modules.stop_regulations(retain=True) and modules.categories is hook
            assert modules.close() is False and not hook.closed
        finally:
            release.set()
        assert "owned.rule" in future.result(timeout=3)
    assert modules.close() and hook.closed and modules.categories is None
