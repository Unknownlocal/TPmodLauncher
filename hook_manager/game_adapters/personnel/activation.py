# SPDX-License-Identifier: MPL-2.0
"""Describe future employment contracts selected by native weekly activation."""

from copy import copy
from types import MethodType, SimpleNamespace

from ...manager import HookError, data_copy
from ..employment import contract_terms, person_identity
from .activation_methods import activation_methods
from .contract_activation import ContractActivation
from .market_graph import MarketGraph


class ActivationPlanner:
    """Use native activation on copies to describe which agreements would take effect.

    This only prepares rule input. The enclosing caller must protect these reads,
    validate the proposals and control any later changes to the live career.
    """

    def __init__(self, state, *, kinds=("driver", "staff")):
        if (type(kinds) is not tuple or not kinds or any(kind not in ("driver", "staff") for kind in kinds)
                or len(kinds) != len(set(kinds))):
            raise HookError("Activation planning needs distinct supported personnel kinds")
        if str(getattr(state, "game_mode", "formula")).lower() != "formula":
            raise HookError("Employment activation planning supports Formula mode")
        self.state, self.kinds = state, kinds
        self.managers = {kind: getattr(state, kind + "_manager") for kind in kinds}
        self.methods = activation_methods(state, kinds)

    def prepare(self, current_week, *, evaluation_season=None):
        """Describe agreements due in one week without promoting live contracts."""
        state = self.state
        if any(getattr(state, kind + "_manager") is not manager or manager.state is not state
               for kind, manager in self.managers.items()):
            raise HookError("Activation planner belongs to another native state")
        season = state.season_no if evaluation_season is None else evaluation_season
        if any(type(value) is not int for value in (current_week, season, state.season_no, state.week_index)) or season < 1:
            raise HookError("Activation planning needs native integer calendar values")
        # A rollover may ask about another season before the live clock advances.
        # Use native season boundaries, rather than assuming a fixed year length.
        calendar = copy(state)
        calendar.season_no = season
        first = self.methods["state"]["current_season_start_week"][0]
        following = self.methods["state"]["next_season_start"][0]
        start, end, live_start = first(calendar), following(calendar), first(state)
        if any(type(value) is not int for value in (start, end, live_start)) or not start <= current_week < end:
            raise HookError("Activation week is outside its evaluation season")
        # Rules see the live career clock separately from each agreement's dates.
        clock = {"season": state.season_no, "absolute_week": live_start + state.week_index}
        # GameState.drivers is a read-only property. A plain view lets the native
        # activation body read our roster without keeping that live descriptor.
        private, graph, people, activations = SimpleNamespace(**vars(state)), MarketGraph(), {}, []

        def person(original):
            if id(original) not in people:
                clone = copy(original)
                # Native activation mutates the incoming agreement. Copy future
                # containers together so shared records and duplicate people keep
                # their native behavior. Existing contracts are read-only inputs.
                clone.future_contract = graph.copy(getattr(original, "future_contract", None))
                people[id(original)] = clone
            return people[id(original)]

        for kind in self.kinds:
            manager = copy(self.managers[kind])
            manager.state = private
            for name, (function, static) in self.methods[kind].items():
                setattr(manager, name, function if static else MethodType(function, manager))
            roster = state.drivers if kind == "driver" else self.managers[kind].staff
            clones = [person(original) for original in roster]
            if kind == "driver":
                private.drivers = manager.drivers = clones
            else:
                manager.staff = clones
            setattr(private, kind + "_manager", manager)
            originals = {id(people[id(original)]): original for original in roster}

            def observe(clone, employer, *, kind=kind, manager=manager, originals=originals):
                """Capture the promoted agreement where native code would set its team."""
                original = originals[id(clone)]
                if type(employer) is not str or not employer:
                    raise HookError("Activation needs an existing unambiguous employer")
                owner = state.teams.get(employer)
                if owner is not None and not isinstance(owner, dict):
                    raise HookError("Activation needs an ordinary team record")
                supplier = manager._supplier_by_id(employer) if kind == "staff" else None
                if owner is not None and supplier is not None:
                    # Stored staff agreements have only an owner key. If it names
                    # both kinds of employer, we cannot give rules a reliable ref.
                    raise HookError("Stored staff employment has an ambiguous employer")
                employer_kind = "team" if owner is not None else "supplier"
                owner = owner if owner is not None else supplier
                if owner is None:
                    raise HookError("Activation needs an existing unambiguous employer")
                role = manager.normalize_role(original.role) if kind == "staff" else None
                terms = contract_terms(clone.contract, role=role)
                if terms is None or clone.contract.get("team") != employer:
                    # Native can fall back to the person's current team. That
                    # fallback cannot supply a missing owner in the stored rule input.
                    raise HookError("Activation needs a complete stored employment agreement")
                prepared = data_copy({
                    "clock": clock,
                    "effective": {"season": season, "start_week": terms["start_week"], "end_week": terms["end_week"]},
                    "proposal": {"action": "activate", "person": person_identity(original, kind),
                                 "employer": {"ref": employer_kind + ":" + str(id(owner)), "name": employer,
                                              "kind": employer_kind},
                                 "contract_kind": "employment", "existing": contract_terms(getattr(original, "contract", None), role=role),
                                 "proposed": terms},
                })
                activations.append(ContractActivation(kind, original, prepared))

            # The native setter would update driver caches, colours and career
            # callbacks. Observe its input instead; none of those effects may run
            # before the enclosing transition has validated the agreements.
            manager.set_team = observe
            function, static = self.methods["state"]["_activate_future_" + kind + "_contracts"]
            activate = function if static else MethodType(function, private)
            activate(current_week)
        return tuple(activations)
