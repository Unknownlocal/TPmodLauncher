# SPDX-License-Identifier: MPL-2.0
"""Future activation uses native selection and normalization without live effects."""

from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from hook_manager.game_adapters.personnel.activation import ActivationPlanner
from hook_manager.game_adapters.regulations.contexts import regulation_context
from hook_manager.manager import HookError
from mod_runtime._regulations.policy import context_fields


def owned(monkeypatch):
    driver_module, staff_module, game_module = (ModuleType(name) for name in ("managers.driver", "managers.staff_manager", "game_state"))
    for module in (driver_module, staff_module, game_module):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    calls = []

    def canonical_driver_status(value: Any) -> str:
        return {"1st": "first", "2nd": "second"}.get(value, "equal")

    def normalize_driver_contract_status(contract: Any) -> Any:
        contract["role"] = contract.get("role", "main").strip().lower()
        contract["status"] = contract["priority"] = ("equal" if contract["role"] == "reserve" else
                                                   canonical_driver_status(contract.get("priority")))
        if driver_module.fail:
            raise ValueError("Native normalization failed after writing")
        return contract

    driver_module.canonical_driver_status = canonical_driver_status
    driver_module.normalize_driver_contract_status = normalize_driver_contract_status
    driver_module.fail = False

    class DriverManager:
        def normalize_contract_status(self, contract: Any) -> Any:
            return normalize_driver_contract_status(contract)

        def set_team(self, driver, team_name):
            raise AssertionError("Preparation ran a live team setter")

    class StaffManager:
        @staticmethod
        def normalize_role(role: "Optional[str]") -> "str":
            return role.lower().replace(" ", "_")

        def _supplier_by_id(self, supplier_id: "str"):
            return self.state.suppliers.get(supplier_id)

        def set_team(self, staff, team_name):
            raise AssertionError("Preparation ran a live team setter")

    class State:
        # Match the game's read-only roster property; preparation must provide
        # its own view rather than assigning a replacement roster on a state copy.
        @property
        def drivers(self):
            return self.driver_manager.drivers

        def current_season_start_week(self):
            return (self.season_no - 1) * 60 + 1

        def next_season_start(self):
            return self.season_no * 60 + 1

        def _activate_future_driver_contracts(self, current_week: int) -> None:
            calls.append("driver")
            for driver in self.drivers:
                future = getattr(driver, "future_contract", None)
                if not isinstance(future, dict) or future.get("start_week") != current_week:
                    continue
                try:
                    self.driver_manager.normalize_contract_status(future)
                except Exception:
                    pass
                driver.contract = future
                self.driver_manager.set_team(driver, future.get("team", driver.team))
                driver.future_contract = None

        def _activate_future_staff_contracts(self, current_week: int) -> None:
            calls.append("staff")
            for person in self.staff_manager.staff:
                future = getattr(person, "future_contract", None)
                if not isinstance(future, dict) or future.get("start_week") != current_week:
                    continue
                person.contract = future
                self.staff_manager.set_team(person, future.get("team", person.team))
                person.future_contract = None

    state = State()
    state.season_no, state.week_index, state.game_mode = 3, 9, "formula"
    state.teams, state.suppliers = {"target": {"active": False}}, {"engine": SimpleNamespace(active=False)}
    state.driver_manager, state.staff_manager = DriverManager(), StaffManager()
    for manager in (state.driver_manager, state.staff_manager):
        manager.state = state
    future = {"team": "target", "start_week": 131, "length_weeks": 17, "salary_m": 2.5,
              "role": " Reserve ", "priority": "1st", "bonus_per_win_m": -.1}
    driver = SimpleNamespace(name="Driver", nationality="GB", age=25, team="old", contract=None, future_contract=future)
    other = SimpleNamespace(**{**vars(driver), "name": "Other driver"})
    skipped = SimpleNamespace(name="Skipped", future_contract=["Not a contract"])
    state.driver_manager.drivers = [driver, other, skipped]
    staff = SimpleNamespace(name="Staff", nationality="GB", age=40, role="Head Of Engine", team="old", contract=None,
                            future_contract={"team": "engine", "start_week": 131, "length_weeks": 26, "salary_m": 3.})
    state.staff_manager.staff = [staff]
    return state, driver, other, staff, driver_module, calls


def test_native_selection_terms_and_shared_future_records_remain_private(monkeypatch):
    # Native activation catches normalization errors even after partial writes.
    # Both outcomes must stay on copied agreements and preserve shared inputs.
    for fail in (False, True):
        state, driver, other, staff, module, calls = owned(monkeypatch)
        module.fail = fail
        before = deepcopy([vars(person) for person in (driver, other, staff)])
        future = driver.future_contract
        result = ActivationPlanner(state).prepare(131)
        assert calls == ["driver", "staff"] and tuple(item.person for item in result) == (driver, other, staff)
        proposed = result[0].prepared["proposal"]
        assert proposed["person"]["ref"] == "driver:" + str(id(driver)) and proposed["action"] == "activate"
        assert proposed["proposed"]["role"] == "reserve" and proposed["proposed"]["status"] == "equal"
        assert proposed["proposed"]["duration_weeks"] == 17 and proposed["proposed"]["bonuses_m"]["win"] == -.1
        assert result[0].prepared["effective"] == {"season": 3, "start_week": 131, "end_week": 148}
        assert result[0].prepared["clock"] == {"season": 3, "absolute_week": 130}
        assert result[2].prepared["proposal"]["employer"] == {
            "kind": "supplier", "name": "engine", "ref": "supplier:" + str(id(state.suppliers["engine"]))}
        assert result[2].prepared["proposal"]["proposed"]["role"] == "head_of_engine"
        result[0].prepared["proposal"]["proposed"]["role"] = "Changed"
        assert result[1].prepared["proposal"]["proposed"]["role"] == "reserve"
        assert [vars(person) for person in (driver, other, staff)] == before
        assert driver.future_contract is other.future_contract is future


def test_explicit_evaluation_season_and_kind_use_native_calendar_and_activation_envelope(monkeypatch):
    state, driver, other, staff, module, calls = owned(monkeypatch)
    monkeypatch.delitem(sys.modules, "managers.driver")
    staff.future_contract["start_week"] = 181
    planner = ActivationPlanner(state, kinds=("staff",))
    with pytest.raises(HookError, match="evaluation season"):
        planner.prepare(181)
    result = planner.prepare(181, evaluation_season=4)
    context = regulation_context("personnel.contract", result[0].prepared, initiator="system", operation="activate")
    context_fields(context)
    assert calls == ["staff"] and context["clock"] == {"season": 3, "absolute_week": 130}
    assert context["effective"] == {"season": 4, "start_week": 181, "end_week": 207}
    assert state.season_no == 3 and state.week_index == 9 and staff.contract is None


def test_incompatible_state_and_unprojectable_activations_fail_before_live_changes(monkeypatch):
    for failure in ("signature", "initializing", "state", "ambiguous", "missing_owner", "missing_team", "money"):
        state, driver, other, staff, module, calls = owned(monkeypatch)
        if failure == "signature":
            monkeypatch.setattr(type(state), "_activate_future_driver_contracts", lambda self: None)
        elif failure == "initializing":
            module.__spec__ = SimpleNamespace(_initializing=True)
        elif failure == "ambiguous":
            state.teams["engine"] = {"active": True}
        elif failure == "missing_owner":
            state.suppliers.clear()
        elif failure == "missing_team":
            staff.future_contract.pop("team")
        elif failure == "money":
            staff.future_contract["salary_m"] = "3"
        before = deepcopy([vars(person) for person in (driver, other, staff)])
        with pytest.raises(HookError):
            planner = ActivationPlanner(state)
            if failure == "state":
                state.staff_manager = SimpleNamespace()
            planner.prepare(131)
        assert [vars(person) for person in (driver, other, staff)] == before
