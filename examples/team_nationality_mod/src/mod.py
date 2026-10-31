# SPDX-License-Identifier: MPL-2.0
"""Compare signing nationalities using the employer's loaded career data."""

import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        pass  # Validators are bound from the pack's regulations.json.

    def on_event(self, name, payload):
        return "null"


def validate_contract(context, regulation):
    """Check approaches and signings against the selected option."""
    proposal = context["proposal"]
    option = regulation[regulation["selected"]]
    employer = proposal["employer"]
    # Suppliers stay outside this team rule. Missing team nationality cannot
    # satisfy an enabled restriction, so refuse rather than silently allow it.
    if option["parameters"]["require_match"] and employer["kind"] in ("team", "academy"):
        nationality = employer["nationality"]
        if not nationality:
            return {"decision": "deny", "message": "Team nationality is unavailable."}
        if proposal["person"]["nationality"] != nationality:
            return {"decision": "deny", "message": f"Nationality must match {employer['name']} ({nationality})."}
    return {"decision": "allow"}
