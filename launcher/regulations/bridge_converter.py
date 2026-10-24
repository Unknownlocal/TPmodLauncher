# SPDX-License-Identifier: MPL-2.0
"""Admit only the two named native rule replacements, without binding either."""

from . import schema
from .rookie_contract_limit import RookieContractLimit
from .rule_parameter import RuleParameter
from .staff_prestige_formula import StaffPrestigeFormula


def _rookie(value, accessible):
    if type(value) is dict and "value" in value:
        schema.fields(value, ("value",))
        maximum = value["value"]
        # bool is an int subclass; accepting it would turn true into a year cap.
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ValueError("Rookie maximum must be a positive integer or null")
        return RookieContractLimit(maximum)
    schema.fields(value, ("rule", "parameter"), ("source",))
    rule = schema.rule(value["rule"], accessible)
    schema.label(value["parameter"])
    source = value.get("source", "parameters")
    if source not in ("parameters", "option_data"):
        raise ValueError("Rookie parameter source must be parameters or option_data")
    # An optional key may be absent on any option. Its selected value and type
    # are resolved later, without confusing absence with an explicit JSON null.
    return RookieContractLimit(RuleParameter(rule, value["parameter"], source))


def _prestige(value, source, accessible, index):
    schema.fields(value, ("handler",), ("data",))
    schema.handler(value["handler"], index.source_paths(source))
    rules = tuple(schema.rule(rule, accessible) for rule in schema.rows(value.get("data", []), 32))
    if len(set(rules)) != len(rules):
        raise ValueError("Duplicate staff prestige data reference")
    return StaffPrestigeFormula(value["handler"], rules)


def convert_bridges(value, source, accessible, index):
    """Convert closed declarations; JSON cannot select native targets or models."""
    schema.fields(value, (), ("rookie_contract_max_years", "staff_required_prestige"))
    result = {}
    if "rookie_contract_max_years" in value:
        result["rookie_contract_max_years"] = _rookie(value["rookie_contract_max_years"], accessible)
    if "staff_required_prestige" in value:
        result["staff_required_prestige"] = _prestige(value["staff_required_prestige"], source, accessible, index)
    return result
