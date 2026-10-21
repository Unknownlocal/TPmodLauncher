# SPDX-License-Identifier: MPL-2.0
"""Academy proposal ownership; actual native parity runs in the local probe."""

from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.academy import planner as implementation
from hook_manager.manager import HookError


def test_academy_sign_and_renew_prepare_full_season_terms_without_live_writes(monkeypatch):
    contract = {"start_season": 1, "term_seasons": 3, "annual_cost_m": .8}
    driver = SimpleNamespace(name="Junior", nationality="GB", age=16, talent=40,
                             academy_contract={"team": "stale", "term_seasons": 99})
    owner = {"name": "target", "academy": {"contracts": {driver.name: contract}}, "balance_m": 50.}
    manager = SimpleNamespace()
    state = SimpleNamespace(academy_manager=manager, driver_manager=SimpleNamespace(drivers=[driver]),
                            teams={"target": owner}, game_mode="formula", season_no=3, week_index=9)
    manager.state = state
    # Use a deliberately different calendar to prove terms come from the native
    # helper, not an assumed 52-week season. Signing/normalization are never run.
    methods = {"academy": {"_seasons_until_age_cap": (lambda self, person: 2, False),
                           "signing_cost_m": (lambda self, person: .7, False),
                           "contract_for": (lambda self, name: None, False)},
               "state": {"_team_record": (lambda self, name: self.teams.get(name), False),
                         "current_season_start_week": (lambda self: 1 + (self.season_no - 1) * 60, False)}}
    monkeypatch.setattr(implementation, "academy_methods", lambda state: methods)
    planner = implementation.AcademyContractPlanner(state)
    args = {"self": manager, "team_name": "target", "driver_name": "Junior", "seasons": 20}
    before = deepcopy((state.teams, vars(driver)))
    signed = planner.prepare("academy.sign", args)
    assert set(signed) == {"clock", "effective", "proposal"}
    assert signed["clock"] == {"season": 3, "absolute_week": 130}
    assert signed["effective"] == {"season": 3, "start_week": 121, "end_week": 241}
    proposal = signed["proposal"]
    assert proposal["contract_kind"] == proposal["employer"]["kind"] == "academy"
    assert proposal["person"]["ref"] == "driver:" + str(id(driver)) and proposal["existing"] is None
    assert proposal["proposed"]["duration_seasons"] == 2 and proposal["proposed"]["annual_retainer_m"] == .7
    assert proposal["proposed"]["annual_salary_m"] is None
    proposal["person"]["nationality"] = "changed"
    args["extra_seasons"] = 2
    renewed = planner.prepare("academy.renew", args)
    assert renewed["effective"] == {"season": 3, "start_week": 1, "end_week": 301}
    assert renewed["proposal"]["existing"]["duration_seasons"] == 3
    assert renewed["proposal"]["proposed"]["duration_seasons"] == 5
    assert renewed["proposal"]["proposed"]["annual_retainer_m"] == .8
    assert renewed["proposal"]["person"]["nationality"] == "GB"
    assert before == (state.teams, vars(driver))
    state.academy_manager = SimpleNamespace()
    with pytest.raises(HookError, match="another native state"):
        planner.prepare("academy.renew", args)


def test_incompatible_academy_signatures_raise_without_importing_game():
    state = SimpleNamespace(game_mode="formula", academy_manager=SimpleNamespace(sign_driver=lambda: True))
    before = set(sys.modules)
    with pytest.raises(HookError):
        implementation.AcademyContractPlanner(state)
    assert not {"managers.academy_manager", "managers.driver", "game_state"} & (set(sys.modules) - before)
