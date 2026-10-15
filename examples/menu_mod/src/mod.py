# SPDX-License-Identifier: MPL-2.0
"""A sandboxed UI mod: the host supplies native rendering and click confirmation."""

import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.add_menu_button(
            "confirmation", "Mod Launcher", "The sandboxed UI mod is active"
        )
        wit_world.report("Menu example activated")

    def on_event(self, name, payload):
        return "null"
