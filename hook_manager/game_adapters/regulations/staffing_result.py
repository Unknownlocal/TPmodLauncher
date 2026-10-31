# SPDX-License-Identifier: MPL-2.0
"""A private coordinator result, independent of native list/None return shapes."""

from dataclasses import dataclass

from .outcome import ValidationOutcome


@dataclass(frozen=True)
class StaffingResult:
    """Tell an enclosing caller whether the complete staffing operation published.

    Native recovery returns None even when a required role stays empty. Callers
    must check ``published`` rather than infer success from ``native_result``.
    ``missing`` holds (team, role, absolute week) slots. ``refusal`` carries policy
    denial or unavailable validation; it stays None for ordinary native failure.
    """

    published: bool
    native_result: object = None
    missing: tuple = ()
    refusal: ValidationOutcome | None = None
