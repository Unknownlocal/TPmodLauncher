# SPDX-License-Identifier: MPL-2.0
"""Prepare final driver employment terms without signing or changing a career."""

import math

from ...manager import HookError, data_copy
from .methods import driver_methods
from .snapshot import DriverContractSnapshot
from ..employment import BONUS_KEYS, contract_terms, person_identity
from .terms import return_owner_loans


class DriverContractPlanner:
    def __init__(self, state):
        # Native support raises on incompatibility. The consuming adapter owns
        # reporting and whether to skip its integration; no rule system is needed.
        if str(getattr(state, "game_mode", "formula")).lower() != "formula":
            raise HookError("Driver employment planning supports Formula mode")
        self.state, self.manager = state, state.driver_manager
        self.methods = driver_methods(state)

    def prepare(self, arguments):
        if (type(arguments) is not dict or arguments.get("self") is not self.manager
                or self.state.driver_manager is not self.manager or self.manager.state is not self.state):
            raise HookError("Driver planner belongs to another native state")
        employer, original = arguments["team_name"], arguments["driver"]
        if type(employer) is not str or not employer or type(self.state.teams.get(employer)) is not dict or not self.state.teams[employer]:
            raise HookError("Driver proposal needs an existing named team")
        if type(self.state.season_no) is not int or type(self.state.week_index) is not int:
            raise HookError("Driver proposal needs the native career clock")
        years, role = arguments["years"], arguments["role"]
        if type(years) is not int or years < 1 or type(role) is not str or not role or arguments["entry_id"]:
            raise HookError("Unsupported driver employment terms")
        # A new proposal needs a real salary. Older saved agreements may omit
        # money fields, which contract_terms deliberately preserves as null.
        if type(arguments["salary_m"]) not in (int, float) or not math.isfinite(arguments["salary_m"]):
            raise HookError("Driver contract money needs finite native numbers")
        scratch = DriverContractSnapshot(self.state, original, employer, self.methods)
        manager, driver, state = scratch.manager, scratch.driver, scratch.state
        current_start, next_start = state.current_season_start_week(), state.next_season_start()
        # Driver signing counts from the season start, including its offset.
        # Do not reuse the aero/chassis week convention here.
        week = current_start + state.week_index
        requested = arguments["contract_start_week"]
        if requested is not None:
            requested = int(requested)
            if requested not in (current_start, next_start):
                raise HookError("Native driver start must be this or next season")
        # Describe the prior agreement before private loan-return normalization
        # can remove it. Native status queries also write into their argument.
        prior = data_copy(driver.contract or driver.future_contract)
        if prior and prior.get("team"):
            prior_status = manager.contract_status_at_week(prior, week)
            existing = contract_terms(prior, status=prior_status)
        else:
            existing = None
        cover = manager.current_reserve_cover_weeks(employer, driver, role, requested)
        if cover is not None and years != 1:
            raise HookError("Native reserve cover requires a one-season request")
        return_owner_loans(manager, driver, employer, requested, years, next_start)
        capacity = self.methods["globals"]["driver_contract_capacity"][0]
        active = manager.active_contract(driver, week)
        current_limit = max(0, int(capacity(manager, employer, role, week=week)))
        start = requested if requested is not None else (
            current_start if active is None and manager.active_driver_count(employer, role, week) < current_limit else next_start)
        bonuses = arguments["bonuses"] if isinstance(arguments["bonuses"], dict) else {}
        # Preserve the existing plain-data bounds before float conversion, so
        # custom objects cannot run conversion methods during proposal planning.
        bonus_values = data_copy({key: bonuses[key] for key in BONUS_KEYS if key in bonuses})
        # Mirror the inspected native conversion without reaching into a nested
        # function or requiring a particular signing implementation layout.
        values = {}
        for key in BONUS_KEYS:
            try:
                values[key] = max(0.0, float(bonus_values.get(key, 0.0) or 0.0))
            except Exception:
                values[key] = 0.0
        status = self.methods["globals"]["canonical_driver_status"][0](arguments["priority"])
        proposed = dict(team=employer, role=role, status=status, priority=status, salary_m=arguments["salary_m"],
                        start_week=week if cover is not None else start, length_weeks=cover if cover is not None else 52 * years,
                        **values)
        # Native implicit current signing cancels a scheduled agreement, while
        # an explicit current start preserves it. Both writes stay on the copy.
        if start <= week:
            driver.contract = proposed
            if requested is None:
                driver.future_contract = None
        else:
            driver.future_contract = proposed
        # The native post-signing hierarchy can change the promised status.
        # Run those checked calculations on cloned contracts, suppressing effects.
        if employer == state.player_team and role == "main":
            manager.reconcile_player_main_statuses(employer, driver, proposed, start)
        elif employer != state.player_team and role == "main":
            manager.normalize_ai_team_hierarchy(employer)
            if start <= week and current_limit == 2:
                manager.ensure_main_status_consistency(employer, week, apply_effects=False)
        terms = contract_terms(proposed)
        # The person may have no independent role attribute; employment role is
        # carried in the proposed terms. Never invent an identity field from it.
        person = person_identity(original, "driver")
        # Keep native proposal data independent of regulation schema/operation
        # metadata so other trusted game adapters can use the same preparation.
        return data_copy({
            "clock": {"season": state.season_no, "absolute_week": week},
            "effective": {"season": state.season_no + int(start == next_start),
                          "start_week": terms["start_week"], "end_week": terms["end_week"]},
            "proposal": {"action": "renew" if prior and prior.get("team") == employer else "sign", "person": person,
                         "employer": {"ref": "team:" + str(id(self.state.teams[employer])), "name": employer, "kind": "team"},
                         "contract_kind": "employment", "existing": existing, "proposed": terms},
        })
