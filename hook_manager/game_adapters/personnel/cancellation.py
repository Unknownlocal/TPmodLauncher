# SPDX-License-Identifier: MPL-2.0
"""Unwind checked personnel callers before they treat validation failure as loss."""


class PersonnelCancellation(Exception):
    """Carry a trusted refusal to its owning player or market caller wrapper."""

    def __init__(self, outcome):
        super().__init__(outcome.message)
        self.outcome = outcome
