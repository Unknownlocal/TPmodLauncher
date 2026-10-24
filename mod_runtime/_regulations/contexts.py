# SPDX-License-Identifier: MPL-2.0
"""Retain one proposal table for all owners taking part in a shared action."""

import gc

from .frozen import FrozenValue
from .policy import context_fields
from .values import bounded, integer, measure


class RegulationContexts:
    """Check once, then lend cached values and measurements to owner call pages."""

    def __init__(self, action, values):
        if type(values) is not list or not 1 <= len(values) <= 1024:
            raise ValueError("Invalid shared regulation context count")
        self.action, self.used = action, set()
        rows = []
        for value in values:
            context_fields(value)
            metric = bounded(measure(value))
            rows.append((FrozenValue(value), metric,
                         value["scope"], value["effective"]["season"]))
        # Flat cached values avoid retaining a second mutable JSON tree, and let
        # each handler receive fresh arguments under its preparation allowance.
        self.rows = tuple(rows)
        # Untrack the flat cached tuples during preparation. Their first GC
        # scan must not be charged to a later validator's ordinary allowance.
        gc.collect(0)

    def get(self, index):
        index = integer(index)
        if index >= len(self.rows):
            raise ValueError("Unknown shared regulation context")
        self.used.add(index)
        return self.rows[index]

    def seal(self):
        """Reject unused input rows before an action can be accepted as complete."""
        if len(self.used) != len(self.rows):
            raise ValueError("Unreferenced shared regulation context")
