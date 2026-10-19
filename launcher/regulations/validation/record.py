# SPDX-License-Identifier: MPL-2.0
"""Cache one rule value with its JSON text and size measurements in the launcher.

OwnerView objects can refer to the same record, so mods subscribing to the same
native option do not make the launcher encode and measure it separately for each
mod. Each sandbox still receives its own installed data.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class CatalogRecord:
    # frozen=True prevents field reassignment; it does not freeze nested dicts.
    # Launcher code treats value as read-only. Mod handlers receive separate
    # argument copies inside their sandbox.
    identity: int
    kind: str
    value: dict
    text: str
    metric: tuple
