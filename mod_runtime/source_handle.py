# SPDX-License-Identifier: MPL-2.0
"""Address one logical source mod without owning another Python interpreter."""


class SourceHandle:
    """A member's identity and availability; its group owns calls and closure."""

    def __init__(self, group, manifest):
        self._group = group
        self.manifest = manifest

    @property
    def closed(self):
        """Every member observes the same physical runner's availability."""
        return self._group.closed
