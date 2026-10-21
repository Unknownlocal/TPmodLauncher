# SPDX-License-Identifier: MPL-2.0
"""Prepare academy membership proposals without signing, renewal or billing."""

from types import MethodType, SimpleNamespace

from ...manager import HookError, data_copy
from ..employment import person_identity
from .methods import academy_methods
from .terms import academy_terms


class AcademyContractPlanner:
    def __init__(self, state):
        # Consumers own compatibility reporting and installation; this native
        # support class only checks and prepares terms for one Formula career.
        if str(getattr(state, "game_mode", "formula")).lower() != "formula":
            raise HookError("Academy planning supports Formula mode")
        self.state, self.manager = state, state.academy_manager
        self.methods = academy_methods(state)

    def prepare(self, target, arguments):
        if (type(arguments) is not dict or arguments.get("self") is not self.manager
                or self.state.academy_manager is not self.manager or self.manager.state is not self.state):
            raise HookError("Academy planner belongs to another native state")
        if target not in ("academy.sign", "academy.renew"):
            raise HookError("Unsupported academy planning target")
        season = self.state.season_no
        if type(season) is not int or type(self.state.week_index) is not int:
            raise HookError("Academy proposal needs the native career clock")
        employer, name = arguments["team_name"], arguments["driver_name"]
        if type(employer) is not str or not employer or type(name) is not str or not name:
            raise HookError("Academy proposal needs native team and driver names")
        owner = self.methods["state"]["_team_record"][0](self.state, employer)
        if type(owner) is not dict or not owner:
            raise HookError("Academy proposal needs an existing team")
        driver = next((person for person in self.state.driver_manager.drivers if person.name == name), None)
        if driver is None:
            raise HookError("Academy proposal needs an existing driver")
        # The retained queries only read the borrowed career and candidate, or
        # copy one membership record. No academy-state normalization is needed.
        manager = SimpleNamespace(state=self.state)
        for method, (function, static) in self.methods["academy"].items():
            setattr(manager, method, function if static else MethodType(function, manager))
        if target == "academy.sign":
            # This cap is part of native term normalization. Full slot/history/
            # employment eligibility checks remain with native signing later.
            cap = manager._seasons_until_age_cap(driver)
            if cap <= 0:
                raise HookError("Native academy signing has no remaining term")
            term = max(1, min(cap, int(arguments["seasons"])))
            fee = manager.signing_cost_m(driver)
            proposed = {"start_season": season, "term_seasons": term, "annual_cost_m": fee}
            prior = manager.contract_for(name)
        else:
            # Renewal selects the destination team's canonical record, not a
            # potentially stale driver.academy_contract link. No normalizer runs.
            academy = owner.get("academy", {})
            if type(academy) is not dict or type(academy.get("contracts")) is not dict:
                raise HookError("Academy renewal needs existing membership records")
            prior = academy["contracts"].get(name)
            if type(prior) is not dict or not prior:
                raise HookError("Academy renewal needs an existing contract")
            proposed = dict(prior)
            # Native renewal adds the clamped extension to the original full
            # term, not to its remaining seasons, and does not recalculate fees.
            proposed["term_seasons"] = (
                max(1, int(prior.get("term_seasons", 1)))
                + max(1, min(5, int(arguments["extra_seasons"])))
            )
        calendar = self.methods["state"]["current_season_start_week"][0]
        terms = academy_terms(proposed, season, calendar)
        # A renewal's dates can begin in the past. Evaluate this decision under
        # current rules unless its stored membership start is still in the future.
        return data_copy({
            "clock": {"season": season, "absolute_week": calendar(self.state) + self.state.week_index},
            "effective": {"season": max(season, int(proposed.get("start_season", season))),
                          "start_week": terms["start_week"], "end_week": terms["end_week"]},
            "proposal": {"action": "sign" if target == "academy.sign" else "renew",
                         "person": person_identity(driver, "driver"),
                         "employer": {"ref": "academy:" + str(id(owner)), "name": employer, "kind": "academy"},
                         "contract_kind": "academy", "existing": academy_terms(prior, season, calendar),
                         "proposed": terms},
        })
