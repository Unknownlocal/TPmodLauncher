# SPDX-License-Identifier: MPL-2.0
"""Carry a validation outcome back to the wrapper responsible for the action."""


class ValidationCancellation(Exception):
    """Let a caller stop safely while retaining the refusal's status and message."""

    def __init__(self, outcome):
        super().__init__(outcome.message)
        self.outcome = outcome
