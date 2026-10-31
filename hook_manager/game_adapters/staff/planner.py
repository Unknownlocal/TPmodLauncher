# SPDX-License-Identifier: MPL-2.0
"""Prepare native team/supplier staff terms without signing or changing a career."""

import math
from types import MethodType, SimpleNamespace

from ...manager import HookError, data_copy
from ..employment import BONUS_KEYS, contract_terms, employer_identity, person_identity
from .methods import staff_methods
from ..personnel.payroll import prepare_payroll


class StaffContractPlanner:
    def __init__(self, state):
        # Check compatibility once. The consuming adapter owns CLI/log reporting
        # and deciding whether to skip installation when construction fails.
        if str(getattr(state, "game_mode", "formula")).lower() != "formula":
            raise HookError("Staff employment planning supports Formula mode")
        self.state, self.manager = state, state.staff_manager
        self.methods = staff_methods(state)

    def prepare(self, arguments, *, payroll_checks=None):
        if (type(arguments) is not dict or arguments.get("self") is not self.manager
                or self.state.staff_manager is not self.manager or self.manager.state is not self.state):
            raise HookError("Staff planner belongs to another native state")
        if type(self.state.season_no) is not int or type(self.state.week_index) is not int:
            raise HookError("Staff proposal needs the native career clock")
        employer, staff = arguments["team_name"], arguments["staff"]
        years, salary = arguments["years"], arguments["salary_m"]
        if type(employer) is not str or not employer or type(years) is not int or years < 1:
            raise HookError("Unsupported staff employment terms")
        if type(salary) not in (int, float) or not math.isfinite(salary):
            raise HookError("Staff contract money needs finite native numbers")
        # These checked helpers only read native rosters, teams and suppliers.
        # Bind retained functions to a small query owner. Full signing eligibility,
        # prestige getters and post-signing morale effects are not run here.
        manager = SimpleNamespace(state=self.state, staff=self.manager.staff)
        for name, (function, static) in self.methods["staff"].items():
            setattr(manager, name, function if static else MethodType(function, manager))
        role = manager.normalize_role(staff.role)
        kind = str(arguments["signer_kind"] or "team").strip().lower()
        if kind == "auto":
            # Native signing tries the team first, even when a supplier uses
            # the same key. These role checks resolve ownership, not mod policy.
            if manager.team_can_sign_role(employer, staff.role):
                kind = "team"
            elif manager.supplier_can_sign_role(employer, staff.role):
                kind = "supplier"
            else:
                raise HookError("Native staff employer kind could not be resolved")
        if kind == "team":
            owner = self.state.teams.get(employer)
            if type(owner) is not dict or not owner:
                raise HookError("Staff proposal needs an existing named team")
        elif kind == "supplier":
            owner = manager._supplier_by_id(employer)
            if owner is None:
                raise HookError("Staff proposal needs an existing supplier")
        else:
            raise HookError("Unsupported native staff employer kind")
        current_start = self.methods["state"]["current_season_start_week"][0](self.state)
        next_start = self.methods["state"]["next_season_start"][0](self.state)
        week = current_start + self.state.week_index
        cover = arguments["allow_current_cover"]
        start = arguments["contract_start_week"]
        if start is not None:
            start = int(start)
            if start not in (current_start, next_start):
                raise HookError("Native staff start must be this or next season")
        # An explicit start takes precedence over occupied-seat scheduling.
        elif manager.team_has_active_staff(employer, role, week=week):
            start = next_start
        else:
            start = week if cover else current_start
        # Native staff cover truncates even a multi-year request. A future start
        # keeps the requested length; an explicit current start can be backdated.
        duration = max(1, next_start - start) if cover and start <= week else 52 * years
        proposed = {"team": employer, "salary_m": salary, "start_week": start, "length_weeks": duration}
        bonuses = arguments["bonuses"]
        if isinstance(bonuses, dict):
            # Staff signing uses float conversion without the driver's zero
            # clamp. No bonus object means fields stay absent, not zero-filled.
            proposed.update({key: float(bonuses.get(key, 0.0) or 0.0) for key in BONUS_KEYS})
        # Describe the previous agreement without replacing either live record.
        # Signing, including clearing a future record, belongs to the game later.
        prior = getattr(staff, "contract", None) or getattr(staff, "future_contract", None)
        terms = contract_terms(proposed, role=role)
        existing = contract_terms(prior, role=role)
        employer_record = employer_identity(owner, employer, kind)
        if payroll_checks is not None:
            # Supplier employment uses the same validation scope but is outside
            # team payroll. Its context says so without reading either roster.
            employer_record["payroll"] = (prepare_payroll(self.state, employer, staff, "staff", proposed,
                                                         week, next_start, payroll_checks)
                                           if kind == "team" else None)
        return data_copy({
            "clock": {"season": self.state.season_no, "absolute_week": week},
            "effective": {"season": self.state.season_no + int(start == next_start),
                          "start_week": terms["start_week"], "end_week": terms["end_week"]},
            "proposal": {"action": "renew" if prior and prior.get("team") == employer else "sign",
                         "person": person_identity(staff, "staff"),
                         "employer": employer_record,
                         "contract_kind": "employment", "existing": existing, "proposed": terms},
        })
