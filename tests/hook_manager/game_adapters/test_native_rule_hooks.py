# SPDX-License-Identifier: MPL-2.0
"""Fixed native switches preserve the game's rookie test for custom validators."""

from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations import regulation_native_switches
from hook_manager.game_adapters.drivers import rookie_switch
from hook_manager.game_adapters.staff import prestige_switch
from hook_manager.manager import HookError
from tests.hook_manager.native_switch_support import native_switches as native


def test_default_switches_need_no_hook_or_inspection(native, monkeypatch):
    state, drivers, staff = native
    originals = drivers.formula_rookie_contract_year_limit, staff.required_prestige
    monkeypatch.setattr(rookie_switch, "rookie_check_target", lambda: pytest.fail("Default inspected"))
    monkeypatch.setattr(prestige_switch, "staff_prestige_target", lambda: pytest.fail("Default inspected"))
    for values in ({}, {"rookie_contract_length_check": True, "staff_prestige_check": True}):
        assert regulation_native_switches.install_regulation_switches(state, values, submit_log=pytest.fail) == ()
        assert (drivers.formula_rookie_contract_year_limit, staff.required_prestige) == originals


def test_native_switch_queries_require_boolean_results(native):
    state, _, _ = native
    hooks = [rookie_switch.RookieContractLengthSwitch.create(state, enabled=lambda *args: "yes", submit_log=pytest.fail),
             prestige_switch.StaffPrestigeSwitch.create(state, enabled=lambda: 1, submit_log=pytest.fail)]
    try:
        with pytest.raises(HookError, match="return a boolean"):
            state.driver_manager.formula_rookie_contract_year_limit("Team", object())
        with pytest.raises(HookError, match="return a boolean"):
            state.staff_manager.required_prestige(object())
    finally:
        for hook in reversed(hooks):
            hook.stop()


def test_switch_keeps_original_applicability_available_and_restores_owned_helper(native):
    state, drivers, staff = native
    original = rookie_switch.native_rookie_limit(state.driver_manager)
    hooks = regulation_native_switches.install_regulation_switches(state, {
        "rookie_contract_length_check": False, "staff_prestige_check": False}, submit_log=pytest.fail)
    escaped = state.driver_manager.formula_rookie_contract_year_limit
    rookie, veteran = SimpleNamespace(rookie=True), SimpleNamespace(rookie=False)
    try:
        assert len(hooks) == 2
        assert escaped("Team", rookie) is None and state.driver_manager.calls == 0
        assert state.staff_manager.required_prestige(object()) == 0
        # Disabling the native cap must not erase the game's applicability test.
        retained = rookie_switch.native_rookie_limit(state.driver_manager)
        assert retained is original
        assert retained(state.driver_manager, "Team", rookie) == 2
        assert retained(state.driver_manager, "Team", veteran) is None
        assert retained(state.driver_manager, "Team", rookie, "reserve") is None
        other = drivers()
        other.state, other.calls = SimpleNamespace(), 0
        assert other.formula_rookie_contract_year_limit("Team", rookie) == 2
        owned = state.driver_manager
        state.driver_manager = other
        assert escaped("Team", rookie) == 2
        state.driver_manager = owned
        hooks[0].remove("drivers.rookie-check")
        assert escaped("Team", rookie) == 2
    finally:
        for hook in reversed(hooks):
            hook.stop()
    assert drivers.formula_rookie_contract_year_limit is original
    assert escaped("Team", rookie) == 2
    assert state.staff_manager.required_prestige(object()) == 15


def test_incompatible_rookie_helper_is_skipped_independently_of_staff(native):
    state, drivers, staff = native
    def changed(self, team_name, driver, extra):
        return 7
    drivers.formula_rookie_contract_year_limit = changed
    reports = []
    hooks = regulation_native_switches.install_regulation_switches(state, {
        "rookie_contract_length_check": False, "staff_prestige_check": False}, submit_log=reports.append)
    try:
        assert len(hooks) == 1 and len(reports) == 1
        assert "COMPATIBILITY FAILED drivers.rookie-check" in reports[0]["detail"]
        assert "continuing native" in reports[0]["detail"]
        assert drivers.formula_rookie_contract_year_limit is changed
        assert state.staff_manager.required_prestige(object()) == 0
    finally:
        for hook in hooks:
            hook.stop()


def test_invalid_switches_are_rejected_before_installing_either_hook(native):
    state, drivers, staff = native
    original = drivers.formula_rookie_contract_year_limit
    for values in ({"rookie_contract_max_years": {"value": 3}},
                   {"rookie_contract_length_check": None},
                   {"rookie_contract_length_check": False, "staff_prestige_check": 0}):
        with pytest.raises(HookError):
            regulation_native_switches.install_regulation_switches(state, values, submit_log=pytest.fail)
        assert drivers.formula_rookie_contract_year_limit is original


def test_unexpected_setup_failure_restores_previously_installed_hook(native, monkeypatch):
    state, drivers, staff = native
    original = drivers.formula_rookie_contract_year_limit
    def fail(*args, **kwargs):
        raise RuntimeError("Setup interrupted")
    monkeypatch.setattr(regulation_native_switches.StaffPrestigeSwitch, "create", fail)
    with pytest.raises(RuntimeError, match="Setup interrupted"):
        regulation_native_switches.install_regulation_switches(state, {"rookie_contract_length_check": False}, submit_log=pytest.fail)
    assert drivers.formula_rookie_contract_year_limit is original


def test_disabled_minimum_avoids_native_calculation_and_restores_owned_helper(native):
    state, _, staff = native
    original = staff.required_prestige
    reports = []
    adapter = prestige_switch.StaffPrestigeSwitch.create(state, enabled=False, submit_log=reports.append)
    assert adapter is not None and not reports
    escaped = state.staff_manager.required_prestige
    owned = state.staff_manager
    try:
        # This object lacks native inputs: disabling the check must not evaluate
        # the original formula before returning a replacement requirement.
        assert escaped(staff=object()) == 0 and state.staff_manager.calls == []
        other = staff()
        other.calls = []
        other.state = SimpleNamespace(staff_manager=other)
        candidate = SimpleNamespace(requirement=17)
        assert other.required_prestige(candidate) == 17
        state.staff_manager = other
        assert escaped(candidate) == 17  # The old career no longer owns this manager.
        state.staff_manager = owned
        adapter.remove("staff.prestige-check")
        assert not adapter.closed
        assert staff.required_prestige is original
        assert escaped(candidate) == 17
    finally:
        adapter.stop()
    assert staff.required_prestige is original
    assert escaped(SimpleNamespace(requirement=23)) == 23


def test_incompatible_helper_is_reported_and_skipped(native):
    state, _, staff = native
    def changed(self, staff, extra=None):
        return 17
    staff.required_prestige = changed
    reports = []
    assert prestige_switch.StaffPrestigeSwitch.create(state, enabled=False, submit_log=reports.append) is None
    assert len(reports) == 1 and "COMPATIBILITY FAILED" in reports[0]["detail"] and "skipped" in reports[0]["detail"]
    assert staff.required_prestige is changed
