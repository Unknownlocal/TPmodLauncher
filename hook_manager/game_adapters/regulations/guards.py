# SPDX-License-Identifier: MPL-2.0
"""Run all subscribing mods against one proposal before calling native code.

The callable manager still owns wrappers, worker admission and restoration. This
path replaces its per-owner projections with one context and one channel action.
Neither the projections nor the rule-selection callback come from mod code.
"""

import threading
import time

from ...callables import CallableAdapters
from ...manager import HookError, data_copy
from .framing import regulation_decisions


class RegulationGuards(CallableAdapters):
    def __init__(self, channel, select, scope):
        super().__init__(channel)
        self.channel, self.select = channel, select
        self.subscriptions = channel.bindings(scope)

    def _register(self, owner, bindings):
        # All owners must agree on the proposal. Otherwise the first binding's
        # projection would silently determine which terms its peers validate.
        for binding in bindings:
            if binding.phase != "guard" or any(
                    item.target == binding.target and item.project is not binding.project
                    for item in self.entries.values()):
                raise HookError("Shared regulation guards require one projection per target")
        return super()._register(owner, bindings)

    @property
    def last_refusal(self):
        """The executing caller can read its reason without sharing thread state."""
        return getattr(self._local, "refusal", None)

    def _deny(self, codec, message):
        self._local.refusal = message
        return codec.denied(message)

    def _validate(self, original, signature, entries, args, kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        deadline = time.monotonic() + self.seconds
        codec, projection = entries[0][1].result, entries[0][1].project
        participants = {key[0] for key, _ in entries}
        self._local.refusal = None
        acquired = False
        try:
            if (getattr(self._local, "dispatching", False)
                    or threading.current_thread() is not threading.main_thread()
                    and not getattr(self._local, "operation", False)):
                raise HookError("Unenrolled thread or reentrant regulation validation")
            if any(self.owners[owner] != "ready" for owner in participants):
                return self._deny(codec, "Validation unavailable")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._guest.acquire(timeout=remaining):
                raise HookError("Regulation dispatch deadline expired")
            acquired = self._local.dispatching = True
            context = data_copy(projection(dict(bound.arguments), None))
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
            if time.monotonic() >= deadline or any(self.owners[owner] != "ready" for owner in participants):
                raise HookError("Regulation allowance expired or became unavailable")
            if refusal:
                return self._deny(codec, refusal)
        except Exception as error:
            # Identified handler failures belong to that owner. A projection or
            # broken stream prevents checking this action's whole cohort.
            failed = participants & self.channel._unavailable
            for owner in failed or participants:
                self.owners[owner] = "unavailable"
            self._report(error)
            return self._deny(codec, "Validation unavailable")
        finally:
            if acquired:
                self._local.dispatching = False
                self._guest.release()
        # Native exceptions propagate once, outside validation error handling.
        return self._execute(original, args, kwargs)

    def _execute(self, original, args, kwargs):
        return original(*args, **kwargs)
