# SPDX-License-Identifier: MPL-2.0
"""Unwind a checked player confirmation before its backup/closing writes."""


class PersonnelCancellation(Exception):
    """Private control flow, caught by the owning player-decision wrapper."""
