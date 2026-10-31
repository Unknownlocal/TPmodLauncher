# SPDX-License-Identifier: MPL-2.0
"""Admit only the two named native check switches, without installing hooks."""

from . import schema


def convert_bridges(value, categories=None):
    """Convert closed declarations; JSON cannot select native targets or models."""
    schema.fields(value, (), ("rookie_contract_length_check", "staff_prestige_check"))
    for name, enabled in value.items():
        if type(enabled) is bool:
            continue
        if type(enabled) is not dict or set(enabled) != {"rule", "parameter"}:
            raise ValueError(name + " must be a boolean or rule/parameter reference")
        rule, parameter = schema.name(enabled["rule"], 256), schema.name(enabled["parameter"], 128)
        if categories is not None:
            if rule not in categories:
                raise ValueError("Native check reference needs an owned category")
            if any(type(option.get("parameters", {}).get(parameter)) is not bool for option in categories[rule]["options"]):
                raise ValueError("Every native check option needs a boolean parameter")
    return value
