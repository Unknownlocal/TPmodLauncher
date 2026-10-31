# SPDX-License-Identifier: MPL-2.0
"""Private native-side regulation transport; no mod registration API."""

# Export transport only. Native guard adapters are imported explicitly by
# trusted career initialization, not by a launcher that only needs protocol I/O.
from .channel import RegulationChannel

__all__ = ["RegulationChannel"]
