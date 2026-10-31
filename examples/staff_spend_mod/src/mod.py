# SPDX-License-Identifier: MPL-2.0
"""Apply the selected cap to projected annual staff and driver base salaries."""

import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        pass  # Validators are declared in the pack's regulations.json.

    def on_event(self, name, payload):
        return "null"


def validate_contract(context, regulation):
    """Apply the selected rule to team employment; academy fees are separate."""
    proposal = context["proposal"]
    parameters = regulation[regulation["selected"]]["parameters"]
    mode = parameters["count"]
    if mode == "none" or proposal["employer"]["kind"] != "team" or proposal["contract_kind"] != "employment":
        return {"decision": "allow"}
    if mode == "staff" and proposal["person"]["kind"] == "driver":
        return {"decision": "allow"}
    # The shared totals already include the proposed replacement agreement.
    payroll = proposal["employer"]["payroll"]
    total = payroll["staff_annual_salary_m"]
    if mode == "all":
        total += payroll["driver_annual_salary_m"]
    cap = parameters["cap_m"]
    if total > cap:
        return {"decision": "deny", "message": f"Annual payroll would be ${total:g}M; the limit is ${cap:g}M."}
    return {"decision": "allow"}
