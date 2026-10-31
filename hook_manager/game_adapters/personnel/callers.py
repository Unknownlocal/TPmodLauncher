# SPDX-License-Identifier: MPL-2.0
"""Keep offer preparation private and distinguish policy refusal from unavailability."""

from copy import copy, deepcopy
from contextlib import ExitStack, nullcontext
import threading

from ..validation_cancellation import ValidationCancellation
from .market_preparation import MarketPreparation
from .preparation import PRESTIGE_FIELDS, preparation_draft, publish_preparation
from .rollover import rollover_wrapper
from ..drivers.queries import DriverQueries
from ..drivers.query_inputs import DriverQueryInputs, discard_query_inputs


# These native entry points return None, including when their work is stopped.
_MARKET_CALLERS = {"due", "ai_staff", "ai_drivers"}
_GETTERS = {"count", "status", "entries", "next_entries", "personalities", "career", "prestige"}


class PersonnelCallers:
    def __init__(self, guards, state):
        self.guards, self.state = guards, state
        self.queries = DriverQueries()

    def wrap(self, role, original, signature):
        if role == "rollover":
            return rollover_wrapper(self.guards, self.state, original, signature)
        if role == "active":
            # This frequently called method has a checked, fixed signature.
            # Select its direct wrapper once instead of binding every lookup.
            return self._active_wrapper(original)

        def wrapped(*args, **kwargs):
            guards = self.guards
            if guards.closed or not guards.owners:
                return original(*args, **kwargs)
            previous = getattr(guards._local, "personnel_caller", None)
            # These reads need no argument rewriting outside preparation or once
            # signing is admitted. Let native code also report argument errors;
            # thousands of nested getters need not bind the same arguments twice.
            if role in _GETTERS and (previous is None or getattr(guards._local, "committing_personnel", False)):
                return original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            market = previous["market"] if previous else None
            if role == "mail":
                # Signing uses live managers and can send through their real
                # state's mailbox. Bring that mail into this caller's view too;
                # calls for another career keep their original owner.
                if market is not None and market.mail is not None and (
                        values["self"] is market.mail.live or values["self"] is market.mail.private):
                    return market.mail.send(original, bound)
                return original(*bound.args, **bound.kwargs)
            # PrestigeManager has no career-state member; identify its instance
            # directly. Other managers identify the career through their state.
            belongs = (values["self"] is self.state.prestige_manager if role == "prestige"
                       else getattr(values["self"], "state", None) is self.state
                       or market is not None and getattr(values["self"], "state", None) is market.state)
            if not belongs:
                return original(*args, **kwargs)
            if role == "submit":
                return self._submit(original, bound, previous)
            if role == "ai_failure":
                result = self._ai_failure(original, bound, previous)
                if market is not None:
                    # Only this offer failed; competing teams are still pending.
                    market.checkpoint(values["row"])
                return result
            if role in ("finish", "stage", "notice", "debug"):
                if market is None:
                    return original(*args, **kwargs)
                market.bind(bound)
                row = bound.arguments["source_row" if role == "finish" else "player_row" if role == "stage" else "row"]
                # These helpers retain their native logic/return values. The
                # scope attaches their sends to a record; only finish/stage
                # also establish a completed candidate checkpoint.
                with market.mail_for(row):
                    result = original(*bound.args, **bound.kwargs)
                if role in ("finish", "stage"):
                    market.checkpoint(row, candidate_scope=True)
                return result
            if role in _GETTERS:
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
            # A native precheck or private-view failure may happen before signing.
            guards._local.outcome = None
            frame = {"role": role, "initiator": "ai" if role in _MARKET_CALLERS else "player", "refusal": None,
                     # One view for the outer operation, reused by all nested
                     # offers. Rule count does not create more market copies.
                     "market": market or MarketPreparation(self.state),
                     # Consumer-owned validation bookkeeping. Nested offers share
                     # it, and removing the outer frame prevents later reuse.
                     "validation": previous["validation"] if previous else {},
                     # Nested confirmation/offer checks reuse private query
                     # inputs, never an allowance or a week-dependent answer.
                     "query_inputs": previous["query_inputs"] if previous else DriverQueryInputs(),
                     # Driver failure handling consumes this single attempt;
                     # it is not a cache of decisions or a retry-suppression map.
                     "offer_attempt": None,
                     "cancel": role == "decision" and bool(values["accept"]),
                     # Nested offer execution must see the same temporary
                     # personality/prestige values as its enclosing confirmation.
                     "normalizations": previous["normalizations"] if previous else {}}
            if role == "offer":
                frame["initiator"] = values["row"].get("initiated_by", "unknown")
                if frame["initiator"] not in ("player", "ai", "system"):
                    frame["initiator"] = "unknown"
            guards._local.personnel_caller = frame
            frame["market"].bind(bound)
            # Freeze owner changes and shutdown through the entire caller, not
            # just the nested signing guard, so preparation uses one registry.
            with guards._lock:
                guards._active += 1
            try:
                # Weekly progression runs on a native worker. Grant access only
                # inside these mapped automated callers; nested offers retain
                # that scope, while arbitrary worker signings remain refused.
                scoped = (role in _MARKET_CALLERS and threading.current_thread() is not threading.main_thread()
                          and not getattr(guards._local, "operation", False))
                with guards.operation() if scoped else nullcontext():
                    if role == "offer":
                        return self._offer(original, bound, frame, previous)
                    try:
                        result = original(*bound.args, **bound.kwargs)
                    except ValidationCancellation as refused:
                        frame["refusal"] = refused.outcome
                        if refused.outcome.status == "unavailable" and previous is not None:
                            # AI can ignore an inner uncontested-signing result.
                            # Keep unwinding until its owning caller can stop too.
                            previous["refusal"] = refused.outcome
                            raise
                        if role in _MARKET_CALLERS:
                            return None
                        return False, str(refused)
                    return (False, frame["refusal"].message) if frame["refusal"] else result
            finally:
                # Native prechecks may return or raise without ever signing.
                # Preserve their usual initialization unless a guard refused it.
                try:
                    if not frame["refusal"]:
                        publish_preparation(frame)
                    if previous is None:
                        # Only the outer owner publishes/discards the shared
                        # market; nested offer return is not market completion.
                        frame["market"].finish(frame)
                finally:
                    # A nested offer can fail without entering signing. Forget
                    # its inputs before an outer caller tries another candidate.
                    discard_query_inputs(frame)
                    if previous is not None:
                        # The outer AI must associate failure with the offer that
                        # just returned, including a native precheck/no refusal.
                        previous["offer_attempt"] = frame["offer_attempt"]
                    guards._local.personnel_caller = previous
                    with guards._lock:
                        guards._active -= 1
        return wrapped

    def _submit(self, original, bound, previous):
        """Refuse before native offer/deadline writes, using its normal UI result."""
        guards = self.guards
        with ExitStack() as lifetime:
            # Recheck ownership and enter admission under the same lock used by
            # cleanup. The entire lookup/preparation/native write stays pinned.
            with guards._lock:
                owned = not guards.closed and bool(guards.owners)
                if owned and not getattr(guards._local, "operation", False):
                    lifetime.enter_context(guards.operation())
            if not owned:
                return original(*bound.args, **bound.kwargs)
            return self._submit_checked(original, bound, previous)

    def _submit_checked(self, original, bound, previous):
        self.guards._local.outcome = None
        values = bound.arguments
        row = self.guards.offers.row(values)
        kind = self.guards.offers.kind(values)
        if (kind not in self.guards.planners or not row
                or str(row.get("status")) not in ("talks_open", "final_offer")):
            return original(*bound.args, **bound.kwargs)
        try:
            arguments = self.guards.offers.arguments(values, row)
        except (TypeError, ValueError, OverflowError):
            # Bad offer values are an ordinary submission error, not evidence
            # that a healthy mod failed. Full planning still runs under budget.
            return False, "Invalid contract offer terms.", row
        outcome, result = self.guards.validate_submission(
            lambda: self.guards._build_contract(arguments),
            lambda: original(*bound.args, **bound.kwargs),
            initiator=previous["initiator"] if previous else "player")
        if outcome.status == "allow":
            return result
        if outcome.status == "unavailable" and previous:
            previous["refusal"] = outcome
            raise ValidationCancellation(outcome)
        # Deliberately do not call the signing refusal presenter: submit_offer's
        # false/message/row result already drives the native inline error path.
        return False, outcome.message, row

    def _active_wrapper(self, original):
        """Keep native argument rules without constructing BoundArguments per read."""
        guards, state, queries = self.guards, self.state, self.queries

        def active(self, driver, week=None):
            if guards.closed or not guards.owners:
                return original(self, driver, week)
            frame = getattr(guards._local, "personnel_caller", None)
            if frame is None or getattr(guards._local, "committing_personnel", False):
                return original(self, driver, week)
            career = getattr(self, "state", None)
            market = frame["market"]
            if career is not state and (market is None or career is not market.state):
                return original(self, driver, week)
            return queries.active(original, self, driver, week, frame)

        return active

    def _ai_failure(self, original, bound, frame):
        if frame is None or frame["role"] != "ai_drivers":
            return original(*bound.args, **bound.kwargs)
        attempt = frame["offer_attempt"]
        # Consume even a nonmatching attempt: a later native precheck must not
        # inherit policy denial from an earlier row or use it a second time.
        frame["offer_attempt"] = None
        row = bound.arguments["row"]
        if (attempt is None or attempt[0] is not row or attempt[1] is None
                or attempt[1].status != "deny"):
            return original(*bound.args, **bound.kwargs)
        # Native code turns 'will not renew' and 'reserve role' into eligibility
        # blocks. Give its classifier trusted text for policy denial, while it
        # still calculates the ordinary cooldown. Genuine native failures bypass.
        bound.arguments.update(reason="Regulation policy denied", permanent=False)
        try:
            return original(*bound.args, **bound.kwargs)
        finally:
            # Keep the complete mod reason for UI/records. The native debug entry
            # uses the generic marker supplied to its classifier above.
            row["failure_reason"] = attempt[1].message

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
            # Native offer scoring can apply the rookie cap before signing.
            # An explicit start must select the same rule as the final contract.
            start = (row.get("context") or {}).get("contract_start_week", row.get("target_contract_start_week"))
            with self.guards.native_check_scope(start), frame["market"].mail_for(row):
                result = original(*bound.args, **bound.kwargs)
        finally:
            # Match the caller's original record, never the private offer draft.
            frame["offer_attempt"] = row, frame["refusal"]
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
        refusal = frame["refusal"]
        if refusal and previous:
            unavailable = refusal.status == "unavailable"
            if unavailable or previous["cancel"] or previous["role"] == "uncontested":
                previous["refusal"] = refusal
            if unavailable or previous["cancel"]:
                # Player acceptance would try backups and close after False.
                # Unavailability must also stop automated failure/closing logic;
                # an automated policy denial still follows native alternatives.
                raise ValidationCancellation(refusal)
        return result
