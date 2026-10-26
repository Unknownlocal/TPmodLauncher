# SPDX-License-Identifier: MPL-2.0
"""Apply the adopted rookie length rule using the game's applicability check."""

import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        pass  # The launcher binds declared validators after source activation.

    def on_event(self, name, payload):
        return "null"


def validate_contract(context, regulation):
    """Use the selected option, including one scheduled for a future contract."""
    proposal = context["proposal"]
    if proposal.get("native_checks", {}).get("rookie_contract_length_applies", False):
        maximum = regulation[regulation["selected"]]["parameters"]["max_years"]
        if proposal["proposed"]["duration_weeks"] > maximum * 52:
            return {"decision": "deny", "message": f"Rookie contracts may last at most {maximum} years."}
    return {"decision": "allow"}
