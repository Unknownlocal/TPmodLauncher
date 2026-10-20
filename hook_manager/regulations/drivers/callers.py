# SPDX-License-Identifier: MPL-2.0
"""Keep offer preparation private and stop refused player confirmations."""

from copy import deepcopy

from .cancellation import DriverCancellation
from .preparation import publish_preparation
from .queries import DriverQueries


class DriverCallers:
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
            previous = getattr(guards._local, "driver_caller", None)
            if role in ("active", "count", "status", "entries", "next_entries", "personalities", "career", "prestige"):
                if previous is None or getattr(guards._local, "committing_driver", False):
                    return original(*args, **kwargs)
                return self.queries.run(role, original, bound, previous)
            if (role == "offer" and values["row"].get("candidate_type") != "driver"
                    or role == "uncontested" and values["candidate_type"] != "driver"):
                return original(*args, **kwargs)
            frame = {"role": role, "initiator": "player", "refusal": None,
                     "cancel": role == "decision" and bool(values["accept"]),
                     # Nested offer execution must see the same temporary
                     # personality/prestige values as its enclosing confirmation.
                     "normalizations": previous["normalizations"] if previous else {}}
            if role == "offer":
                frame["initiator"] = values["row"].get("initiated_by", "unknown")
                if frame["initiator"] not in ("player", "ai", "system"):
                    frame["initiator"] = "unknown"
            guards._local.driver_caller = frame
            # Freeze owner changes and shutdown through the entire caller, not
            # just the nested signing guard, so preparation uses one registry.
            with guards._lock:
                guards._active += 1
            try:
                if role == "offer":
                    return self._offer(original, bound, frame, previous)
                try:
                    result = original(*bound.args, **bound.kwargs)
                except DriverCancellation as refused:
                    return False, str(refused)
                return (False, frame["refusal"]) if frame["refusal"] else result
            finally:
                # Native prechecks may return or raise without ever signing.
                # Preserve their usual initialization unless a mod refused it.
                try:
                    if not frame["refusal"]:
                        publish_preparation(frame)
                finally:
                    guards._local.driver_caller = previous
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
        try:
            result = original(*bound.args, **bound.kwargs)
        finally:
            if frame["refusal"]:
                row["execution_block_reason"] = frame["refusal"]
            else:
                # Native failure still keeps its own offer normalization. Only
                # a regulation refusal cancels these writes.
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
                # after False. A regulation refusal must stop before those writes.
                raise DriverCancellation(frame["refusal"])
        return result
