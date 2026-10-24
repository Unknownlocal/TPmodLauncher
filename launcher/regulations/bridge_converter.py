# SPDX-License-Identifier: MPL-2.0
"""Admit only the two named native check switches, without installing hooks."""

from . import schema


def convert_bridges(value):
    """Convert closed declarations; JSON cannot select native targets or models."""
    schema.fields(value, (), ("rookie_contract_length_check", "staff_prestige_check"))
    for name, enabled in value.items():
        if type(enabled) is not bool:
            raise ValueError(name + " must be a boolean")
    return value
