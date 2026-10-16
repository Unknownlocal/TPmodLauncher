# SPDX-License-Identifier: MPL-2.0
"""Declare the editor and publish saved colours as a restart-only UI palette."""

import json
import wit_world

from palette import COLOR_GROUPS, PALETTE_SCHEMA


class WitWorld(wit_world.WitWorld):
    def activate(self):
        values = wit_world.register_settings(json.dumps({
            "schema": PALETTE_SCHEMA,
            "label": "Theme",
            "fields": [
                {"id": f"colour_{number}", "label": " / ".join(role.replace("_", " ").title() for role in roles),
                 "color": "#" + "".join(f"{channel:02X}" for channel in default)}
                for number, default, roles in COLOR_GROUPS
            ],
        }, separators=(",", ":")))
        self.publish(values)

    def publish(self, payload):
        values = json.loads(payload)["values"]
        wit_world.register_theme(json.dumps({
            "label": "Custom Theme",
            "colours": {role: values[f"colour_{number}"]
                        for number, _, roles in COLOR_GROUPS for role in roles},
        }, separators=(",", ":")))

    def on_event(self, name, payload):
        if name == "ui-settings.changed":
            self.publish(payload)
        return "null"
