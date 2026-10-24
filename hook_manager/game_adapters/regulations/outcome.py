# SPDX-License-Identifier: MPL-2.0
"""Keep trusted validation status separate from mod-authored refusal text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationOutcome:
    """Record one host validation as allow, deny or unavailable.

    ``allow`` permits native execution; native code can still return False or
    raise. ``deny`` is a policy decision with a mod-authored message. ``unavailable``
    means required validation could not complete. Callers branch on ``status``,
    never message text. This object stays private and does not change wire replies.
    """

    status: str
    message: str | None = None
