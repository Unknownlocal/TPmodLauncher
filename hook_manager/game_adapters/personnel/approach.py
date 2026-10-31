# SPDX-License-Identifier: MPL-2.0
"""Prepare a team approach without starting talks or changing live contracts."""

from copy import copy
import inspect

from ...callables import method_group
from ..native_methods import native_signatures, native_target
from .native_methods import PERSONNEL
from ...manager import HookError
from ..employment import employer_identity, person_identity
from ..drivers.methods import driver_methods
from ..drivers.snapshot import DriverContractSnapshot
from ..staff.methods import staff_methods


class PersonnelApproach:
    """Own the native approach mapping and its small, read-only description."""

    target = native_target(PERSONNEL, "personnel.approach", "begin_approach")

    def __init__(self, state):
        self.state, self.manager = state, state.personnel_negotiation_manager
        if str(getattr(state, "game_mode", "formula")).lower() != "formula":
            raise HookError("Personnel approaches support Formula teams")
        resolved = self.target.resolve({})
        if (resolved is None or not isinstance(self.manager, resolved[0])
                or self.manager.state is not state
                or inspect.getattr_static(self.manager, "begin_approach") is not resolved[2]):
            raise HookError("Approach needs the mapped native manager entry point")
        self.start = method_group(self.manager, native_signatures(PERSONNEL, '_default_target_contract_start_week'))["_default_target_contract_start_week"][0]
        self.drivers = driver_methods(state)
        # Staff date helpers read live data without writes; check their
        # compatibility but retain no unused copy of their function map.
        staff_methods(state)

    def belongs(self, arguments):
        # Other careers and supplier/oval paths retain their native behaviour.
        return (arguments["self"] is self.manager
                and self.manager.state is self.state
                and str(arguments["candidate_type"] or "").strip().lower() in ("driver", "staff")
                and str(arguments["signer_kind"] or "team") == "team")

    def prepare(self, arguments):
        state, person = self.state, arguments["candidate"]
        # Match native input spelling without rewriting the eventual native call.
        kind = str(arguments["candidate_type"] or "").strip().lower()
        team = str(arguments["team_name"] or "").strip()
        if state.personnel_negotiation_manager is not self.manager or not self.belongs(arguments):
            raise HookError("Approach belongs to another career")
        context = dict(arguments["context"] or {})
        current = state.current_season_start_week()
        following = state.next_season_start()
        start = context.get("contract_start_week")
        if start is None:
            # Driver date queries normalise contracts. Reuse the existing small
            # destination-roster snapshot; never run them on the live manager.
            query = copy(self.manager)
            query.state = copy(state)
            if kind == "driver":
                draft = DriverContractSnapshot(state, person, team, self.drivers)
                query.state.driver_manager, person_for_date = draft.manager, draft.driver
            else:
                # These checked staff helpers only read rosters and team data.
                person_for_date = person
            start = self.start(query, kind, person_for_date, team, signer_kind="team", context=context)
        start = int(start)
        return {
            "clock": {"season": state.season_no, "absolute_week": current + state.week_index},
            "effective": {"season": state.season_no + int(start >= following), "start_week": start, "end_week": None},
            "proposal": {"action": "approach", "person": person_identity(person, kind),
                         "employer": employer_identity(state.teams[team], team, "team"),
                         "role": context.get("role") or getattr(person, "role", "main")},
        }

    @staticmethod
    def input_refusal(arguments):
        """Keep a malformed native date separate from a broken mod validator."""
        value = dict(arguments["context"] or {}).get("contract_start_week")
        if value is not None:
            try:
                int(value)
            except (TypeError, ValueError):
                return "Unable to determine the intended contract season."

    @staticmethod
    def refuse(message):
        """Native screens already display the returned approach-failure reason."""
        return False, message, None
