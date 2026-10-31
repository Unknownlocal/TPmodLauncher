# SPDX-License-Identifier: MPL-2.0
"""Describe submitted terms through the existing native contract planners."""

import inspect
import math

from ...callables import method_group
from ..native_methods import native_signatures
from .native_methods import PERSONNEL
from ...manager import HookError, data_copy
from ..employment import BONUS_KEYS


class PersonnelOffer:
    """Translate the shared negotiation's offer fields into signing arguments."""

    def __init__(self, state, planners):
        self.planners = planners
        self.record = method_group(state.personnel_negotiation_manager, native_signatures(PERSONNEL, 'get_record'))["get_record"][0]
        self.signatures = {kind: inspect.signature(getattr(type(planner.manager), "sign_" + kind))
                           for kind, planner in planners.items()}

    def row(self, values):
        """Read the existing negotiation without creating or updating a record."""
        return self.record(values["self"], values["candidate_type"], values["candidate"],
                           values["team_name"], signer_kind=values["signer_kind"])

    @staticmethod
    def kind(values):
        """Use the same spelling accepted by native candidate record lookup."""
        return str(values["candidate_type"] or "").strip().lower()

    def arguments(self, values, row):
        """Return signing inputs, raising an ordinary input error for bad terms."""
        # Match _execute_offer's field precedence and coercions without running
        # it, native eligibility, or a signer. Planners own contract normalisation.
        kind = self.kind(values)
        planner = self.planners[kind]
        offer, context = values["offer"] or {}, row.get("context") or {}
        # Native drivers prefer salary_m; staff prefer salary when both exist.
        salary_keys = ("salary_m", "salary") if kind == "driver" else ("salary", "salary_m")
        arguments = dict(self=planner.manager, team_name=str(row.get("team_id") or ""),
                         salary_m=float(offer.get(salary_keys[0], offer.get(salary_keys[1], 0.0)) or 0.0),
                         years=int(offer.get("years", 1) or 1),
                         bonuses={key: offer.get(key, 0.0) for key in BONUS_KEYS})
        arguments[kind] = values["candidate"]
        if arguments["years"] < 1 or not math.isfinite(arguments["salary_m"]):
            raise ValueError("Invalid contract offer terms")
        try:
            arguments["bonuses"] = data_copy(arguments["bonuses"])
        except HookError as error:
            raise ValueError("Invalid contract bonuses") from error
        if kind == "driver":
            arguments.update(role=str(offer.get("role") or context.get("role") or "main"),
                             priority=str(offer.get("status", offer.get("priority", "equal")) or "equal"))
            if offer.get("entry_id"):
                raise ValueError("Formula driver offers do not support an entry ID")
            if offer.get("contract_season") or context.get("contract_season"):
                raise ValueError("Formula driver offers use an absolute start week")
        else:
            arguments["signer_kind"] = str(row.get("signer_kind") or "team")
            # Staff conversion is strict; the driver's existing planner instead
            # tolerates an unparseable bonus and substitutes zero.
            if any(not math.isfinite(float(value or 0.0)) for value in arguments["bonuses"].values()):
                raise ValueError("Invalid staff contract bonus")
        if context.get("contract_start_week") is not None:
            arguments["contract_start_week"] = int(context["contract_start_week"])
            if arguments["contract_start_week"] not in (planner.state.current_season_start_week(), planner.state.next_season_start()):
                raise ValueError("Unsupported contract offer start")
        bound = self.signatures[kind].bind(**arguments)
        bound.apply_defaults()
        return dict(bound.arguments)
