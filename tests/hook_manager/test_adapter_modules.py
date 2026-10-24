# SPDX-License-Identifier: MPL-2.0
"""Private adapter imports own their definitions, not game hooks or mod code."""

import sys
import threading
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.callables import CallableAdapters
from hook_manager.game_adapters.regulations.native_policy import NativeRegulationPolicy
from hook_manager.manager import HookError, Manager


@pytest.fixture
def host(monkeypatch):
    module = ModuleType("_owned_adapter_bundle")
    module.manager = Manager()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    yield module
    module.manager.stop()


def test_failed_import_rolls_back_package_aliases_and_temporary_finder(host):
    # The bundle cannot fall back to files for an undeclared private import.
    modules = AdapterModules(host, {
        "game_adapters": ("owned/__init__.py", "from .missing import value", True),
    })
    finders = tuple(sys.meta_path)
    result = modules.load()
    assert result["state"] == "failed" and "not in the trusted adapter bundle" in result["detail"]
    assert modules.modules == {} and not hasattr(host, "adapter_code")
    assert not any(name.startswith(modules.prefix) for name in sys.modules)
    assert tuple(sys.meta_path) == finders
    assert modules.load() == result  # A failed source set is not partly retried.
    modules.close()


def test_load_rejects_a_foreign_namespace_without_removing_it(host, monkeypatch):
    modules = AdapterModules(host, {})
    foreign = ModuleType(modules.prefix + ".foreign")
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    assert modules.load()["state"] == "failed"
    assert sys.modules[foreign.__name__] is foreign
    modules.close()


def test_close_preserves_replaced_modules_and_cannot_reopen(host, monkeypatch):
    modules = AdapterModules(host, {"game_adapters": ("owned/__init__.py", "value = 3", True)})
    assert modules.load()["state"] == "loaded"
    name = modules.prefix + ".game_adapters"
    replacement = ModuleType(name)
    # Restore deletion, not the old imported module, after this test ends.
    sys.modules.pop(name)
    monkeypatch.setitem(sys.modules, name, replacement)
    modules.close()
    modules.close()
    assert sys.modules[name] is replacement and not hasattr(host, "adapter_code")
    assert modules.status() == {"state": "closed", "modules": 0}
    with pytest.raises(HookError, match="closed"):
        modules.load()


@pytest.fixture
def installation(host, monkeypatch):
    modules = AdapterModules(host, {
        "game_adapters": ("owned/__init__.py", "", True),
        "game_adapters.regulations": ("owned/regulations/__init__.py", "", True),
    })
    modules.load()
    game = ModuleType("game_state")
    game.GameState = type("GameState", (), {})
    monkeypatch.setitem(sys.modules, "game_state", game)
    career = game.GameState()
    def current(generation):
        if generation != 1:
            raise HookError("The requested career is no longer current")
        return career
    host.career = SimpleNamespace(current=current)
    channel = SimpleNamespace(closed=False)
    def close():
        # Connections and installed objects must stop before definitions vanish.
        assert modules.prefix in sys.modules
        channel.closed = True
    channel.close = close
    native = Manager()
    native.groups["drivers.rookie-check"] = {"state": "active", "bindings": [], "changes": []}
    personnel, projects = CallableAdapters(), CallableAdapters()
    personnel.planners = {"driver": object()}
    adapters = host.adapter_code.game_adapters.regulations
    adapters.native_policy = SimpleNamespace(NativeRegulationPolicy=NativeRegulationPolicy)
    adapters.native_rules = SimpleNamespace(install_native_rules=lambda *a, **kw: (native,))
    adapters.personnel_adapter = SimpleNamespace(PersonnelAdapter=SimpleNamespace(create=lambda *a: personnel))
    adapters.academy_adapter = SimpleNamespace(AcademyAdapter=SimpleNamespace(create=lambda *a: None))
    adapters.project_adapter = SimpleNamespace(ProjectAdapter=SimpleNamespace(create=lambda *a: projects))
    yield modules, career, channel, adapters, (native, personnel, projects)
    assert modules.close()
    # A factory skipped by a test still owns its unreturned stand-in.
    for hook in (native, personnel, projects):
        hook.stop()


def install(installation):
    modules, career, channel, _, _ = installation
    return modules.install_regulations(career, channel, lambda *a: None,
        native_rules={"rookie_contract_length_check": False, "staff_prestige_check": False}, report=pytest.fail)


def test_installation_reports_components_without_claiming_full_scope(installation):
    modules, _, channel, _, hooks = installation
    with pytest.raises(HookError, match="native GameState"):
        modules.install_regulations(object(), channel, lambda *a: None, native_rules={}, report=pytest.fail)
    assert not channel.closed and modules.channel is None
    with pytest.raises(HookError, match="no longer current"):
        modules.install_current_regulations(0, channel, native_rules={}, report=pytest.fail)
    assert not channel.closed and all(not hook.closed for hook in hooks)
    coverage = install(installation)
    assert coverage == {"driver_signing": True, "staff_signing": False, "academy_signing": False,
        "development_projects": True, "rookie_contract_length_check": "disabled",
        "staff_prestige_check": "skipped", "staffing": False}
    coverage["staffing"] = True
    assert modules.status()["regulations"] == {"state": "installed", "components": dict(coverage, staffing=False)}
    with pytest.raises(HookError, match="unused loaded"):
        install(installation)
    assert modules.stop_regulations()
    assert all(hook.closed for hook in hooks) and channel.closed
    assert modules.state == "loaded"  # Policy/career removal can keep definitions.


def test_setup_exception_restores_prior_components_and_closes_channel(installation):
    modules, _, channel, adapters, hooks = installation
    def fail(*args):
        raise RuntimeError("Interrupted setup")
    adapters.project_adapter.ProjectAdapter.create = fail
    with pytest.raises(RuntimeError, match="Interrupted setup"):
        install(installation)
    assert hooks[0].closed and hooks[1].closed and not hooks[2].closed
    assert channel.closed and modules.channel is None and modules.coverage == {}
    assert modules.prefix in sys.modules


def test_incompatible_rule_selection_reports_skip_before_taking_channel(installation):
    modules, _, channel, _, hooks = installation
    channel._rules, channel._custom = {"missing.native.rule"}, set()
    reports = []
    assert modules.install_current_regulations(1, channel, native_rules={}, report=reports.append) is None
    assert len(reports) == 1 and "COMPATIBILITY FAILED" in reports[0] and "continuing native game" in reports[0]
    assert modules.channel is None and modules.policy is None and modules.regulations == []
    assert all(not hook.closed for hook in hooks) and not channel.closed


@contextmanager
def active_worker(guard):
    entered, release = threading.Event(), threading.Event()
    def worker():
        with guard.operation():
            entered.set()
            assert release.wait(3)
    thread = threading.Thread(target=worker)
    guard.enroll(thread)
    thread.start()
    try:
        assert entered.wait(3)
        yield
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()


def test_busy_regulation_worker_defers_whole_bundle_cleanup(installation):
    modules, _, channel, _, hooks = installation
    install(installation)
    with active_worker(hooks[-1]):
        assert modules.close() is False
        assert all(not hook.closed for hook in hooks) and not channel.closed
        assert modules.prefix in sys.modules and modules.state == "loaded"
    assert modules.close() and all(hook.closed for hook in hooks) and channel.closed
    assert not any(name.startswith(modules.prefix) for name in sys.modules)


def test_failed_setup_reports_deferred_cleanup_until_earlier_worker_finishes(installation):
    modules, _, channel, adapters, hooks = installation
    def fail(*args):
        raise RuntimeError("Interrupted setup\n" + "x" * 600)
    adapters.project_adapter.ProjectAdapter.create = fail
    with active_worker(hooks[1]):
        with pytest.raises(RuntimeError, match="Interrupted setup"):
            install(installation)
        status = modules.status()["regulations"]
        assert status["state"] == "cleanup_pending" and len(status["detail"]) == 512
        assert "\n" not in status["detail"] and status["components"]["driver_signing"]
        assert all(not hook.closed for hook in hooks) and not channel.closed
    assert modules.stop_regulations() and channel.closed
    assert hooks[0].closed and hooks[1].closed and not hooks[2].closed
    assert "regulations" not in modules.status() and modules.regulation_error is None
