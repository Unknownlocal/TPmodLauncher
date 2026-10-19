# SPDX-License-Identifier: MPL-2.0
"""One canonical payload and its once-computed JSON/measurement components."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class CatalogRecord:
    # Field bindings are frozen; nested values stay trusted read-only canonical
    # data. Only independent copies cross into author execution in the guest.
    identity: int
    kind: str
    value: dict
    text: str
    metric: tuple
