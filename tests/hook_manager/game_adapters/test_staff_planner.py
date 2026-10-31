# SPDX-License-Identifier: MPL-2.0
"""Staff term preparation preserves live records and resolves employer rules."""

from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.staff import planner as implementation
from hook_manager.manager import HookError


def test_staff_terms_resolve_employer_and_cover_without_live_effects(monkeypatch):
    agreement = {"team": "other", "start_week": 105, "length_weeks": 52, "salary_m": 1.0}
    staff = SimpleNamespace(name="Engineer", role="Head Of Engine", nationality="GB", age=41,
                            contract=agreement, future_contract=None)
    supplier = SimpleNamespace(active=True, acquired=False, departing=False,
                               nationality="DE", prestige_base=30, heritage_prestige=40., form_prestige=50.)
    manager = SimpleNamespace(staff=[staff])
    state = SimpleNamespace(staff_manager=manager, teams={"target": {"headquarters": {"engine_plant": 0}}},
                            game_mode="formula", season_no=3, week_index=9,
                            engine_manager=SimpleNamespace(external_suppliers={"target": supplier}))
    manager.state = state

    def active(self, employer, role, *, week):
        assert self is not manager and self.staff is manager.staff and week == 114
        return False

    # Stand-in date and eligibility helpers exercise employer and cover
    # selection without requiring installed game files.
    methods = {
        "state": {"current_season_start_week": (lambda self: 105, False), "next_season_start": (lambda self: 157, False)},
        "staff": {"normalize_role": (lambda role: "head_of_engine", True), "team_has_active_staff": (active, False),
                  "team_can_sign_role": (lambda self, name, role: False, False),
                  "supplier_can_sign_role": (lambda self, name, role: True, False),
                  "_supplier_by_id": (lambda self, name: self.state.engine_manager.external_suppliers.get(name), False)},
    }
    monkeypatch.setattr(implementation, "staff_methods", lambda state: methods)
    planner = implementation.StaffContractPlanner(state)
    bonuses = {"bonus_per_point_m": "0.1", "bonus_per_win_m": -2}
    arguments = {"self": manager, "team_name": "target", "staff": staff, "salary_m": 2.5, "years": 2,
                 "signer_kind": " AUTO ", "allow_current_cover": True, "contract_start_week": None,
                 "bonuses": bonuses, "bypass_requirements": True}
    before = deepcopy((vars(staff), state.teams, vars(supplier), arguments["bonuses"]))
    result = planner.prepare(arguments)
    assert set(result) == {"clock", "effective", "proposal"}
    assert result["effective"] == {"season": 3, "start_week": 114, "end_week": 157}
    proposal = result["proposal"]
    assert proposal["person"]["role"] == "Head Of Engine" and proposal["proposed"]["role"] == "head_of_engine"
    assert proposal["proposed"]["duration_weeks"] == 43
    assert proposal["employer"] == {"ref": "supplier:" + str(id(supplier)), "name": "target", "kind": "supplier",
                                    "nationality": "DE", "prestige_base": 30, "heritage_prestige": 40., "form_prestige": 50.}
    assert proposal["proposed"]["bonuses_m"] == {"point": .1, "win": -2., "podium": 0.}
    assert planner.prepare(arguments, payroll_checks=[])["proposal"]["employer"]["payroll"] is None
    proposal["proposed"]["bonuses_m"]["point"] = 99
    arguments.update(signer_kind="team", allow_current_cover=False, contract_start_week=157, bonuses=None)
    future = planner.prepare(arguments)
    assert future["effective"] == {"season": 4, "start_week": 157, "end_week": 261}
    assert all(future["proposal"]["employer"][key] is None
               for key in ("nationality", "prestige_base", "heritage_prestige", "form_prestige"))
    state.driver_manager = SimpleNamespace(drivers=[SimpleNamespace(
        contract={"team": "target", "salary_m": 7, "start_week": 105, "length_weeks": 156}, future_contract=None)])
    assert planner.prepare(arguments, payroll_checks=[])["proposal"]["employer"]["payroll"] == {
        "as_of_week": 157, "staff_annual_salary_m": 2.5, "driver_annual_salary_m": 7.}
    assert future["proposal"]["proposed"]["bonuses_m"] == {"point": None, "win": None, "podium": None}
    assert before[:3] == (vars(staff), state.teams, vars(supplier)) and before[3] == bonuses
    staff.name = "x" * 4097
    with pytest.raises(HookError, match="bounded plain values"):
        planner.prepare(arguments)
    staff.name = "Engineer"
    for incomplete in (True, None, float("nan")):
        arguments["salary_m"] = incomplete
        with pytest.raises(HookError, match="finite native numbers"):
            planner.prepare(arguments)
    state.staff_manager = SimpleNamespace()
    with pytest.raises(HookError, match="another native state"):
        planner.prepare(arguments)


def test_incompatible_staff_helpers_raise_without_importing_game():
    state = SimpleNamespace(game_mode="formula", staff_manager=SimpleNamespace(sign_staff=lambda: True))
    before = set(sys.modules)
    with pytest.raises(HookError):
        implementation.StaffContractPlanner(state)
    assert not {"managers.staff_manager", "managers.engine_manager", "game_state"} & (set(sys.modules) - before)
