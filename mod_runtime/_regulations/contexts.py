# SPDX-License-Identifier: MPL-2.0
"""Keep one checked proposal for all mods participating in an action."""

import gc

from .frozen import FrozenValue
from .policy import context_fields
from .values import bounded, integer, measure


class RegulationContexts:
    """Measure the shared proposal once; handlers still receive fresh copies."""

    def __init__(self, action, values):
        if type(values) is not list or len(values) != 1:
            raise ValueError("Expected one shared regulation context")
        value = values[0]
        context_fields(value)
        metric = bounded(measure(value))
        self.action, self.used = action, False
        self.row = (FrozenValue(value), metric,
                    value["scope"], value["effective"]["season"])
        # Untrack flat cached tuples now so their first GC scan is not charged
        # to a later validator's ordinary allowance.
        gc.collect(0)

    def get(self, index):
        if integer(index) != 0:
            raise ValueError("Unknown shared regulation context")
        self.used = True
        return self.row

    def seal(self):
        """Completion must have consumed the proposal, not merely transferred it."""
        if not self.used:
            raise ValueError("Unreferenced shared regulation context")
