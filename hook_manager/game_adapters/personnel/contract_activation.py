# SPDX-License-Identifier: MPL-2.0
"""One existing agreement selected by native future-contract activation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractActivation:
    """Associate a real game person with copied input for regulation checks.

    The reference identifies the person whose agreement is being checked. The
    prepared dictionary is separately owned data, not permission to activate it.
    """

    kind: str
    person: object
    prepared: dict
