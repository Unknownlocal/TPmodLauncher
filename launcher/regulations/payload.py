# SPDX-License-Identifier: MPL-2.0
"""Measure the fixed minimum invocation once, then account for owner metadata."""

from mod_runtime._regulations.values import bounded, measure


def minimum(key, option):
    """Measure a current-option envelope with empty context and owner metadata.

    This is only a static lower bound. Dispatch must still check real context,
    revision/season values and any upcoming policy against the full limits.
    """
    return measure({"context": {}, "regulation": {
        "schema": 1, "key": key, "revision": "x", "evaluation_season": 0,
        "selected": "current", "upcoming": None, "current": {
            "key": option["key"], "effective_season": 0,
            "parameters": option.get("parameters", {}), "option_data": {}}}}, limited=True)


def check_metadata(base, metadata):
    """Replace minimum()'s empty option_data object using exact measurements."""
    if not metadata:
        return  # The cached base already includes and checks an empty object.
    extra = measure(metadata, limited=True)
    # Metrics hold bytes, nodes, depth and integer-conversion work. Replace the
    # empty object (two bytes, one node) three levels below root; work adds up.
    bounded((base[0] - 2 + extra[0], base[1] - 1 + extra[1],
             max(base[2], 3 + extra[2]), base[3] + extra[3]))
