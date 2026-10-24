# SPDX-License-Identifier: MPL-2.0
"""Reuse private active-contract inputs within one negotiation, not its answers."""

from copy import deepcopy
from types import SimpleNamespace


class DriverQueryInputs:
    """Keep unexposed working records until signing or the caller finishes.

    Native active-contract reads normalise their input and return either that
    agreement or None for the requested week. An agreement returned to a caller
    must leave this store: later caller edits must not affect another query.
    The native check still runs for every week, including repeated queries.
    """

    def __init__(self):
        self.managers = {}
        self.drivers = {}

    def clear(self):
        """Expire all inputs around native writes, failures and caller completion."""
        self.managers.clear()
        self.drivers.clear()

    def active(self, original, bound):
        values = bound.arguments
        manager, driver = values["self"], values["driver"]
        manager_key = id(manager)
        if manager_key not in self.managers:
            # This helper only needs the clock and normaliser. Do not borrow a
            # live query cache: a pre-signing read must not fill or expose it.
            private = SimpleNamespace(state=manager.state, _ai_signing_query_cache=None,
                                      normalize_contract_status=manager.normalize_contract_status)
            # Retain originals too, so an object ID cannot be reused here.
            self.managers[manager_key] = manager, private
        values["self"] = self.managers[manager_key][1]
        key = manager_key, id(driver)
        source = driver.contract
        entry = self.drivers.get(key)
        # A source copy detects in-place edits as well as contract replacement.
        # Most roster members have None here, making this comparison trivial.
        if entry is None or entry[1] is not source or entry[2] != source:
            entry = None
            private_driver = SimpleNamespace(contract=deepcopy(source))
        else:
            private_driver = entry[3]
        values["driver"] = private_driver
        try:
            result = original(*bound.args, **bound.kwargs)
        except BaseException:
            # Do not reuse a partly normalised record after a native failure.
            self.clear()
            raise
        if result is None:
            if entry is None:
                # Live owner, source identity, untouched source copy, working
                # driver: keep change detection separate from normalisation.
                self.drivers[key] = driver, source, deepcopy(source), private_driver
        else:
            # The returned agreement remains a fresh, caller-owned copy just as
            # before reuse. Keep no private reference that could expose its edits
            # to a subsequent lookup. None results expose no mutable record.
            self.drivers.pop(key, None)
        return result


def discard_query_inputs(frame):
    """Also accept signing frames created without enclosing negotiation callers."""
    inputs = frame.get("query_inputs")
    if inputs is not None:
        inputs.clear()
