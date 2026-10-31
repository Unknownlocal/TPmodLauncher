# SPDX-License-Identifier: MPL-2.0
"""Apply the existing mod lifecycle and JSON event contract to a shared member."""

from launcher.mod_contract import Mod


class SharedSourceMod(Mod):
    """Address one entry by mod ID while its runner owns all members' memory.

    PackMods creates these only after the whole group's sources have loaded.
    Mod supplies activation and bounded JSON events. Closing any member closes
    the group: peers may hold Python objects that cannot be unloaded separately.
    """

    def __init__(self, runner, snapshot):
        self.runner = runner
        self.manifest = snapshot.manifest
        self.sandbox = runner.sandbox
        # Regulation admission still checks handlers against the accepted source.
        # Other members need no retained host copy after the guest has loaded it.
        self.snapshot = snapshot if "regulations" in self.manifest.capabilities else None
        self._active = False

    def _activate(self):
        self.runner.activate(self.manifest.id)

    def _on_event(self, name, payload):
        return self.runner.on_event(self.manifest.id, name, payload)

    def close(self):
        """Release the physical runner and every member's registrations together."""
        self.runner.close()
