# SPDX-License-Identifier: MPL-2.0
"""Remember unchanged personnel refusals for one native operation only."""


class RefusedProposals:
    """Keep each person/employer's last rejected proposal within one operation."""

    def __init__(self):
        self._revision = None
        self._denials = {}

    @staticmethod
    def _value(value):
        """Snapshot already checked plain data for comparison, without JSON encoding."""
        # Validators can distinguish True, 1 and 1.0. Keep their types too;
        # dictionary insertion order does not affect which proposal was checked.
        if type(value) is dict:
            return dict, tuple(sorted((key, RefusedProposals._value(item)) for key, item in value.items()))
        if type(value) is list:
            return list, tuple(RefusedProposals._value(item) for item in value)
        return type(value), value

    def check(self, channel, context, selections, calls, deadline, evaluate):
        """Reuse an unchanged rejection, or ask all required validators again."""
        lock = getattr(channel, "_lock", None)
        request = None
        if lock is not None and lock.acquire(blocking=False):
            try:
                # Publication/reset use this lock too. Read availability, policy
                # identity and the remembered result as one consistent snapshot.
                generations = getattr(channel, "_generations", None)
                if (generations is not None and not getattr(channel, "closed", True)
                        and not calls.keys() & channel._unavailable and calls.keys() <= generations.keys()):
                    revision = channel.epoch, tuple((owner, generations[owner], channel._owners[owner]["revision"])
                                                    for owner in calls)
                    if revision != self._revision:
                        self._revision = revision
                        self._denials.clear()
                    proposal = context["proposal"]
                    identity = proposal["person"]["ref"], proposal["employer"]["ref"]
                    # Normalized terms, existing contracts and selected rules
                    # all affect a refusal. Compare the complete validator input.
                    request = self._value(context), self._value(selections), self._value(calls)
                    previous = self._denials.get(identity)
                    if previous is not None and previous[0] == request:
                        return previous[1]
                    # Changed terms replace the old refusal. Reverting to those
                    # old terms later still requires a new check.
                    self._denials.pop(identity, None)
                else:
                    # Fresh dispatch retains the normal failure handling for
                    # closed, unavailable or unpublished policy state.
                    self._denials.clear()
            finally:
                lock.release()
        else:
            self._denials.clear()
        # Channel dispatch acquires this same lock; release it before requesting
        # a new decision. The enclosing guard still checks its action deadline
        # and owner readiness after either a remembered or a fresh result.
        refusal = evaluate(context, selections, calls, deadline)
        if request is not None and refusal:
            # evaluate checks the complete response before returning a policy
            # denial. Allowances and unavailable validation are never remembered.
            self._denials[identity] = request, refusal
        return refusal
