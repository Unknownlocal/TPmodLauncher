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
    ``missing`` records native queried staffing slots still empty after planning.
    ``publication`` holds the native writes and a stale-input check; it grants no
    regulation permission. The consuming coordinator decides whether to publish.
    """

    native_result: object
    signings: tuple[StaffSigning, ...]
    generated: tuple
    history_entries: dict
    missing: tuple = ()
    publication: object = None
