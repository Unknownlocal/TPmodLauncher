# SPDX-License-Identifier: MPL-2.0
"""Adopted options control native checks without replacing their calculations."""

import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.regulation_native_switches import install_regulation_switches
from hook_manager.game_adapters.regulations.regulation_selection import RegulationSelection
from tests.hook_manager.native_switch_support import native_switches as native


def test_adoption_future_contracts_and_nested_scope_keep_native_checks_owned(native, monkeypatch):
    state, drivers, staff = native
    category = json.loads((Path(__file__).resolve().parents[4] / "examples/rookie_contract_mod/regulations.json").read_text())[
        "mod_extensions"]["example.rookie_contracts"]["categories"][0]
    module = ModuleType("managers.regulation_manager")
    exec('''class RegulationManager:
 def _option_key_for_season(self, category_key: 'str', season_no: 'int') -> 'Optional[str]':
  return self.upcoming if season_no == 2 else self.current
 @property
 def regulations(self):
  return {rule: {"current": self.current, "upcoming": self.upcoming} for rule in self.regulation_defs}
''', vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    state.season_no, state.regulation_manager = 1, module.RegulationManager()
    state.regulation_manager.state = state
    state.regulation_manager.regulation_defs = {category["key"]: category}
    state.regulation_manager.current, state.regulation_manager.upcoming = "vanilla", "four_years"
    state.next_season_start = lambda: 53
    Driver = type("Driver", (), {"__module__": "managers.driver"})

    def start(self, team_name: str, driver: Driver, role: str = "main") -> int:
        return driver.start_week

    drivers._negotiation_start_week = start
    binding = {"rule": category["key"], "parameter": "native_check"}
    hooks = install_regulation_switches(state, {"rookie_contract_length_check": binding, "staff_prestige_check": binding}, submit_log=pytest.fail)
    rookie, candidate = SimpleNamespace(rookie=True, start_week=1), SimpleNamespace(requirement=17)
    escaped = state.driver_manager.formula_rookie_contract_year_limit
    try:
        assert escaped("Team", rookie) == 2  # B: the original native cap.
        for key in ("unrestricted", "four_years"):
            state.regulation_manager.current = key
            assert escaped("Team", rookie) is None  # A/C: only the native cap is bypassed.
        assert state.staff_manager.required_prestige(candidate) == 0
        state.regulation_manager.upcoming = "vanilla"
        rookie.start_week = 53
        assert escaped("Team", rookie) == 2  # Native query infers next-season terms.
        with RegulationSelection.scope(state, start_week=1):
            assert escaped("Team", rookie) is None
            with pytest.raises(ValueError, match="native failure"):
                with RegulationSelection.scope(state, 2):
                    assert escaped("Team", rookie) == 2
                    assert state.staff_manager.required_prestige(candidate) == 17
                    raise ValueError("native failure")
            assert escaped("Team", rookie) is None  # Nested exceptions restore the outer season.
        assert escaped("Team", rookie) == 2
        assert state.staff_manager.required_prestige(candidate) == 0
    finally:
        for hook in reversed(hooks):
            hook.stop()
    assert escaped("Team", rookie) == 2  # Cached methods resume native after cleanup.
