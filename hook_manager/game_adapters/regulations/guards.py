# SPDX-License-Identifier: MPL-2.0
"""Run all subscribing mods against one proposal before calling native code.

The callable manager still owns wrappers, worker admission and restoration. This
adapter supplies one context and one channel action shared by all participants.
Neither the projections nor the rule-selection callback come from mod code.
"""

import threading
import time

from ...callables import CallableAdapters
from ...manager import HookError, data_copy
from .framing import regulation_decisions
from .outcome import ValidationOutcome


class RegulationGuards(CallableAdapters):
    def __init__(self, channel, select, scope, *, report=None):
        super().__init__()
        self.channel, self.select = channel, select
        self.scope, self._error_report = scope, report
        # Native policy supplies a cheap freshness check. Fixed trusted selectors
        # used without a live policy have no changing selection inputs.
        self.policy_identity = getattr(getattr(select, "__self__", None), "identity", lambda: None)
        self.subscriptions = channel.bindings(scope)

    def _register(self, owner, bindings):
        # All owners must agree on the proposal. Otherwise the first binding's
        # projection would silently determine which terms its peers validate.
        for binding in bindings:
            if any(
                    item.target == binding.target and item.project is not binding.project
                    for item in self.entries.values()):
                raise HookError("Shared regulation guards require one projection per target")
        return super()._register(owner, bindings)

    @property
    def last_outcome(self):
        """Most recent owned validation on this thread, not native execution success."""
        return getattr(self._local, "outcome", None)

    def _deny(self, refuse, outcome):
        self._local.outcome = outcome
        return refuse(outcome.message)

    def _validate(self, original, signature, entries, args, kwargs):
        # Direct native calls do not always have a caller frame to clear this.
        self._local.outcome = None
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        refuse, projection = entries[0][1].refuse, entries[0][1].project
        participants = {key[0] for key, _ in entries}
        outcome = self._decision(lambda: projection(dict(bound.arguments), None), participants,
                                 identity=lambda: self._proposal_identity(bound.arguments))
        self._local.outcome = outcome
        if outcome.status != "allow":
            return self._deny(refuse, outcome)
        return self._execute(original, args, kwargs)

    def _proposal_identity(self, arguments):
        """Capture inputs that must stay unchanged during this validation."""
        return self.policy_identity()

    def _decision(self, project, participants, *, identity=None):
        """Check one proposal without executing native code or choosing its return shape."""
        deadline = time.monotonic() + self.seconds
        identity = identity or self.policy_identity
        acquired = False
        try:
            if not getattr(self.channel, "validation_ready", True):
                # Installation is incomplete, not a broken validator. Do not
                # project inputs, dispatch or permanently disable its owners.
                return ValidationOutcome("unavailable", "Validation unavailable")
            if (getattr(self._local, "dispatching", False)
                    or threading.current_thread() is not threading.main_thread()
                    and not getattr(self._local, "operation", False)):
                raise HookError("Unscoped worker or reentrant regulation validation")
            if any(self.owners[owner] != "ready" for owner in participants):
                return ValidationOutcome("unavailable", "Validation unavailable")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._guest.acquire(timeout=remaining):
                raise HookError("Regulation dispatch deadline expired")
            acquired = self._local.dispatching = True
            policy = identity()
            context = data_copy(project())
            # Only small choice references vary by rule. The complete proposal
            # is sent once, and every applicable handler points to context zero.
            rules = sorted({rule for owner in participants for _, rule in self.subscriptions[owner]})
            selections = [dict(data_copy(self.select(rule, context["effective"]["season"])),
                               rule=rule, evaluation_season=context["effective"]["season"]) for rule in rules]
            indexes = {rule: index for index, rule in enumerate(rules)}
            calls = {owner: [[slot, 0, indexes[rule]] for slot, rule in self.subscriptions[owner]]
                     for owner in sorted(participants)}
            if time.monotonic() >= deadline:
                raise HookError("Regulation projection deadline expired")
            # Trusted integrations can reuse an operation's policy refusal here;
            # admission/readiness and deadline checks still surround that path.
            refusal = self._refusal(context, selections, calls, deadline)
            # Cleanup may close the shared gate while this reply is in flight.
            # Decline the write without treating that pause as a mod failure.
            if not getattr(self.channel, "validation_ready", True):
                return ValidationOutcome("unavailable", "Validation unavailable")
            if policy != identity():
                # Native choices changed while preparing inputs or waiting for
                # a reply. Decline this attempt without disabling healthy mods.
                return ValidationOutcome("unavailable", "Regulation choices changed during validation")
            if time.monotonic() >= deadline or any(self.owners[owner] != "ready" for owner in participants):
                raise HookError("Regulation allowance expired or became unavailable")
            if refusal:
                return ValidationOutcome("deny", refusal)
        except Exception as error:
            # Identified handler failures belong to that owner. A projection or
            # broken stream prevents checking this action's whole cohort.
            failed = participants & self.channel._unavailable
            for owner in failed or participants:
                self.owners[owner] = "unavailable"
            self._report(error)
            # Only actual validation errors use this failure notice. Other
            # callers also retain ordinary policy refusals through _report.
            if self._error_report is not None:
                self._error_report("REGULATIONS VALIDATION FAILED scope=" + self.scope + ": "
                                   + " ".join(str(error).split())[:512])
            return ValidationOutcome("unavailable", "Validation unavailable")
        finally:
            if acquired:
                self._local.dispatching = False
                self._guest.release()
        # Execution belongs to the caller, outside validation error handling.
        # Native False or exceptions after this allowance remain native failures.
        return ValidationOutcome("allow")

    def _refusal(self, context, selections, calls, deadline):
        """Return a fully checked policy refusal, or None when every handler allows."""
        decisions = self.channel([context], selections, calls, deadline)
        if type(decisions) is not dict or decisions.keys() != calls.keys():
            raise HookError("Incomplete regulation decisions")
        refusal = None
        # Validate every reply before accepting the first ordered refusal;
        # a malformed later decision is an unavailable validator, not a deny.
        for owner, rows in calls.items():
            regulation_decisions(decisions[owner], len(rows))
            for decision in decisions[owner]:
                if decision["decision"] == "deny":
                    refusal = refusal or decision["message"]
        return refusal

    def _execute(self, original, args, kwargs):
        return original(*args, **kwargs)
