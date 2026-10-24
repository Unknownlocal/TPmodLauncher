# SPDX-License-Identifier: MPL-2.0
"""The private toggle replaces one helper and keeps its ordinary hook lifetime."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.staff import prestige_switch


@pytest.fixture
def native(monkeypatch):
    module = ModuleType("managers.staff_manager")
    exec('''class StaffManager:
 def required_prestige(self, staff: 'Staff') -> 'int':
  self.calls.append(staff)
  return staff.requirement
''', module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manager = module.StaffManager()
    manager.calls = []
    state = SimpleNamespace(staff_manager=manager)
    manager.state = state
    return module, state


def test_native_default_needs_no_hook_or_compatibility_inspection(monkeypatch):
    monkeypatch.setattr(prestige_switch, "staff_prestige_target", lambda: pytest.fail("Native default inspected"))
    assert prestige_switch.StaffPrestigeSwitch.create(None, report=lambda text: pytest.fail(text)) is None


def test_disabled_minimum_avoids_native_calculation_and_restores_owned_helper(native):
    module, state = native
    original = module.StaffManager.required_prestige
    reports = []
    adapter = prestige_switch.StaffPrestigeSwitch.create(state, enabled=False, report=reports.append)
    assert adapter is not None and not reports
    escaped = state.staff_manager.required_prestige
    owned = state.staff_manager
    try:
        # This object lacks native inputs: disabling the check must not evaluate
        # the original formula before returning a replacement requirement.
        assert escaped(staff=object()) == 0 and state.staff_manager.calls == []
        other = module.StaffManager()
        other.calls = []
        other.state = SimpleNamespace(staff_manager=other)
        candidate = SimpleNamespace(requirement=17)
        assert other.required_prestige(candidate) == 17
        state.staff_manager = other
        assert escaped(candidate) == 17  # The old career no longer owns this manager.
        state.staff_manager = owned
        adapter.remove("staff.prestige-check")
        assert not adapter.closed
        assert module.StaffManager.required_prestige is original
        assert escaped(candidate) == 17
    finally:
        adapter.stop()
    assert module.StaffManager.required_prestige is original
    assert escaped(SimpleNamespace(requirement=23)) == 23


def test_incompatible_helper_is_reported_and_skipped(native):
    module, state = native
    def changed(self, staff, extra=None):
        return 17
    module.StaffManager.required_prestige = changed
    reports = []
    assert prestige_switch.StaffPrestigeSwitch.create(state, enabled=False, report=reports.append) is None
    assert len(reports) == 1 and "COMPATIBILITY FAILED" in reports[0] and "skipped" in reports[0]
    assert module.StaffManager.required_prestige is changed
