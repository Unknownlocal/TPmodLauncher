# SPDX-License-Identifier: MPL-2.0
"""Cache one rule's JSON text and size measurements in the launcher.

OwnerView objects share records for common rule data. The launcher encodes and
measures each record once, and the shared runner installs one physical copy.
Each mod's view selects which records accompany its validator calls.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class CatalogRecord:
    identity: int
    kind: str
    text: str
    metric: tuple
