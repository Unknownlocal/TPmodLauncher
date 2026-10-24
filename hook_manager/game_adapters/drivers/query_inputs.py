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
        self.empty_driver = SimpleNamespace(contract=None)

    def clear(self):
        """Expire all inputs around native writes, failures and caller completion."""
        self.managers.clear()
        self.drivers.clear()
        self.empty_driver = SimpleNamespace(contract=None)

    def active(self, original, manager, driver, week):
        manager_key = id(manager)
        if manager_key not in self.managers:
            # This helper only needs the clock and normaliser. Do not borrow a
            # live query cache: a pre-signing read must not fill or expose it.
            private = SimpleNamespace(state=manager.state, _ai_signing_query_cache=None,
                                      normalize_contract_status=manager.normalize_contract_status)
            # Retain originals too, so an object ID cannot be reused here.
            self.managers[manager_key] = manager, private
        private_manager = self.managers[manager_key][1]
        source = driver.contract
        if source is None:
            # No agreement can escape this native lookup, and its private query
            # cache is disabled. One empty input can serve all such drivers;
            # native code still checks the requested week on every call.
            private_driver = self.empty_driver
            private_driver.contract = None
        else:
            key = manager_key, id(driver)
            entry = self.drivers.get(key)
            # A source copy detects nested edits as well as replacement. The
            # normalised working copy cannot serve as the untouched baseline.
            if entry is None or entry[1] is not source or entry[2] != source:
                entry = None
                private_driver = SimpleNamespace(contract=deepcopy(source))
            else:
                private_driver = entry[3]
        try:
            result = original(private_manager, private_driver, week)
        except BaseException:
            # Do not reuse a partly normalised record after a native failure.
            self.clear()
            raise
        if source is None:
            if result is not None:
                # Preserve fresh ownership even if native code produces a
                # record from an initially empty input in a later game build.
                self.empty_driver = SimpleNamespace(contract=None)
        elif result is None:
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
