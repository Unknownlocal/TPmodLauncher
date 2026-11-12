# SPDX-License-Identifier: MPL-2.0
"""Bound the three fixed UI registries at both sides of the bridge."""

import json


def validate_ui_snapshot(snapshot):
    """Validate envelope sizes; each native adapter validates its own declarations."""
    if type(snapshot) is not dict or not snapshot or snapshot.keys() - {"menu", "settings", "themes"}:
        raise ValueError("Expected a bounded UI snapshot")
    for area, value in snapshot.items():
        if area == "settings":
            valid = type(value) is dict and set(value) == {"records", "result"} and type(value["records"]) is list
            limit = 65_536
        else:
            valid, limit = type(value) is list, 16_384
        if not valid or len(json.dumps(value, allow_nan=False).encode("utf-8")) > limit:
            raise ValueError("Expected bounded " + area + " declarations")
    return snapshot
