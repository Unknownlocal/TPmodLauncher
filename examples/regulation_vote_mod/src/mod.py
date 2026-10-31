# SPDX-License-Identifier: MPL-2.0
"""Teams outside the top three favour DRS; the top three oppose it."""

import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        pass

    def on_event(self, name, payload):
        return "null"


def vote(context, regulation):
    wants_drs = context["snapshot"]["teams"][context["team"]]["rank"] > 3
    return {"choice": "proposed" if regulation["proposed"]["drs_enabled"] == wants_drs else "current",
            "reason": "Teams outside the top three favour DRS; the top three oppose it."}
