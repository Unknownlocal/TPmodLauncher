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
    channel = SimpleNamespace(closed=False, bindings=lambda scope: {} if scope == "personnel.approach" else {"owned.rules": ((0, "rule"),)})
    def close():
        # Connections and installed objects must stop before definitions vanish.
        assert modules.prefix in sys.modules
        channel.closed = True
    channel.close = close
    native = Manager()
    native.groups["drivers.rookie-check"] = {"state": "active", "bindings": [], "changes": []}
    personnel, academy, projects = CallableAdapters(), CallableAdapters(), CallableAdapters()
    personnel.install_prepared = lambda: personnel
    academy.install_prepared = lambda: academy
    adapters = host.adapter_code.game_adapters.regulations
    adapters.channel = SimpleNamespace(RegulationChannel=lambda endpoint: channel)
    adapters.native_policy = SimpleNamespace(NativeRegulationPolicy=NativeRegulationPolicy)
    from hook_manager.game_adapters.regulations.approach_adapter import ApproachAdapter
    adapters.approach_adapter = SimpleNamespace(ApproachAdapter=ApproachAdapter)
    adapters.native_rules = SimpleNamespace(install_native_rules=lambda *a, **kw: (native,))
    adapters.personnel_adapter = SimpleNamespace(PersonnelAdapter=lambda *a, **kw: personnel)
    adapters.academy_adapter = SimpleNamespace(AcademyAdapter=lambda *a: academy)
    adapters.project_adapter = SimpleNamespace(ProjectAdapter=SimpleNamespace(create=lambda *a: projects))
    yield modules, career, channel, adapters, (native, personnel, academy, projects)
    assert modules.close()
    # A factory skipped by a test still owns its unreturned stand-in.
    for hook in (native, personnel, academy, projects):
        hook.stop()


def install(installation):
    modules, career, channel, _, _ = installation
    return modules.install_regulations(career, channel, lambda *a: None,
        native_rules={"rookie_contract_length_check": False, "staff_prestige_check": False}, report=pytest.fail)


def capture(installation):
    """Stand in for the native loading owner; its real checks have their own suite."""
    modules, career, _, _, _ = installation
    def current(generation):
        if generation != 1:
            raise HookError("The requested loading attempt is no longer current")
        return career
    modules.preload = SimpleNamespace(generation=1, current=current, allow=lambda state: None, stop=lambda: None,
                                     loading_status=lambda: {"state": "waiting", "generation": 1})
    return modules.preload


def test_installation_reports_complete_personnel_coverage(installation):
    modules, _, channel, _, hooks = installation
    with pytest.raises(HookError, match="native GameState"):
        modules.install_regulations(object(), channel, lambda *a: None, native_rules={}, report=pytest.fail)
    assert modules.channel is None and not channel.closed and all(not hook.closed for hook in hooks)
    coverage = install(installation)
    assert coverage == {"driver_signing": True, "staff_signing": True, "academy_signing": True,
        "personnel_approaches": False,
        "development_projects": True, "rookie_contract_length_check": "disabled",
        "staff_prestige_check": "skipped", "staffing": True}
    coverage["staffing"] = False
    assert modules.status()["regulations"] == {"state": "installed", "components": dict(coverage, staffing=True)}
    with pytest.raises(HookError, match="unused loaded"):
        install(installation)
    assert modules.stop_regulations()
    assert all(hook.closed for hook in hooks) and channel.closed
    assert modules.state == "loaded"  # Policy/career removal can keep definitions.


def test_late_academy_preflight_failure_leaves_all_personnel_methods_native(installation, monkeypatch):
    from hook_manager.game_adapters.regulations import personnel_adapter, academy_adapter
    from tests.hook_manager.test_personnel_adapter import owned

    modules, _, channel, adapters, hooks = installation
    state, _, negotiations, _, _, signing, callers = owned(monkeypatch)
    monkeypatch.setattr(sys.modules["game_state"], "GameState", type(state))
    adapters.personnel_adapter, adapters.academy_adapter = personnel_adapter, academy_adapter
    targets = (signing, personnel_adapter.staff_target(), *callers, *personnel_adapter.staffing_targets())
    originals = [target.resolve({})[2] for target in targets]
    observed = []

    def incompatible(career):
        observed.extend(target.resolve({})[2] for target in targets)
        raise HookError("Owned academy helper changed")

    monkeypatch.setattr(academy_adapter, "AcademyContractPlanner", incompatible)
    reports = []
    coverage = modules.install_regulations(state, channel, lambda *args: None,
                                          native_rules={}, report=reports.append)
    assert observed == originals == [target.resolve({})[2] for target in targets]
    assert len(reports) == 1 and "Owned academy helper changed" in reports[0]
    assert "continuing native game" in reports[0]
    assert not any(coverage[key] for key in ("driver_signing", "staff_signing", "staffing", "academy_signing"))
    assert coverage["development_projects"]  # Its independent scope remains usable.


def test_setup_exception_restores_prior_components_and_closes_channel(installation):
    modules, _, channel, adapters, hooks = installation
    def fail(*args):
        raise RuntimeError("Interrupted setup")
    adapters.project_adapter.ProjectAdapter.create = fail
    with pytest.raises(RuntimeError, match="Interrupted setup"):
        install(installation)
    assert all(hook.closed for hook in hooks[:-1]) and not hooks[-1].closed
    assert channel.closed and modules.channel is None and modules.coverage == {}
    assert modules.prefix in sys.modules


def test_incompatible_rule_selection_reports_skip_and_closes_candidate_channel(installation):
    modules, _, channel, _, hooks = installation
    channel._rules, channel._custom = {"missing.native.rule"}, set()
    capture(installation)
    reports = []
    result = modules.prepare_regulations({"generation": 1, "endpoint": {}}, report=reports.append)
    assert result["regulations"]["state"] == "unavailable"
    assert len(reports) == 1 and "COMPATIBILITY FAILED" in reports[0] and "continuing native game" in reports[0]
    assert modules.channel is None and modules.policy is None and modules.regulations == []
    assert all(not hook.closed for hook in hooks) and channel.closed


def test_preparation_publishes_without_hooks_and_owns_cleanup(installation):
    modules, career, channel, adapters, hooks = installation
    channel._token = "0" * 64
    channel._rules, channel._custom = set(), set()
    published = []
    channel.publish = lambda **data: published.append(data)
    request = {"generation": 1, "endpoint": {"port": 1, "token": "0" * 64}}
    with pytest.raises(HookError, match="preparation fields"):
        modules.prepare_regulations(dict(request, target="arbitrary"), report=pytest.fail)
    notices = []
    modules.prepare_regulations(request, report=notices.append)
    assert "captured native loading attempt" in notices[0] and modules.channel is None and not channel.closed
    capture(installation)
    result = modules.prepare_regulations(request, report=pytest.fail)
    assert result["regulations"] == {"state": "prepared", "components": {}, "career_generation": 1}
    assert published == [{"native": {}, "custom": {}}]
    assert modules.policy.state is career and modules.policy._published
    assert modules.channel is channel and modules.regulations == []
    assert all(not hook.closed for hook in hooks)
    with pytest.raises(HookError, match="already owns"):
        modules.prepare_regulations(request, report=pytest.fail)
    with pytest.raises(HookError, match="does not own"):
        modules.control_regulations("install-regulations", dict(request, generation=2), report=pytest.fail)
    assert modules.channel is channel and not channel.closed
    values = []
    original = adapters.native_rules.install_native_rules
    def install_switches(state, switches, **kwargs):
        values.append(dict(switches))
        return original(state, switches, **kwargs)
    adapters.native_rules.install_native_rules = install_switches
    switches = {"rookie_contract_length_check": False, "staff_prestige_check": True}
    assert modules.control_regulations("install-regulations", dict(request, native_rules=switches), report=pytest.fail)
    assert values == [switches]
    assert not channel.validation_ready and modules.status()["regulations"]["state"] == "installed"
    assert modules.policy.state is career and modules.policy._published
    assert modules.control_regulations("activate-regulations", request, report=pytest.fail)
    assert channel.validation_ready and modules.status()["regulations"]["state"] == "active"
    with pytest.raises(HookError, match="does not own"):
        modules.control_regulations("restore-native-rules", dict(request, generation=2), report=pytest.fail)
    assert modules.control_regulations("restore-native-rules", request, report=pytest.fail)
    assert not channel.validation_ready and not channel.closed
    assert hooks[0].closed and all(not hook.closed for hook in hooks[1:])
    assert modules.regulations == list(hooks[1:]) and modules.policy is not None
    assert modules.coverage["rookie_contract_length_check"] == "native"
    assert modules.stop_regulations() and channel.closed
    assert modules.policy is None and modules.regulation_generation is None


@pytest.mark.parametrize("failure", ["career_change", "replaced_hook"])
def test_activation_rejects_stale_career_or_replaced_owned_wrapper(installation, failure):
    modules, _, channel, _, hooks = installation
    channel._rules, channel._custom, channel._token = set(), set(), "0" * 64
    channel.publish = lambda **data: None
    request = {"generation": 1, "endpoint": {"port": 1, "token": channel._token}}
    capture(installation)
    modules.prepare_regulations(request, report=pytest.fail)
    modules.control_regulations("install-regulations", request, report=pytest.fail)
    owner, installed, foreign = SimpleNamespace(), object(), object()
    if failure == "career_change":
        modules.preload.current = lambda generation: object()
    else:
        owner.method = foreign
        hooks[0].groups["drivers.rookie-check"]["changes"].append((owner, "method", object(), installed, True))
    with pytest.raises(HookError, match="no longer current|replaced a regulation hook"):
        modules.control_regulations("activate-regulations", request, report=pytest.fail)
    assert not channel.validation_ready and channel.closed and modules.channel is None
    assert all(hook.closed for hook in hooks)
    if failure == "replaced_hook":
        assert owner.method is foreign


def test_cancelled_prepared_career_can_retire_without_closing_pack_connection(installation):
    modules, _, channel, _, _ = installation
    channel._rules, channel._custom, channel._token, channel.epoch = set(), set(), "0" * 64, 1
    channel.publish = lambda **data: None
    resets = []
    channel.reset = resets.append
    request = {"generation": 1, "endpoint": {"port": 1, "token": channel._token}}
    preload = capture(installation)
    modules.prepare_regulations(request, report=pytest.fail)
    def cancelled(generation):
        raise HookError("cancelled loading attempt")
    preload.current = cancelled
    assert modules.control_regulations("install-regulations", request, report=pytest.fail) == "stale"
    assert modules.channel is channel and not channel.closed
    assert modules.control_regulations("retire-regulations", request, report=pytest.fail)
    assert modules.preload is preload and modules.policy is None and resets == [2]


@pytest.mark.parametrize("failure", ["publication", "career_change"])
def test_preparation_failure_closes_candidate_and_reports_native_fallback(installation, failure):
    modules, _, channel, _, hooks = installation
    channel._rules, channel._custom = set(), set()
    released = []
    preload = capture(installation)
    preload.stop = lambda: released.append(True)
    def publish(**data):
        if failure == "publication":
            raise HookError("Publication failed")
        preload.current = lambda generation: object()
    channel.publish = publish
    notices = []
    result = modules.prepare_regulations({"generation": 1, "endpoint": {}}, report=notices.append)
    assert result["regulations"]["state"] == "unavailable"
    assert len(notices) == 1 and "continuing native game" in notices[0]
    assert modules.channel is None and modules.policy is None and channel.closed
    assert modules.regulations == [] and all(not hook.closed for hook in hooks)
    assert released == [True] and modules.preload is None


@contextmanager
def active_worker(guard):
    entered, release = threading.Event(), threading.Event()
    def worker():
        with guard.operation():
            entered.set()
            assert release.wait(3)
    thread = threading.Thread(target=worker)
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
    assert all(hook.closed for hook in hooks[:-1]) and not hooks[-1].closed
    assert "regulations" not in modules.status() and modules.regulation_error is None
