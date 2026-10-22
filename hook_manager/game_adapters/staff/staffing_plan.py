# SPDX-License-Identifier: MPL-2.0
"""Private staffing preparation results; none of this grants signing admission."""

from dataclasses import dataclass

from .staff_signing import StaffSigning


@dataclass(frozen=True)
class StaffingPlan:
    """Describe a native onboarding/recovery run without publishing its effects.

    ``native_result`` preserves the native list/None result. ``signings`` records
    every attempted contract, including native refusals. ``generated`` also keeps
    unselected population seeds, still unsigned. ``history_entries`` contains only
    newly created history keys. These are trusted native objects, never mod data.
    Validation, stale-state checks and publication belong to the later coordinator.
    """

    native_result: object
    signings: tuple[StaffSigning, ...]
    generated: tuple
    history_entries: dict
