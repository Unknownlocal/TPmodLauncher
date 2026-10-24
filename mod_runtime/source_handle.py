# SPDX-License-Identifier: MPL-2.0
"""Address one logical source mod without owning another Python interpreter."""


class SourceHandle:
    """A member's identity and lifecycle access; only its group owns closure."""

    def __init__(self, group, manifest):
        self._group = group
        self.manifest = manifest

    @property
    def closed(self):
        """Every member observes the same physical runner's availability."""
        return self._group.closed

    def activate(self, *, deadline=None):
        """Activate this entry without creating or taking ownership of a Store."""
        self._group.activate(self.manifest.id, deadline=deadline)

    def on_event(self, name, payload, *, deadline=None):
        """Exchange raw strings; the launcher adapter owns JSON/event admission."""
        return self._group.on_event(self.manifest.id, name, payload, deadline=deadline)
