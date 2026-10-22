# SPDX-License-Identifier: MPL-2.0
"""Keep offer preparation private and stop refused player confirmations."""

from copy import copy, deepcopy

from .cancellation import PersonnelCancellation
from .preparation import PRESTIGE_FIELDS, preparation_draft, publish_preparation
from ..drivers.queries import DriverQueries


class PersonnelCallers:
    def __init__(self, guards, state):
        self.guards, self.state = guards, state
        self.queries = DriverQueries()

    def wrap(self, role, original, signature):
        def wrapped(*args, **kwargs):
            guards = self.guards
            if guards.closed or not guards.owners:
                return original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            # PrestigeManager has no career-state member; identify its instance
            # directly. Other managers identify the career through their state.
            belongs = (values["self"] is self.state.prestige_manager if role == "prestige"
                       else getattr(values["self"], "state", None) is self.state)
            if not belongs:
                return original(*args, **kwargs)
            previous = getattr(guards._local, "personnel_caller", None)
            if role in ("active", "count", "status", "entries", "next_entries", "personalities", "career", "prestige"):
                if previous is None or getattr(guards._local, "committing_personnel", False):
                    return original(*args, **kwargs)
                if role == "prestige":
                    # Staff and driver prechecks use this getter. Its lazy team
                    # initialization must stay private until either signing allows.
                    values["team"] = preparation_draft(previous, values["team"], PRESTIGE_FIELDS)
                    return original(*bound.args, **bound.kwargs)
                return self.queries.run(role, original, bound, previous)
            if (role == "offer" and values["row"].get("candidate_type") not in guards.planners
                    or role == "uncontested" and values["candidate_type"] not in guards.planners):
                # Compatibility may have disabled one kind. Preserve that kind's
                # native path while retaining guards for the compatible kind.
                return original(*args, **kwargs)
            frame = {"role": role, "initiator": "player", "refusal": None,
                     "cancel": role == "decision" and bool(values["accept"]),
                     # Nested offer execution must see the same temporary
                     # personality/prestige values as its enclosing confirmation.
                     "normalizations": previous["normalizations"] if previous else {}}
            # A native precheck may return before signing reaches a guard. Do not
            # let that call inherit an earlier attempt's validation outcome.
            guards._local.outcome = None
            if role == "offer":
                frame["initiator"] = values["row"].get("initiated_by", "unknown")
                if frame["initiator"] not in ("player", "ai", "system"):
                    frame["initiator"] = "unknown"
            guards._local.personnel_caller = frame
            # Freeze owner changes and shutdown through the entire caller, not
            # just the nested signing guard, so preparation uses one registry.
            with guards._lock:
                guards._active += 1
            try:
                if role == "offer":
                    return self._offer(original, bound, frame, previous)
                try:
                    result = original(*bound.args, **bound.kwargs)
                except PersonnelCancellation as refused:
                    return False, str(refused)
                return (False, frame["refusal"].message) if frame["refusal"] else result
            finally:
                # Native prechecks may return or raise without ever signing.
                # Preserve their usual initialization unless a guard refused it.
                try:
                    if not frame["refusal"]:
                        publish_preparation(frame)
                finally:
                    guards._local.personnel_caller = previous
                    with guards._lock:
                        guards._active -= 1
        return wrapped

    def _offer(self, original, bound, frame, previous):
        row = bound.arguments["row"]
        draft = dict(row)
        # Copy together: an offer can also be the context's planned_offer.
        # Separate copies would make native normalization score it twice.
        draft.update(deepcopy({key: row[key] for key in ("offer", "context") if isinstance(row.get(key), dict)}))
        bound.arguments["row"] = draft
        if row.get("candidate_type") == "staff" and (draft.get("context") or {}).get("entry_id"):
            # The native shared caller can set this Oval hint even in Formula.
            # Read from a shallow staff copy until signing admission, then use
            # the original staff object for the actual contract and effects.
            live = bound.arguments["candidate"]
            frame["staff_candidate"] = live, copy(live)
            bound.arguments["candidate"] = frame["staff_candidate"][1]
        try:
            result = original(*bound.args, **bound.kwargs)
        finally:
            if frame["refusal"]:
                row["execution_block_reason"] = frame["refusal"].message
            else:
                # Native failure still keeps its own offer normalization. Only
                # a guard refusal cancels these writes.
                # The checked normalizer updates only these term/score fields.
                # Preserve live dictionary identities, including planned offers.
                pairs = [(row.get("offer"), draft.get("offer")),
                         ((row.get("context") or {}).get("planned_offer"),
                          (draft.get("context") or {}).get("planned_offer"))]
                for live, prepared in pairs:
                    if isinstance(live, dict) and isinstance(prepared, dict):
                        for key in ("years", "interest_pct", "interest_breakdown"):
                            if key in prepared:
                                live[key] = prepared[key]
                for key in ("offer_score", "execution_block_reason"):
                    if key in draft:
                        row[key] = draft[key]
        if frame["refusal"] and previous and (previous["cancel"] or previous["role"] == "uncontested"):
            previous["refusal"] = frame["refusal"]
            if previous["cancel"]:
                # Native player acceptance tries backups and closes the decision
                # after False. A guard refusal must stop before those writes.
                raise PersonnelCancellation(frame["refusal"].message)
        return result
