# SPDX-License-Identifier: MPL-2.0
"""Admit only the two named native rule replacements, without binding either."""

from . import schema
from .rookie_contract_limit import RookieContractLimit, rookie_maximum
from .rule_parameter import RuleParameter


def _rookie(value, accessible):
    if type(value) is dict and "value" in value:
        schema.fields(value, ("value",))
        # bool is an int subclass; accepting it would turn true into a year cap.
        return RookieContractLimit(rookie_maximum(value["value"]))
    schema.fields(value, ("rule", "parameter"), ("source",))
    rule = schema.rule(value["rule"], accessible)
    schema.label(value["parameter"])
    source = value.get("source", "parameters")
    if source not in ("parameters", "option_data"):
        raise ValueError("Rookie parameter source must be parameters or option_data")
    # An optional key may be absent on any option. Its selected value and type
    # are resolved later, without confusing absence with an explicit JSON null.
    return RookieContractLimit(RuleParameter(rule, value["parameter"], source))


def convert_bridges(value, accessible):
    """Convert closed declarations; JSON cannot select native targets or models."""
    schema.fields(value, (), ("rookie_contract_max_years", "staff_prestige_check"))
    result = {}
    if "rookie_contract_max_years" in value:
        result["rookie_contract_max_years"] = _rookie(value["rookie_contract_max_years"], accessible)
    if "staff_prestige_check" in value:
        enabled = value["staff_prestige_check"]
        if type(enabled) is not bool:
            raise ValueError("Staff prestige check must be a boolean")
        result["staff_prestige_check"] = enabled
    return result
