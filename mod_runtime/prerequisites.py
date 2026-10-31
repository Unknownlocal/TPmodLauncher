# SPDX-License-Identifier: MPL-2.0
"""Read optional registration requirements without changing the runner interface."""

import re


def prerequisite_declarations(data, capabilities):
    """Check registration permissions and return immutable named conditions."""
    if type(data) is not dict or len(data) > 6:
        raise ValueError("Expected at most six prerequisite declarations")
    result, unique = [], set()
    for registration, requirements in data.items():
        if registration in ("settings", "theme"):
            capability = "ui-settings"
        elif type(registration) is str and re.fullmatch(r"menu:[a-z][a-z0-9_.:-]{0,127}", registration):
            capability = "main-menu"
        else:
            raise ValueError("Prerequisites must name settings, theme or menu:<button-id>")
        if capability not in capabilities:
            raise ValueError("Prerequisite registration needs its adapter permission")
        if (type(requirements) is not list or not 1 <= len(requirements) <= 8
                or any(type(value) is not str for value in requirements)
                or len(set(requirements)) != len(requirements)):
            raise ValueError("Expected one to eight distinct prerequisite names")
        for value in requirements:
            if registration == "theme" and value in ("career.loaded", "regulations.active"):
                raise ValueError("A restart-only palette cannot wait for career or regulation activation")
            if value in ("career.loaded", "regulations.active"):
                continue
            if value.startswith("mod:") and re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", value[4:]):
                continue
            if (value.startswith("module:") and len(value[7:]) <= 128
                    and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value[7:], re.ASCII)):
                continue
            raise ValueError("Unknown prerequisite: " + value[:160])
        unique.update(requirements)
        result.append((registration, tuple(sorted(requirements))))
    if len(unique) > 8:
        raise ValueError("A mod may name at most eight different prerequisites")
    return tuple(sorted(result))
