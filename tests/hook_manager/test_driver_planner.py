# SPDX-License-Identifier: MPL-2.0
"""Driver term preparation preserves live records and checks compatibility."""

from copy import deepcopy
import sys
from types import MethodType, SimpleNamespace

import pytest

from hook_manager.manager import HookError
from hook_manager.game_adapters.drivers import planner as implementation
from hook_manager.game_adapters.regulations.contexts import regulation_context


def test_driver_planning_uses_private_records_and_keeps_native_effects_out(monkeypatch):
    contract = {"team": "target", "role": "main", "status": "lead", "start_week": 105,
                "length_weeks": 52, "salary_m": 1.5}
    driver = SimpleNamespace(name="Driver", nationality="GB", age=25, team="target", contract=contract, future_contract=None)
    manager = SimpleNamespace(drivers=[driver], _ai_signing_query_cache={"live": "untouched"},
                              _ai_car_package_components_cache={"live": "untouched"},
                              normalize_contract_status=lambda record: record)
    state = SimpleNamespace(driver_manager=manager, teams={"target": {"entry_count": 2,
                            "nationality": "GB", "prestige_base": 40, "heritage_prestige": 70., "form_prestige": 52.}},
                            game_mode="formula", player_team="target", season_no=3, week_index=9)
    manager.state = state

    def sign_driver(self, bonuses=None):
        raise AssertionError("Proposal preparation must never sign")

    manager.sign_driver = MethodType(sign_driver, manager)

    def native_rookie_limit(self, employer, person, role):
        assert self is not manager
        # The real helper ranks only to choose a positive one/two-year maximum.
        # Applicability must not reach that rank's live engine/parts services.
        self._team_competitiveness_rank(employer)
        return 2 if role == "main" else None

    manager._team_competitiveness_rank = lambda team: pytest.fail("Live ranking/services reached")

    def query(self, record, week):
        assert self._ai_signing_query_cache is None and record is not contract
        record["status"] = "equal"
        return "equal"

    def schedule(self, person, record, status, start, *, apply_effects):
        assert person is not driver and apply_effects is False
        record["status"] = status

    def reconcile(self, employer, person, record, start):
        self._schedule_contract_status_change(person, record, "equal", start, apply_effects=True)

    methods = {
        "state": {"current_season_start_week": (lambda self: 105, False), "next_season_start": (lambda self: 157, False)},
        "globals": {"driver_contract_capacity": (lambda *a, **k: 2, False), "canonical_driver_status": (lambda raw: "first", False)},
        "driver": {"_schedule_contract_status_change": (schedule, False), "contract_status_at_week": (query, False),
                   "current_reserve_cover_weeks": (lambda *a: None, False), "driver_team_next_season": (lambda *a: None, False),
                   "active_contract": (lambda self, person, week: person.contract, False),
                   "reconcile_player_main_statuses": (reconcile, False)},
    }
    # Stand-in date, capacity and status helpers expose writes to scratch
    # inputs, allowing the test to check that live records stay intact.
    monkeypatch.setattr(implementation, "driver_methods", lambda state: methods)
    monkeypatch.setattr(implementation, "native_rookie_limit", lambda manager: native_rookie_limit)
    planner = implementation.DriverContractPlanner(state)
    arguments = {"self": manager, "team_name": "target", "driver": driver, "salary_m": 2.5, "years": 2,
                 "cost_points": 0, "role": "main", "priority": "lead", "bonuses": {"bonus_per_point_m": .1},
                 "entry_id": None, "contract_start_week": None}
    before = deepcopy((state.teams, vars(driver), manager._ai_signing_query_cache,
                       manager._ai_car_package_components_cache, arguments["bonuses"]))
    prepared = planner.prepare(arguments)
    assert set(prepared) == {"clock", "effective", "proposal"}
    result = regulation_context("personnel.contract", prepared, initiator="player")
    assert result["schema"] == 1 and result["initiator"] == "player"
    proposal = result["proposal"]
    assert result["clock"] == {"season": 3, "absolute_week": 114}
    assert result["effective"] == {"season": 4, "start_week": 157, "end_week": 261}
    assert proposal["action"] == "renew" and proposal["person"]["nationality"] == "GB"
    assert proposal["employer"] == {"ref": "team:" + str(id(state.teams["target"])), "name": "target", "kind": "team",
                                    "nationality": "GB", "prestige_base": 40, "heritage_prestige": 70., "form_prestige": 52.}
    assert proposal["native_checks"] == {"rookie_contract_length_applies": True}
    assert proposal["person"]["role"] is None and proposal["proposed"]["role"] == "main"
    assert proposal["existing"]["status"] == proposal["proposed"]["status"] == "equal"
    proposal["person"]["nationality"] = "changed"
    proposal["employer"]["nationality"] = "changed"
    proposal["proposed"]["bonuses_m"]["point"] = 99
    preview = regulation_context("personnel.contract", planner.prepare(arguments), operation="preview")
    assert preview["operation"] == "preview" and preview["proposal"]["proposed"]["bonuses_m"]["point"] == .1
    assert preview["proposal"]["employer"]["nationality"] == "GB"
    with pytest.raises(HookError, match="Unsupported regulation proposal context"):
        regulation_context("personnel.contract", prepared, operation="activate")
    assert before == (state.teams, vars(driver), manager._ai_signing_query_cache,
                      manager._ai_car_package_components_cache, arguments["bonuses"])
    state.staff_manager = SimpleNamespace(staff=[SimpleNamespace(
        contract={"team": "target", "salary_m": 3, "start_week": 105, "length_weeks": 156}, future_contract=None)])
    enriched = planner.prepare(arguments, payroll_checks=[])
    assert enriched["proposal"]["employer"]["payroll"] == {
        "as_of_week": 157, "staff_annual_salary_m": 3., "driver_annual_salary_m": 2.5}
    assert before == (state.teams, vars(driver), manager._ai_signing_query_cache,
                      manager._ai_car_package_components_cache, arguments["bonuses"])

    class CustomBonus:
        def __float__(self):
            pytest.fail("Proposal input checks must precede custom conversion")

    arguments["bonuses"] = {"bonus_per_point_m": CustomBonus()}
    with pytest.raises(HookError, match="bounded plain values"):
        planner.prepare(arguments)
    for incomplete in (True, None):
        arguments["salary_m"] = incomplete
        with pytest.raises(HookError, match="finite native numbers"):
            planner.prepare(arguments)
    state.driver_manager = SimpleNamespace()
    with pytest.raises(HookError, match="another native state"):
        planner.prepare(arguments)


def test_incompatible_driver_planning_does_not_import_game():
    state = SimpleNamespace(game_mode="formula", driver_manager=SimpleNamespace(sign_driver=lambda: True))
    before = set(sys.modules)
    with pytest.raises(HookError):
        implementation.DriverContractPlanner(state)
    assert not {"managers.driver", "managers.formula_entries", "game_state"} & (set(sys.modules) - before)
