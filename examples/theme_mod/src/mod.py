# SPDX-License-Identifier: MPL-2.0
"""Declare the theme editor through shared UI settings; palette application is separate."""

import json
import wit_world

from palette import COLOR_GROUPS, COLOR_LABEL_PREFIX, PALETTE_SCHEMA


class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.register_settings(json.dumps({
            "schema": PALETTE_SCHEMA,
            "label": "Theme",
            "fields": [
                {"id": f"colour_{number}", "label": f"{COLOR_LABEL_PREFIX} {number}",
                 "color": "#" + "".join(f"{channel:02X}" for channel in default)}
                for number, default, _ in COLOR_GROUPS
            ],
        }, separators=(",", ":")))

    def on_event(self, name, payload):
        return "null"
