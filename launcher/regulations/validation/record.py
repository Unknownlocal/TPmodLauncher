# SPDX-License-Identifier: MPL-2.0
"""Cache one rule's JSON text and size measurements in the launcher.

OwnerView objects can refer to the same record, so mods subscribing to the same
native option do not make the launcher encode and measure it separately for each
mod. Each sandbox still receives its own installed data.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class CatalogRecord:
    identity: int
    kind: str
    text: str
    metric: tuple
