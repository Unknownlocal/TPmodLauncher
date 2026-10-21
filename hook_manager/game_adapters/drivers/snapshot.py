# SPDX-License-Identifier: MPL-2.0
"""Give native date/status queries private driver contracts to normalize.

Only the proposed driver and the destination team's relevant roster are copied.
Calendar/entry helpers borrow team definitions and adopted regulation data
read-only. Nothing from those borrowed objects is returned to a mod.
"""

from types import MethodType, SimpleNamespace

from ...manager import HookError, data_copy
from ..employment import BONUS_KEYS


_CONTRACT_KEYS = ("team", "role", "status", "priority", "start_week", "length_weeks", "salary_m",
                  "academy_loan", "loan_owner", "pending_status_change", *BONUS_KEYS)
_PERSON_KEYS = ("name", "nationality", "age", "role", "team", "retired", "retiring", "deceased",
                "cornering", "braking", "consistency", "smoothness", "control")


def _contract(value):
    if value is None:
        return None
    if type(value) is not dict:
        raise HookError("Driver planning needs ordinary native contract records")
    return data_copy({key: value[key] for key in _CONTRACT_KEYS if key in value})


def _person(original):
    result = SimpleNamespace(**{key: data_copy(getattr(original, key)) for key in _PERSON_KEYS if hasattr(original, key)})
    result.contract = _contract(getattr(original, "contract", None))
    result.future_contract = _contract(getattr(original, "future_contract", None))
    return result


def _bind(owner, methods):
    for name, (function, static) in methods.items():
        setattr(owner, name, function if static else MethodType(function, owner))


class DriverContractSnapshot:
    def __init__(self, source, driver, employer, methods):
        # The checked entry helpers only read these team/regulation definitions.
        # Borrow them so each proposal avoids copying stable regulation data;
        # contracts and the query cache, which native helpers can edit, are owned.
        self.state = SimpleNamespace(
            season_no=source.season_no, week_index=source.week_index,
            player_team=getattr(source, "player_team", None), game_mode=getattr(source, "game_mode", "formula"),
            teams={employer: source.teams[employer]},
            regulation_manager=getattr(source, "regulation_manager", None),
            formula_regulation_governance=getattr(source, "formula_regulation_governance", None),
        )
        _bind(self.state, methods["state"])
        self.driver, roster = None, []
        # Seat counts and teammate status need only the destination roster and
        # candidate, not every driver's history or the rest of the career.
        for original in source.driver_manager.drivers:
            relevant = original is driver or any(
                type(record) is dict and record.get("team") == employer
                for record in (getattr(original, "contract", None), getattr(original, "future_contract", None)))
            if relevant:
                clone = _person(original)
                roster.append(clone)
                if original is driver:
                    self.driver = clone
        if self.driver is None:
            # Native signing also accepts newly generated candidates before a
            # caller adds them to the roster. Do not invent roster membership.
            self.driver = _person(driver)
        # Live cache keys refer to live drivers. A fresh cache prevents queries
        # on copied records from returning results for the original identities.
        self.manager = SimpleNamespace(state=self.state, drivers=roster, _ai_signing_query_cache=None)
        self.state.driver_manager = self.manager
        _bind(self.manager, methods["driver"])
        schedule = self.manager._schedule_contract_status_change

        def schedule_without_effects(*args, **kwargs):
            # Status writes are needed for the predicted contract; emails and
            # morale changes are not. This checked helper applies them afterward.
            kwargs["apply_effects"] = False
            return schedule(*args, **kwargs)

        self.manager._schedule_contract_status_change = schedule_without_effects
