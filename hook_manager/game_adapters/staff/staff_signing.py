# SPDX-License-Identifier: MPL-2.0
"""One native signing attempt captured while planning required team staff."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StaffSigning:
    """Keep canonical game arguments beside owned normalized proposal data.

    ``native_accepted`` describes the private native run, not a mod allowance.
    It is None when validation withheld execution, distinct from native False.
    Arguments retain the live manager/person, or the uncommitted generated person.
    These arguments describe the attempt; publishing its effects requires the
    coordinator's complete-operation checks rather than replaying this signing.
    """

    arguments: dict
    prepared: dict
    native_accepted: bool | None
