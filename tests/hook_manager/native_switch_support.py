# SPDX-License-Identifier: MPL-2.0
"""Driver and staff methods used to exercise the native validation switches."""

import inspect
import sys
from types import ModuleType, SimpleNamespace
from typing import Optional

import pytest

from hook_manager.game_adapters.drivers import rookie_check


@pytest.fixture
def native_switches(monkeypatch):
    """Create driver and staff methods; tests own any switches they install."""
    driver = ModuleType("managers.driver")
    driver.Optional = Optional
    exec('''class DriverManager:
 def formula_rookie_contract_year_limit(self, team_name: str, driver: object, role: str = 'main') -> Optional[int]:
  self.calls += 1
  return 2 if driver.rookie and role == "main" else None
''', driver.__dict__)
    staff = ModuleType("managers.staff_manager")
    exec('''class StaffManager:
 def required_prestige(self, staff: 'Staff') -> 'int':
  self.calls.append(staff)
  return getattr(staff, 'requirement', 15)
''', staff.__dict__)
    for module in (driver, staff):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    # Bind the fixture's signature because annotation formatting can differ
    # between the test interpreter and the game interpreter.
    target = rookie_check.rookie_check_target()
    target.signature = str(inspect.signature(driver.DriverManager.formula_rookie_contract_year_limit))
    monkeypatch.setattr(rookie_check, "rookie_check_target", lambda: target)
    state = SimpleNamespace(driver_manager=driver.DriverManager(), staff_manager=staff.StaffManager())
    state.driver_manager.state = state.staff_manager.state = state
    state.driver_manager.calls, state.staff_manager.calls = 0, []
    return state, driver.DriverManager, staff.StaffManager

