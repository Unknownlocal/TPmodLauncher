# SPDX-License-Identifier: MPL-2.0
"""One native signing attempt captured while planning required team staff."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StaffSigning:
    """Keep canonical game arguments beside owned normalized proposal data.

    ``native_accepted`` describes the private native run, not a mod allowance.
    Arguments retain the live manager/person, or the uncommitted generated person.
    A later transaction coordinator must validate before using those arguments.
    """

    arguments: dict
    prepared: dict
    native_accepted: bool
